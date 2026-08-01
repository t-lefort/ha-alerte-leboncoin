"""One sensor per saved search, holding the latest matching ad."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LeboncoinConfigEntry
from .const import DOMAIN, SUBENTRY_TYPE
from .coordinator import SearchCoordinator

# Home Assistant rejects states longer than this, and ad titles run long.
MAX_STATE_LENGTH = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LeboncoinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE:
            continue
        coordinator = entry.runtime_data.coordinators.get(subentry_id)
        if coordinator is None:
            continue
        async_add_entities(
            [LastAdSensor(coordinator, subentry)],
            config_subentry_id=subentry_id,
        )


class LastAdSensor(CoordinatorEntity[SearchCoordinator], SensorEntity):
    """Latest ad that passed the filters, with the details as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "last_ad"
    _attr_icon = "mdi:tag-search"

    def __init__(self, coordinator: SearchCoordinator, subentry: ConfigSubentry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{subentry.subentry_id}_last_ad"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="leboncoin",
            entry_type=None,
        )

    @property
    def available(self) -> bool:
        # A DataDome challenge is a routine hiccup, not a reason to grey the
        # entity out — the last known ad is still perfectly valid.
        return self.coordinator.data is not None

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data or {}
        last_ad = data.get("last_ad")
        if not last_ad:
            return "Aucune annonce"
        return (last_ad.get("title") or "Sans titre")[:MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        last_ad = data.get("last_ad") or {}
        return {
            "search": self.coordinator.search_name,
            "status": "blocked" if not self.coordinator.last_update_success else data.get("status"),
            "last_check": data.get("last_check"),
            "listing_size": data.get("listing_size"),
            "price": last_ad.get("price"),
            "price_label": last_ad.get("price_label"),
            "url": last_ad.get("url"),
            "image": last_ad.get("image"),
            "city": last_ad.get("city"),
            "published": last_ad.get("published"),
            # Ads from the most recent poll only; the event carries the rest.
            "new_ads": data.get("ads", []),
        }
