"""Senzori: potriviri, ultima săptămână, rânduri localitate, următoarea întrerupere."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import DeoConfigEntry
from .coordinator import DeoCoordinator
from .entity import DeoEntity
from .history import ATTR_LIMIT, count_relevant, last_relevant, view
from .parser import _TIME_RE


async def async_setup_entry(
    hass: HomeAssistant, entry: DeoConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities(
        [DeoMatches(c), DeoLastWeek(c), DeoLocalityRows(c), DeoNext(c), DeoLastOutage(c), DeoHistory(c)]
    )


class DeoMatches(DeoEntity, SensorEntity):
    _attr_icon = "mdi:magnify"
    _attr_native_unit_of_measurement = "potriviri"

    def __init__(self, c: DeoCoordinator) -> None:
        super().__init__(c, "potriviri", "Potriviri")

    @property
    def native_value(self) -> int | None:
        d = self.coordinator.data
        return len(d.upcoming) if d else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        if not d:
            return {}
        return {"potriviri": [m.as_dict() for m in d.upcoming[:20]]}


class DeoLastWeek(DeoEntity, SensorEntity):
    _attr_icon = "mdi:calendar-week"

    def __init__(self, c: DeoCoordinator) -> None:
        super().__init__(c, "ultima_saptamana", "Ultima săptămână procesată")

    @property
    def native_value(self) -> str | None:
        d = self.coordinator.data
        w = d.last_week if d else None
        return w.period if w else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        if not d:
            return {}
        return {
            "data_verificarii": d.checked_at.isoformat() if d.checked_at else None,
            "saptamani": [
                {"perioada": w.link.period, "ok": w.ok, "randuri": w.rows, "eroare": w.error}
                for w in d.weeks
            ],
            "erori": sum(1 for w in d.weeks if not w.ok),
        }


class DeoLocalityRows(DeoEntity, SensorEntity):
    _attr_icon = "mdi:map-marker-alert"
    _attr_native_unit_of_measurement = "rânduri"

    def __init__(self, c: DeoCoordinator) -> None:
        super().__init__(c, "randuri_localitate", "Rânduri localitate")

    @property
    def native_value(self) -> int | None:
        d = self.coordinator.data
        return d.locality_rows if d else None


class DeoNext(DeoEntity, SensorEntity):
    _attr_icon = "mdi:clock-alert-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, c: DeoCoordinator) -> None:
        super().__init__(c, "urmatoarea_intrerupere", "Următoarea întrerupere")

    def _next(self):
        d = self.coordinator.data
        if not d:
            return None
        for m in d.upcoming:
            if m.start_datetime():
                return m
        return None

    @property
    def native_value(self) -> datetime | None:
        m = self._next()
        if not m:
            return None
        return dt_util.as_local(m.start_datetime().replace(tzinfo=dt_util.get_default_time_zone()))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        m = self._next()
        return m.as_dict() if m else {}


class _DeoHistoryBase(DeoEntity, SensorEntity):
    """Senzori din istoricul persistent: disponibili și când ultima descărcare a eșuat."""

    @property
    def available(self) -> bool:
        return self.coordinator.history_loaded


class DeoLastOutage(_DeoHistoryBase):
    _attr_icon = "mdi:transmission-tower-off"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, c: DeoCoordinator) -> None:
        super().__init__(c, "ultima_intrerupere", "Ultima întrerupere")

    def _rec(self) -> dict[str, Any] | None:
        return last_relevant(self.coordinator.history, dt_util.now().date())

    @property
    def native_value(self) -> datetime | None:
        r = self._rec()
        if not r:
            return None
        tm = _TIME_RE.search(r.get("interval_orar", ""))
        hh, mm = (int(x) for x in tm.group(1).split(":")) if tm else (0, 0)
        d = datetime.fromisoformat(r["data"]).date()
        return datetime.combine(d, time(hh, mm), tzinfo=dt_util.get_default_time_zone())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        r = self._rec()
        if not r:
            return {}
        return {
            k: r.get(k)
            for k in ("data", "interval_orar", "localitate", "zona", "saptamana", "localitate_necunoscuta")
        }


class DeoHistory(_DeoHistoryBase):
    _attr_icon = "mdi:history"
    _attr_native_unit_of_measurement = "întreruperi"
    # listele mari nu se scriu în baza de date a recorder-ului
    _unrecorded_attributes = frozenset({"istoric", "istoric_toate"})

    def __init__(self, c: DeoCoordinator) -> None:
        super().__init__(c, "istoric_intreruperi", "Istoric întreruperi")

    @property
    def native_value(self) -> int:
        return count_relevant(self.coordinator.history)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        h = self.coordinator.history
        return {
            "istoric": view(h, True, ATTR_LIMIT),
            "istoric_toate": view(h, False, ATTR_LIMIT),
            "total_inregistrari": len(h),
            "backfill_ramas": self.coordinator.backfill_pending,
        }
