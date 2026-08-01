"""Alertes Leboncoin — watch saved searches and alert on new ads."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import SUBENTRY_TYPE
from .coordinator import SearchCoordinator
from .store import SeenStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


@dataclass
class RuntimeData:
    store: SeenStore
    coordinators: dict[str, SearchCoordinator] = field(default_factory=dict)


type LeboncoinConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: LeboncoinConfigEntry) -> bool:
    store = SeenStore(hass)
    await store.async_load()

    coordinators: dict[str, SearchCoordinator] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE:
            continue
        coordinators[subentry_id] = SearchCoordinator(hass, entry, subentry, store)

    # Searches deleted from the UI leave their ad ids behind otherwise.
    for orphan in [key for key in store.known_searches() if key not in entry.subentries]:
        _LOGGER.debug("Forgetting ads of removed search %s", orphan)
        await store.async_forget(orphan)

    entry.runtime_data = RuntimeData(store=store, coordinators=coordinators)

    # Deliberately not async_config_entry_first_refresh: an occasional DataDome
    # challenge is expected and must not prevent the integration from loading.
    for coordinator in coordinators.values():
        await coordinator.async_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LeboncoinConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: LeboncoinConfigEntry) -> None:
    """Rebuild coordinators after a search is added, edited or removed."""
    await hass.config_entries.async_reload(entry.entry_id)
