"""Senzor binar: întrerupere detectată."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DeoConfigEntry
from .entity import DeoEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DeoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([DeoDetected(entry.runtime_data)])


class DeoDetected(DeoEntity, BinarySensorEntity):
    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "intrerupere_detectata", "Întrerupere detectată")
        self._attr_icon = "mdi:transmission-tower-off"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.upcoming)
