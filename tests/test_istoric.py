"""Teste: filtru pe localitate, istoric persistent, backfill tăcut, retry WAF.

Folosește texte sintetice din tests/fixtures/istoric/ (AAAA-LL-ZZ.txt, data = luni); fiecare „PDF” este textul codat UTF-8.
Săptămânile fără fișier simulează blocarea WAF (captcha/Blocked).
"""

from __future__ import annotations

import copy
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import callback
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.deo_intreruperi import history as h
from custom_components.deo_intreruperi import parser as p
from custom_components.deo_intreruperi.api import DeoBlockedError, DeoNotPdfError
from custom_components.deo_intreruperi.const import DOMAIN, EVENT_MATCH

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

FIX = Path(__file__).resolve().parent / "fixtures" / "istoric"
BASE = "https://www.distributieoltenia.ro/x/arges.html"
S_HIST = "sensor.intreruperi_distributie_oltenia_istoric_intreruperi"
S_LAST = "sensor.intreruperi_distributie_oltenia_ultima_intrerupere"
B_DET = "binary_sensor.intreruperi_distributie_oltenia_intrerupere_detectata"
FIRST = date(2026, 3, 30)


def _week(start: date) -> p.WeekLink:
    return p.WeekLink(start, start + timedelta(days=6), f"https://x/{start.isoformat()}.pdf")


ALL_WEEKS = [_week(FIRST + timedelta(weeks=i)) for i in range(31)]  # 30.03 .. 26.10


def _matches_for(start: date, locality: str | None = "curtea de arges") -> list[p.Match]:
    week = _week(start)
    text = (FIX / f"{start.isoformat()}.txt").read_text(encoding="utf-8")
    rows = p.parse_rows(text, week.start, week.end)
    return p.find_matches(rows, week, ["cuza voda"], [], locality)


# --------------------------------------------------------------------------
# Filtru pe localitate (3 cazuri sintetice)
# --------------------------------------------------------------------------


def test_locality_filter_cases():
    pit1 = _matches_for(date(2026, 6, 22))
    pit2 = _matches_for(date(2026, 8, 31))
    cda = _matches_for(date(2026, 9, 21))
    assert len(pit1) == len(pit2) == len(cda) == 1
    assert (pit1[0].date, pit1[0].locality, pit1[0].relevant) == (date(2026, 6, 22), "Pitești", False)
    assert (pit2[0].date, pit2[0].locality, pit2[0].relevant) == (date(2026, 9, 2), "Pitești", False)
    assert (cda[0].date, cda[0].locality, cda[0].relevant) == (date(2026, 9, 24), "Curtea de Argeș", True)
    assert cda[0].interval == "09:00 - 17:00" and not cda[0].locality_unknown
    assert not pit1[0].locality_unknown and not pit2[0].locality_unknown
    # fără filtru (locality=None) toate sunt relevante, ca înainte
    assert all(m.relevant for m in _matches_for(date(2026, 6, 22), None))


def _row(locality: str, zone: str, lines: list[str]) -> p.Row:
    return p.Row(date(2026, 10, 27), "09:00 - 17:00", locality, zone, lines)


def test_unknown_locality_is_relevant_but_flagged():
    week = _week(date(2026, 10, 26))
    unknown = _row("", "", ["Str. Cuza-Voda nr. 99"])
    other = _row("Pitești", "Strada Cuza Voda", ["Pitești Strada Cuza Voda"])
    m = p.find_matches([unknown, other], week, ["cuza voda"], [], "curtea de arges")
    assert [(x.relevant, x.locality_unknown) for x in m] == [(True, True), (False, False)]
    assert m[0].as_dict()["localitate_necunoscuta"] is True and m[1].as_dict()["relevant"] is False


def test_pta_match_always_relevant():
    week = _week(date(2026, 10, 26))
    row = _row("Pitești", "PTA Cuza Voda", ["Pitești PTA Cuza Voda"])
    m = p.find_matches([row], week, ["cuza voda"], ["pta cuza voda"], "curtea de arges")
    by_kind = {x.kind: x.relevant for x in m}
    assert by_kind == {"cuvant": False, "pta": True}


