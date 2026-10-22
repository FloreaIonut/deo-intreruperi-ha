"""Integrarea „Întreruperi Distribuție Oltenia”."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import DeoCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

type DeoConfigEntry = ConfigEntry[DeoCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DeoConfigEntry) -> bool:
    coordinator = DeoCoordinator(hass, entry)
    # Fără ConfigEntryNotReady: la blocare/eroare entitățile apar „unavailable”,
    # iar eroarea e în log; actualizarea se reia la 6 ore.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeoConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: DeoConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
