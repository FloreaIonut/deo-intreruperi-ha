"""Teste offline pentru parser.py (fără Home Assistant).

Rulare:  python -I tests/test_parser.py     sau     pytest tests/
PDF-urile reale (opțional) se caută în $DEO_PDF_DIR (w1.pdf = 26.10, w2.pdf = 19.10); altfel testul este sărit.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
_spec = importlib.util.spec_from_file_location(
    "deo_parser", HERE.parent / "custom_components" / "deo_intreruperi" / "parser.py"
)
p = importlib.util.module_from_spec(_spec)
sys.modules["deo_parser"] = p
_spec.loader.exec_module(p)

W26 = (date(2026, 10, 26), date(2026, 11, 1))
W19 = (date(2026, 10, 19), date(2026, 10, 25))


def _rows(name, week):
    return p.parse_rows((FIX / name).read_text(encoding="utf-8"), *week)


def _link(week):
    return p.WeekLink(week[0], week[1], "https://x/y.pdf")


def test_normalize():
    assert p.normalize("Curtea de \nArgeșȘ") == "curtea de argess"
    assert p.normalize("Str. Cuza-Vodă") == "str cuza voda"
    assert p.normalize("  ȚARĂ  ") == "tara"
    assert p.split_list("Cuza Vodă, , cuza-voda; PTA 1") == ["cuza voda", "pta 1"]


def test_curtea_de_arges_found_and_cuza_absent_26_10():
    rows = _rows("saptamana_26_10_2026.txt", W26)
    assert len(rows) > 20
    assert p.count_locality_rows(rows, "curtea de arges") > 0
    assert p.find_matches(rows, _link(W26), ["cuza voda"], []) == []
    assert all(W26[0] <= r.date <= W26[1] for r in rows)
    # PTA din săptămână, cu diacritice/rupturi
    m = p.find_matches(rows, _link(W26), [], ["ptcz ceramica", "pta um curtea de arges"])
    assert {x.keyword for x in m} == {"ptcz ceramica", "pta um curtea de arges"}
    assert all(x.date == date(2026, 10, 26) for x in m)


def test_curtea_de_arges_found_and_cuza_absent_19_10():
    rows = _rows("saptamana_19_10_2026.txt", W19)
    assert p.count_locality_rows(rows, "Curtea de Argeș") >= 4
    assert p.find_matches(rows, _link(W19), ["cuza voda"], []) == []
    # data pe aceeași linie cu intervalul (20.10) și localitate cu mai multe nume
    r = [x for x in rows if x.date == date(2026, 10, 20) and "curtea de arges" in x.norm]
    assert r and "Valea Iasului" in r[0].locality.replace("ș", "s")


def test_synthetic_cuza_voda_matches():
    text = (
        "LUCRĂRI PENTRU PREVENIRE ȘI CORECTARE AVARII – SĂPTĂMÂNA 26.10.2026 - 01.11.2026\n"
        "Data \nInterval orar \nde întrerupere Localitatea Zona de întrerupere \n"
        "27.10 \n"
        "09:00 - 17:00 Curtea de \nArgeșȘ \nStr. Cuza-Vodă nr. 1-45 \n"
        "Pagina 2/3 \n01-06-01_P01-F05_Program-de-întreruperi\n"
        "PTA Altceva \n"
        "10:00 - 12:00 Pitesti Strada Banani \n"
        "Lucrările pot fi reprogramate în situația în care\n"
        "09:00 - 10:00 Fals Str. Cuza Voda \n"
    )
    rows = p.parse_rows(text, *W26)
    assert len(rows) == 2  # după „Lucrările pot fi reprogramate” nu se mai citește
    ms = p.find_matches(rows, _link(W26), p.split_list("cuza voda"), [])
    assert len(ms) == 1
    m = ms[0]
    assert m.date == date(2026, 10, 27) and m.interval == "09:00 - 17:00"
    assert m.locality == "Curtea de Argeș"
    assert "Cuza-Vodă" in m.zone
    assert p.count_locality_rows(rows, "curtea de arges") == 1


def test_keyword_split_over_lines():
    text = "27.10\n09:00 - 17:00 Curtea de Arges Str. Cuza\nVoda\n"
    rows = p.parse_rows(text, *W26)
    assert len(p.find_matches(rows, _link(W26), ["cuza voda"], [])) == 1


def test_dedup_events():
    rows = p.parse_rows("27.10\n09:00 - 17:00 Curtea de Arges Str. Cuza-Voda\n", *W26)
    ms = p.find_matches(rows, _link(W26), ["cuza voda"], [])
    today = date(2026, 10, 22)
    notified: set[str] = set()
    new = p.filter_new(ms + ms, notified, today)  # dublură în aceeași rulare
    assert len(new) == 1
    notified |= {m.key for m in new}
    assert p.filter_new(ms, notified, today) == []  # a doua rulare / după repornire
    # potrivire istorică (săptămână trecută) nu se emite niciodată
    assert p.filter_new(ms, set(), date(2026, 11, 5)) == []
    # altă zonă / alt cuvânt = cheie nouă
    rows2 = p.parse_rows("27.10\n09:00 - 17:00 Curtea de Arges Str. Cuza-Voda PTA Nou\n", *W26)
    ms2 = p.find_matches(rows2, _link(W26), ["cuza voda"], [])
    assert p.filter_new(ms2, notified, today)[0].key not in notified


def test_links_and_week_selection():
    html = (FIX / "lista_pagina.html").read_text(encoding="utf-8")
    links = p.parse_week_links(html, "https://www.distributieoltenia.ro/x/arges.html")
    assert [l.start for l in links][:2] == [date(2026, 10, 26), date(2026, 10, 19)]
    assert links[0].end == date(2026, 11, 1)
    assert "&amp;" not in links[0].url and "hash=" in links[0].url
    assert links[-1].start == date(2021, 12, 27) and links[-1].end == date(2022, 1, 2)
    sel = p.select_weeks(links, date(2026, 10, 22), 3)
    assert [w.start for w in sel] == [date(2026, 10, 19), date(2026, 10, 26)]
    # azi după toate: ia cea mai nouă (informativ)
    assert len(p.select_weeks(links, date(2027, 1, 1), 3)) == 1


def test_blocked_detection():
    blocked = (FIX / "pagina_blocked.html").read_text(encoding="utf-8")
    assert p.looks_blocked(200, blocked)
    assert p.looks_blocked(403, "")
    assert p.looks_blocked(200, "", True, "https://x/blocked.html")
    assert not p.looks_blocked(200, "<html>ok</html>")
    assert p.parse_week_links(blocked, "https://x") == []


def test_real_pdfs_if_available():
    d = os.environ.get("DEO_PDF_DIR")
    if not d or not (Path(d) / "w1.pdf").exists():
        pytest.skip("DEO_PDF_DIR nesetat (PDF-uri reale lipsă)")
    for name, week in (("w1.pdf", W26), ("w2.pdf", W19)):
        text = p.extract_pdf_text((Path(d) / name).read_bytes())
        rows = p.parse_rows(text, *week)
        assert p.count_locality_rows(rows, "curtea de arges") > 0, name
        assert p.find_matches(rows, _link(week), ["cuza voda"], []) == [], name
        print(name, "rânduri:", len(rows), "Curtea de Argeș:", p.count_locality_rows(rows, "curtea de arges"))


if __name__ == "__main__":
    fails = 0
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            try:
                f()
                print("OK  ", n)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", n, repr(e))
    sys.exit(1 if fails else 0)