def test_filter_new_skips_irrelevant():
    ms = _matches_for(date(2026, 8, 31))  # Pitești, 02.09
    assert p.filter_new(ms, set(), date(2026, 9, 1)) == []


# --------------------------------------------------------------------------
# Istoric (logică pură)
# --------------------------------------------------------------------------


def test_history_merge_limit_and_first_detection():
    ms = _matches_for(date(2026, 9, 21)) + _matches_for(date(2026, 6, 22))
    hist, added = h.merge_history([], ms, "2026-10-22T10:00:00+03:00")
    assert added == 2 and [r["data"] for r in hist] == ["2026-09-24", "2026-06-22"]
    hist2, added2 = h.merge_history(hist, ms, "2026-11-01T10:00:00+02:00")
    assert added2 == 0 and all(r["detectat"] == "2026-10-22T10:00:00+03:00" for r in hist2)
    assert h.count_relevant(hist) == 1
    assert [r["data"] for r in h.view(hist, True)] == ["2026-09-24"]
    assert [r["data"] for r in h.view(hist, False)] == ["2026-09-24", "2026-06-22"]
    assert h.last_relevant(hist, date(2026, 10, 22))["data"] == "2026-09-24"
    assert h.last_relevant(hist, date(2026, 9, 1)) is None
    # limită
    many = []
    base = _matches_for(date(2026, 9, 21))[0]
    for i in range(130):
        m = copy.copy(base)
        m.date = date(2026, 1, 1) + timedelta(days=i)
        many.append(m)
    big, _ = h.merge_history([], many, "x", limit=100)
    assert len(big) == 100 and big[0]["data"] == "2026-05-10"


# --------------------------------------------------------------------------
# Integrare: backfill tăcut, Store, retry WAF
# --------------------------------------------------------------------------


class FakeClient:
    """Servește fixturile sintetice; săptămânile fără fișier sau din `blocked` dau WAF."""

    blocked: set[date] = set()
    not_pdf: set[date] = set()
    flaky: dict[date, int] = {}  # săptămână -> de câte ori eșuează înainte să meargă
    calls: list[date] = []

    def __init__(self, session, url):
        pass

    async def fetch_week_links(self):
        return sorted(ALL_WEEKS, key=lambda w: w.start, reverse=True)

    async def fetch_pdf(self, link):
        FakeClient.calls.append(link.start)
        left = FakeClient.flaky.get(link.start, 0)
        if left:
            FakeClient.flaky[link.start] = left - 1
            raise DeoBlockedError("captcha intermitent")
        if link.start in FakeClient.not_pdf:
            raise DeoNotPdfError("nu e PDF")
        f = FIX / f"{link.start.isoformat()}.txt"
        if link.start in FakeClient.blocked or not f.exists():
            raise DeoBlockedError("Blocked")
        return f.read_bytes()


def _fake_extract(data: bytes) -> str:
    return data.decode("utf-8")


@pytest.fixture(autouse=True)
def _reset():
    FakeClient.blocked, FakeClient.not_pdf, FakeClient.flaky, FakeClient.calls = set(), set(), {}, []
    pauses: list[tuple] = []

    async def fake_pause(bounds):
        pauses.append(bounds)

    with patch("custom_components.deo_intreruperi.coordinator._pause", fake_pause):
        yield pauses


@pytest.fixture
def pauses(_reset):
    return _reset


