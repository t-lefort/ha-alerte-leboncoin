"""One polling coordinator per saved search."""

from __future__ import annotations

import logging
import random
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Blocked, LeboncoinApi, clean_search_url
from .const import (
    BACKOFF_MAX,
    BACKOFF_START,
    CONF_CRITICAL,
    CONF_EXCLUDE_KEYWORDS,
    CONF_MAX_ADS,
    CONF_NOTIFY_SERVICES,
    CONF_POLL_SECONDS,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_REQUIRE_KEYWORDS,
    CONF_SEARCH_BODY,
    CONF_URL,
    DEFAULT_MAX_ADS,
    DEFAULT_POLL_SECONDS,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DOMAIN,
    EVENT_NEW_ADS,
    JITTER_RATIO,
    TRANSIENT_BLOCKS,
    TRANSIENT_PAUSE,
)
from .filters import KeywordFilter
from .store import SeenStore

_LOGGER = logging.getLogger(__name__)


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps past midnight


class SearchCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one search, alerts on genuinely new ads.

    The update interval is rewritten after every refresh rather than being
    fixed: it carries the jitter, the overnight pause and the backoff ladder.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        store: SeenStore,
    ) -> None:
        self.subentry_id = subentry.subentry_id
        self.search_name = subentry.title
        self._store = store
        self._config = dict(subentry.data)
        self._api = LeboncoinApi(clean_search_url(self._config[CONF_URL]))
        self._filter = KeywordFilter(
            self._config.get(CONF_REQUIRE_KEYWORDS),
            self._config.get(CONF_EXCLUDE_KEYWORDS),
            self._config.get(CONF_SEARCH_BODY, False),
        )
        self._consecutive_blocks = 0
        self._was_quiet = False
        self._bootstrapped = store.is_bootstrapped(self.subentry_id)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {subentry.title}",
            update_interval=timedelta(seconds=self._poll_seconds),
            config_entry=entry,
        )

    @property
    def _poll_seconds(self) -> int:
        return int(self._config.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS))

    def _next_interval(self) -> timedelta:
        jitter = self._poll_seconds * JITTER_RATIO
        return timedelta(seconds=random.uniform(self._poll_seconds - jitter, self._poll_seconds + jitter))

    def _seconds_until_quiet_end(self) -> float:
        now = dt_util.now()
        end = int(self._config.get(CONF_QUIET_END, DEFAULT_QUIET_END))
        target = now.replace(hour=end, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def _async_update_data(self) -> dict[str, Any]:
        previous = self.data or {"ads": [], "last_ad": None, "status": "starting"}

        quiet_start = int(self._config.get(CONF_QUIET_START, DEFAULT_QUIET_START))
        quiet_end = int(self._config.get(CONF_QUIET_END, DEFAULT_QUIET_END))
        if in_quiet_hours(dt_util.now().hour, quiet_start, quiet_end):
            # Going dark overnight is not only about your sleep: a search that
            # pauses when humans do looks a lot more like a human.
            nap = self._seconds_until_quiet_end()
            self.update_interval = timedelta(seconds=nap)
            self._was_quiet = True
            _LOGGER.debug("%s: quiet hours, sleeping %.1fh", self.search_name, nap / 3600)
            return {**previous, "status": "quiet"}

        try:
            ads = await self.hass.async_add_executor_job(self._api.fetch)
        except Blocked as err:
            self._consecutive_blocks += 1
            # A fresh session is what clears a challenge; the pause is what
            # keeps the retry from looking like hammering.
            await self.hass.async_add_executor_job(self._api.reset)
            if self._consecutive_blocks <= TRANSIENT_BLOCKS:
                self.update_interval = timedelta(seconds=TRANSIENT_PAUSE)
                _LOGGER.debug(
                    "%s: challenged (%d), new session shortly",
                    self.search_name,
                    self._consecutive_blocks,
                )
            else:
                step = self._consecutive_blocks - TRANSIENT_BLOCKS - 1
                pause = min(BACKOFF_MAX, BACKOFF_START * (2**step))
                self.update_interval = timedelta(seconds=pause)
                _LOGGER.warning(
                    "%s: blocked %d times in a row, backing off %ds",
                    self.search_name,
                    self._consecutive_blocks,
                    pause,
                )
            raise UpdateFailed(str(err)) from err

        self._consecutive_blocks = 0
        self.update_interval = self._next_interval()

        new_ads = self._store.filter_new(self.subentry_id, ads)

        if not self._bootstrapped:
            # First run for this search: everything already listed is old news.
            await self._store.async_record(self.subentry_id, ads)
            self._bootstrapped = True
            _LOGGER.info(
                "%s: bootstrapped with %d existing ad(s), no alert sent",
                self.search_name,
                len(ads),
            )
            return {
                "ads": [],
                "last_ad": None,
                "status": "ok",
                "listing_size": len(ads),
                "last_check": dt_util.utcnow().isoformat(),
            }

        kept: list[dict] = []
        if new_ads:
            kept, dropped = self._filter.apply(new_ads)
            for ad, reason in dropped:
                _LOGGER.debug("%s: skipping %s — %s (%s)", self.search_name, ad["id"], ad["title"], reason)
            # Everything new is recorded, filtered-out ads included, so they are
            # never re-examined.
            await self._store.async_record(self.subentry_id, new_ads)

        result = {
            "ads": kept,
            "last_ad": kept[0] if kept else previous.get("last_ad"),
            "status": "ok",
            "listing_size": len(ads),
            "last_check": dt_util.utcnow().isoformat(),
        }

        if kept:
            max_ads = int(self._config.get(CONF_MAX_ADS, DEFAULT_MAX_ADS))
            batch = kept[:max_ads]
            if len(kept) > len(batch):
                _LOGGER.warning(
                    "%s: %d new ads, alerting on the %d most recent only",
                    self.search_name,
                    len(kept),
                    len(batch),
                )
            kind = "catchup" if self._was_quiet else "live"
            for ad in batch:
                _LOGGER.info("%s: NEW [%s] %s — %s", self.search_name, kind, ad["title"], ad["url"])
            await self._async_dispatch(batch, kind)

        self._was_quiet = False
        return result

    async def _async_dispatch(self, ads: list[dict], kind: str) -> None:
        payload = {
            "search": self.search_name,
            "subentry_id": self.subentry_id,
            "kind": kind,  # "live" while watching, "catchup" after quiet hours
            "count": len(ads),
            "ads": ads,
            "top": ads[0],
        }
        # Fired whatever the notification settings are, so you can build your
        # own automations on top without giving up the built-in alerts.
        self.hass.bus.async_fire(EVENT_NEW_ADS, payload)

        services = self._config.get(CONF_NOTIFY_SERVICES) or []
        if not services:
            return

        top = ads[0]
        headline = top["title"] if len(ads) == 1 else f"{len(ads)} nouvelles annonces"
        where = f" — {top['city']}" if top.get("city") else ""
        message = f"{top['price_label']}{where}" if len(ads) == 1 else f"À partir de {top['price_label']}"

        data: dict[str, Any] = {"url": top["url"], "group": f"leboncoin_{self.subentry_id}"}
        if top.get("image"):
            data["image"] = top["image"]

        # Telegram has no notion of priority — its API only exposes
        # disable_notification — so cutting through a silent phone is the
        # companion app's critical push doing the work, not Telegram.
        if kind == "live" and self._config.get(CONF_CRITICAL, True):
            data["push"] = {
                "interruption-level": "critical",
                "sound": {"name": "default", "critical": 1, "volume": 0.8},
            }
        else:
            data["push"] = {"interruption-level": "active"}

        prefix = "🔔" if kind == "live" else "🌅"
        for service in services:
            try:
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": f"{prefix} {headline}",
                        "message": f"{message}\n{top['url']}",
                        "data": data,
                    },
                    blocking=False,
                )
            except Exception:  # noqa: BLE001 - one broken target must not sink the rest
                _LOGGER.exception("%s: notify.%s failed", self.search_name, service)
