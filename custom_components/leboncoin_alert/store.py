"""Persistent record of ads already seen, so a restart never re-alerts."""

from __future__ import annotations

import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import SEEN_RETENTION_DAYS, STORAGE_KEY, STORAGE_VERSION


class SeenStore:
    """Ad ids per search, kept in .storage.

    Held in memory and flushed with a delay: a search polling every 90 seconds
    should not rewrite the file on every tick.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict](hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, dict[str, float]] = {}

    async def async_load(self) -> None:
        self._data = await self._store.async_load() or {}

    def _bucket(self, subentry_id: str) -> dict[str, float]:
        return self._data.setdefault(subentry_id, {})

    def is_bootstrapped(self, subentry_id: str) -> bool:
        return subentry_id in self._data

    def filter_new(self, subentry_id: str, ads: list[dict]) -> list[dict]:
        """Return the ads never recorded before, oldest first."""
        known = self._bucket(subentry_id)
        fresh = [ad for ad in ads if ad.get("id") is not None and str(ad["id"]) not in known]
        # The API returns newest first; alert in publication order.
        return list(reversed(fresh))

    async def async_record(self, subentry_id: str, ads: list[dict]) -> None:
        bucket = self._bucket(subentry_id)
        now = time.time()
        for ad in ads:
            if ad.get("id") is not None:
                bucket[str(ad["id"])] = now
        self._prune(bucket, now)
        self._store.async_delay_save(lambda: self._data, 30)

    async def async_forget(self, subentry_id: str) -> None:
        """Drop a removed search, so its ids stop taking up space."""
        if self._data.pop(subentry_id, None) is not None:
            await self._store.async_save(self._data)

    @staticmethod
    def _prune(bucket: dict[str, float], now: float) -> None:
        cutoff = now - SEEN_RETENTION_DAYS * 86400
        for ad_id in [k for k, seen in bucket.items() if seen < cutoff]:
            del bucket[ad_id]

    def known_searches(self) -> list[str]:
        return list(self._data)

    def count(self, subentry_id: str) -> int:
        return len(self._bucket(subentry_id))