async def _setup(hass, extra_options=None, extract=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"url": BASE, "keywords": "cuza voda", "locality": "curtea de arges", "pta": ""},
        options=extra_options or {},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.deo_intreruperi.coordinator.DeoClient", FakeClient), patch(
        "custom_components.deo_intreruperi.coordinator.extract_pdf_text", extract or _fake_extract
    ), patch("custom_components.deo_intreruperi.coordinator.async_get_clientsession"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _refresh(hass, entry):
    with patch("custom_components.deo_intreruperi.coordinator.extract_pdf_text", _fake_extract):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done(wait_background_tasks=True)


def _dump(hist):
    return [(r["data"], r["interval_orar"], r["localitate"], r["relevant"]) for r in hist]


@pytest.mark.freeze_time("2026-10-22 10:00:00")
async def test_backfill_silent(hass, hass_storage, pauses):
    events = []
    hass.bus.async_listen(EVENT_MATCH, callback(lambda e: events.append(e.data)))
    entry = await _setup(hass)
    c = entry.runtime_data

    # niciun eveniment/alertă, binary_sensor stins (24.09 e în trecut, celelalte în Pitești)
    assert events == []
    assert hass.states.get(B_DET).state == "off"

    hist = c.history
    print("\nISTORIC (backfill offline):")
    for r in hist:
        print(" ", r["data"], r["interval_orar"], "|", r["localitate"], "| relevant:", r["relevant"], "|", r["zona"][:70])
    assert _dump(hist) == [
        ("2026-09-24", "09:00 - 17:00", "Curtea de Argeș", True),
        ("2026-09-02", "09:00 - 17:00", "Pitești", False),
        ("2026-06-22", "09:00 - 17:00", "Pitești", False),
    ]
    assert all(r["detectat"].startswith("2026-10-22") for r in hist)

    # entități noi (entity_id-urile existente rămân neschimbate)
    st = hass.states.get(S_HIST)
    assert st.state == "1"
    assert [r["data"] for r in st.attributes["istoric"]] == ["2026-09-24"]
    assert [r["data"] for r in st.attributes["istoric_toate"]] == ["2026-09-24", "2026-09-02", "2026-06-22"]
    assert st.attributes["istoric_toate"][1]["relevant"] is False
    last = hass.states.get(S_LAST)
    assert last.attributes["interval_orar"] == "09:00 - 17:00"
    assert last.attributes["localitate"] == "Curtea de Argeș"
    assert "PTA Cuza Voda" in last.attributes["zona"]
    assert "24.09.2026" in last.attributes["saptamana"] or "21.09.2026" in last.attributes["saptamana"]
    loc = dt_util.as_local(dt_util.parse_datetime(last.state))
    assert (loc.month, loc.day, loc.hour, loc.minute) == (9, 24, 9, 0)
    for eid in (
        "sensor.intreruperi_distributie_oltenia_potriviri",
        "sensor.intreruperi_distributie_oltenia_urmatoarea_intrerupere",
        "sensor.intreruperi_distributie_oltenia_ultima_saptamana_procesata",
        "sensor.intreruperi_distributie_oltenia_randuri_localitate",
    ):
        assert hass.states.get(eid) is not None

    # Store: istoric + săptămâni procesate (doar cele cu PDF); un PDF pe rând, cu pauze
    saved = [v for k, v in hass_storage.items() if "istoric" in k][0]["data"]
    assert len(saved["istoric"]) == 3
    processed = set(saved["procesate"])
    assert "2026-09-21" in processed and "2026-04-06" not in processed
    assert (1.0, 2.0) in pauses  # pauză între cereri

    # a doua rulare: incremental, nu mai descarcă ce e procesat, tot fără evenimente
    FakeClient.calls.clear()
    await _refresh(hass, entry)
    assert events == []
    assert date(2026, 9, 21) not in FakeClient.calls
    assert _dump(entry.runtime_data.history) == _dump(hist)


@pytest.mark.freeze_time("2026-10-22 10:00:00")
async def test_failed_week_retried_on_next_run(hass, hass_storage):
    events = []
    hass.bus.async_listen(EVENT_MATCH, callback(lambda e: events.append(e.data)))
    FakeClient.blocked = {date(2026, 9, 21)}
    entry = await _setup(hass)
    assert [r["data"] for r in entry.runtime_data.history] == ["2026-09-02", "2026-06-22"]
    assert hass.states.get(S_HIST).state == "0"
    assert FakeClient.calls.count(date(2026, 9, 21)) == 3  # 3 încercări
    assert "2026-09-21" not in [k for v in hass_storage.values() for k in v["data"].get("procesate", {})]

    FakeClient.blocked = set()
    await _refresh(hass, entry)
    assert [r["data"] for r in entry.runtime_data.history][0] == "2026-09-24"
    assert hass.states.get(S_HIST).state == "1"
    assert events == []  # tăcut și la reluare


@pytest.mark.freeze_time("2026-10-22 10:00:00")
async def test_waf_retry_then_success(hass, pauses):
    FakeClient.flaky = {date(2026, 9, 21): 2}  # eșuează de 2 ori, a 3-a merge
    FakeClient.not_pdf = set()
    entry = await _setup(hass)
    assert FakeClient.calls.count(date(2026, 9, 21)) == 3
    assert (2.0, 4.0) in pauses
    assert "2026-09-24" in [r["data"] for r in entry.runtime_data.history]


@pytest.mark.freeze_time("2026-10-22 10:00:00")
async def test_non_pdf_response_is_retried(hass):
    FakeClient.not_pdf = {date(2026, 6, 22)}
    entry = await _setup(hass)
    assert FakeClient.calls.count(date(2026, 6, 22)) == 3
    assert "2026-06-22" not in [r["data"] for r in entry.runtime_data.history]
    assert "2026-06-22" not in entry.runtime_data._processed


@pytest.mark.freeze_time("2026-10-22 10:00:00")
async def test_upcoming_other_locality_no_alert(hass):
    """Cuvânt-cheie în Pitești săptămâna viitoare: în istoric cu relevant=false, fără eveniment/binary_sensor."""
    events = []
    hass.bus.async_listen(EVENT_MATCH, callback(lambda e: events.append(e.data)))
    real = _fake_extract
    target = (FIX / "2026-10-26.txt").read_bytes()
    inject = {
        "pit": "27.10\n09:00 - 17:00 Pitești Strazile Smardan, Cuza Voda alimentate din PTCZ 4 SUD\n",
        "cda": "28.10\n09:00 - 17:00 Curtea de Argeș Strada Cuza Voda nr. 99\n",
    }

    def fake_extract(data):
        text = real(data)
        end = p._END_RE.search(text)
        if data == target and end:
            return text[: end.start()] + inject["pit"] + inject["cda"] + text[end.start():]
        return text

    entry = await _setup(hass, extract=fake_extract)
    recs = {r["data"]: r for r in entry.runtime_data.history if r["data"] >= "2026-10-26"}
    assert recs["2026-10-27"]["relevant"] is False and recs["2026-10-27"]["localitate"] == "Pitești"
    assert recs["2026-10-28"]["relevant"] is True
    # un singur eveniment: doar Curtea de Argeș (28.10); Pitești nu alertează
    assert [e["data"] for e in events] == ["2026-10-28"]
    assert events[0]["relevant"] is True
    assert hass.states.get(B_DET).state == "on"
    assert hass.states.get("sensor.intreruperi_distributie_oltenia_potriviri").state == "1"


# --------------------------------------------------------------------------
# Cardul markdown (card-istoric.yaml) randat cu motorul de șabloane HA
# --------------------------------------------------------------------------


def _card_content() -> str:
    import yaml

    card = yaml.safe_load((Path(__file__).resolve().parent.parent / "card-istoric.yaml").read_text("utf-8"))
    assert card["type"] == "markdown"
    return card["content"]


@pytest.mark.freeze_time("2026-10-22 10:00:00")
async def test_card_renders_history(hass):
    import os

    from homeassistant.helpers.template import Template

    await _setup(hass)
    out = Template(_card_content(), hass).async_render()
    if os.environ.get("DEO_CARD_OUT"):
        Path(os.environ["DEO_CARD_OUT"]).write_text(out, encoding="utf-8")
    assert "Ultima întrerupere:** 24.09.2026, 09:00 - 17:00, Curtea de Argeș" in out
    assert "| 24.09.2026 | 09:00 - 17:00 | Curtea de Argeș | PTA Cuza Voda |" in out
    assert "Pitești" not in out  # `istoric` = doar relevante


@pytest.mark.freeze_time("2026-10-22 10:00:00")
async def test_card_empty_message(hass):
    from homeassistant.helpers.template import Template

    out = Template(_card_content(), hass).async_render()  # senzorii nu există
    assert out.strip() == "Nicio întrerupere programată în istoric (încă)."
