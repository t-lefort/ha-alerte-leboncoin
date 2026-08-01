"""UI configuration: one config entry, one subentry per saved search."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import Blocked, InvalidSearchUrl, LeboncoinApi, clean_search_url, serialise
from .const import (
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
    MIN_POLL_SECONDS,
    SUBENTRY_TYPE,
)
from .filters import KeywordFilter

_LOGGER = logging.getLogger(__name__)

CONF_NAME = "name"


class LeboncoinConfigFlow(ConfigFlow, domain=DOMAIN):
    """The parent entry holds nothing — every search lives in a subentry."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Alertes Leboncoin", data={})

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TYPE: SearchSubentryFlowHandler}


def _notify_service_options(hass) -> list[dict[str, str]]:
    services = hass.services.async_services().get("notify", {})
    return sorted(
        ({"value": name, "label": f"notify.{name}"} for name in services),
        key=lambda option: option["label"],
    )


def _schema(hass, defaults: dict[str, Any]) -> vol.Schema:
    hours = [{"value": str(h), "label": f"{h:02d}h"} for h in range(24)]
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): TextSelector(),
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Optional(
                CONF_REQUIRE_KEYWORDS, default=defaults.get(CONF_REQUIRE_KEYWORDS, "")
            ): TextSelector(),
            vol.Optional(
                CONF_EXCLUDE_KEYWORDS, default=defaults.get(CONF_EXCLUDE_KEYWORDS, "")
            ): TextSelector(),
            vol.Optional(
                CONF_SEARCH_BODY, default=defaults.get(CONF_SEARCH_BODY, False)
            ): BooleanSelector(),
            vol.Required(
                CONF_NOTIFY_SERVICES, default=defaults.get(CONF_NOTIFY_SERVICES, [])
            ): SelectSelector(
                SelectSelectorConfig(
                    options=_notify_service_options(hass),
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                )
            ),
            vol.Optional(CONF_CRITICAL, default=defaults.get(CONF_CRITICAL, True)): BooleanSelector(),
            vol.Required(
                CONF_POLL_SECONDS, default=defaults.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_POLL_SECONDS, max=3600, step=5, mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(
                CONF_QUIET_START, default=str(defaults.get(CONF_QUIET_START, DEFAULT_QUIET_START))
            ): SelectSelector(SelectSelectorConfig(options=hours, mode=SelectSelectorMode.DROPDOWN)),
            vol.Required(
                CONF_QUIET_END, default=str(defaults.get(CONF_QUIET_END, DEFAULT_QUIET_END))
            ): SelectSelector(SelectSelectorConfig(options=hours, mode=SelectSelectorMode.DROPDOWN)),
            vol.Optional(
                CONF_MAX_ADS, default=defaults.get(CONF_MAX_ADS, DEFAULT_MAX_ADS)
            ): NumberSelector(
                NumberSelectorConfig(min=1, max=35, step=1, mode=NumberSelectorMode.BOX)
            ),
        }
    )


class SearchSubentryFlowHandler(ConfigSubentryFlow):
    """Add or edit one saved search."""

    async def _async_form(
        self, step_id: str, user_input: dict[str, Any] | None, defaults: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, str], dict[str, Any]]:
        """Validate input. Returns (cleaned_data, errors, description_placeholders)."""
        errors: dict[str, str] = {}
        placeholders: dict[str, Any] = {}

        if user_input is None:
            return None, errors, placeholders

        try:
            search_url = clean_search_url(user_input[CONF_URL])
        except InvalidSearchUrl as err:
            errors[CONF_URL] = str(err)
            return None, errors, placeholders

        # Run the search once before saving. Catching a typo or a filter that
        # matches nothing here beats discovering it through silence.
        api = LeboncoinApi(search_url)
        try:
            ads = await self.hass.async_add_executor_job(api.fetch_raw_ads)
        except Blocked:
            errors["base"] = "blocked"
            return None, errors, placeholders
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Search preview failed")
            errors["base"] = "unknown"
            return None, errors, placeholders

        serialised = [serialise(ad) for ad in ads]
        keyword_filter = KeywordFilter(
            user_input.get(CONF_REQUIRE_KEYWORDS),
            user_input.get(CONF_EXCLUDE_KEYWORDS),
            user_input.get(CONF_SEARCH_BODY, False),
        )
        kept, _dropped = keyword_filter.apply(serialised)
        placeholders = {
            "total": str(len(serialised)),
            "kept": str(len(kept)),
            "sample": ", ".join(ad["title"][:40] for ad in kept[:3]) or "—",
        }
        if serialised and not kept:
            errors["base"] = "filters_match_nothing"
            return None, errors, placeholders

        data = dict(user_input)
        data[CONF_URL] = search_url
        data[CONF_POLL_SECONDS] = int(data[CONF_POLL_SECONDS])
        data[CONF_QUIET_START] = int(data[CONF_QUIET_START])
        data[CONF_QUIET_END] = int(data[CONF_QUIET_END])
        data[CONF_MAX_ADS] = int(data.get(CONF_MAX_ADS, DEFAULT_MAX_ADS))
        return data, errors, placeholders

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        data, errors, placeholders = await self._async_form("user", user_input, {})
        if data is not None:
            return self.async_create_entry(title=data.pop(CONF_NAME), data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(self.hass, user_input or {}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        defaults = {**subentry.data, CONF_NAME: subentry.title}

        data, errors, placeholders = await self._async_form("reconfigure", user_input, defaults)
        if data is not None:
            return self.async_update_reload_and_abort(
                self._get_entry(),
                subentry,
                title=data.pop(CONF_NAME),
                data=data,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(self.hass, {**defaults, **(user_input or {})}),
            errors=errors,
            description_placeholders=placeholders,
        )
