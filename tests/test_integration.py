"""Test de integrare cu Home Assistant (pytest-homeassistant-custom-component), offline.

Client HTTP și extragerea PDF sunt înlocuite cu fixturi din tests/fixtures.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import callback
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.deo_intreruperi import parser as p
from custom_components.deo_intreruperi.api import DeoBlockedError
from custom_components.deo_intreruperi.const import DOMAIN, EVENT_MATCH

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FIX = Path(__file__).resolve().parent / "fixtures"
BASE = "https://www.distributieoltenia.ro/x/arges.html"
TEXTS = {
    "26_10_2026": (FIX / "saptamana_26_10_2026.txt").read_text(encoding="utf-8").replace(
        "Lucrările pot fi reprogramate",
        "27.10\n09:00 - 17:00 Curtea de Arges Str. Cuza-Voda nr. 99\nLucrările pot fi reprogramate",
    ),
    "19_10_2026": (FIX / "saptamana_19_10_2026.txt").read_text(encoding="utf-8"),
}


class FakeClient:
    blocked = False

    def __init__(self, session, url):
        pass

    async def fetch_week_links(self):
        if FakeClient.blocked:
            raise DeoBlockedError("test WAF")
        return p.parse_week_links((FIX / "lista_pagina.html").read_text(encoding="utf-8"), BASE)

    async def fetch_pdf(self, link):
        return b"%PDF-fake " + link.start.isoformat().encode()


def _fake_extract(data: bytes) -> str:
    return TEXTS["26_10_2026" if b"2026-10-26" in data else "19_10_2026"]


async def _setup(hass, options=None):
    FakeClient.blocked = False
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"url": BASE, "keywords": "cuza voda", "locality": "curtea de arges", "pta": ""},
        options=options or {},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.deo_intreruperi.coordinator.DeoClient", FakeClient), patch(
        "custom_components.deo_intreruperi.coordinator.extract_pdf_text", _fake_extract
    ), patch("custom_components.deo_intreruperi.coordinator.async_get_clientsession"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.freeze_time("2026-10-22 10:00:00")
async def test_full_cycle(hass, hass_storage):
    events = []
    hass.bus.async_listen(EVENT_MATCH, callback(lambda e: events.append(e.data)))
    entry = await _setup(hass)

    st = hass.states.get("binary_sensor.intreruperi_distributie_oltenia_intrerupere_detectata")
    assert st and st.state == "on"
    assert hass.states.get("sensor.intreruperi_distributie_oltenia_potriviri").state == "1"
    assert hass.states.get("sensor.intreruperi_distributie_oltenia_ultima_saptamana_procesata").state == (
        "26.10.2026 - 01.11.2026"
    )
    assert int(hass.states.get("sensor.intreruperi_distributie_oltenia_randuri_localitate").state) >= 5
    nxt = hass.states.get("sensor.intreruperi_distributie_oltenia_urmatoarea_intrerupere")
    loc = dt_util.as_local(dt_util.parse_datetime(nxt.state))
    assert (loc.month, loc.day, loc.hour, loc.minute) == (10, 27, 9, 0)
    assert len(events) == 1 and events[0]["data"] == "2026-10-27" and "Cuza" in events[0]["zona"]

    # a doua rulare: fără eveniment nou; datele notificate sunt persistate
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(events) == 1
    saved = [v for k, v in hass_storage.items() if "notificate" in k]
    assert saved and len(saved[0]["data"]["notificate"]) == 1

    # după „repornire” (reload), setul persistat împiedică re-notificarea
    with patch("custom_components.deo_intreruperi.coordinator.DeoClient", FakeClient), patch(
        "custom_components.deo_intreruperi.coordinator.extract_pdf_text", _fake_extract
    ), patch("custom_components.deo_intreruperi.coordinator.async_get_clientsession"):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert len(events) == 1


@pytest.mark.freeze_time("2026-10-22 10:00:00")
async def test_blocked_makes_unavailable(hass):
    entry = await _setup(hass)
    FakeClient.blocked = True
    with patch("custom_components.deo_intreruperi.coordinator.DeoClient", FakeClient):
        entry.runtime_data._client = FakeClient(None, "")
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.intreruperi_distributie_oltenia_ultima_saptamana_procesata").state == "unavailable"
    assert hass.states.get("binary_sensor.intreruperi_distributie_oltenia_intrerupere_detectata").state == "unavailable"


@pytest.mark.freeze_time("2026-10-22 10:00:00")
async def test_no_match_keyword(hass):
    events = []
    hass.bus.async_listen(EVENT_MATCH, callback(lambda e: events.append(e.data)))
    TEXTS_BACKUP = dict(TEXTS)
    TEXTS["26_10_2026"] = (FIX / "saptamana_26_10_2026.txt").read_text(encoding="utf-8")
    try:
        await _setup(hass)
    finally:
        TEXTS.update(TEXTS_BACKUP)
    assert hass.states.get("binary_sensor.intreruperi_distributie_oltenia_intrerupere_detectata").state == "off"
    assert hass.states.get("sensor.intreruperi_distributie_oltenia_potriviri").state == "0"
    assert events == []
