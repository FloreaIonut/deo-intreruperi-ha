"""Parsare pură (fără Home Assistant): linkuri, text PDF, potriviri, deduplicare.

Modulul nu importă nimic din Home Assistant ca să poată fi testat offline.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time
from urllib.parse import parse_qs, unquote, urljoin, urlparse

# --------------------------------------------------------------------------
# Normalizare
# --------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Fără diacritice, litere mici, orice non-alfanumeric devine spațiu, spații colapsate."""
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("ß", "ss")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def split_list(value: str | list[str] | None) -> list[str]:
    """Listă separată prin virgulă (sau ;) -> elemente normalizate, unice, nevide."""
    if value is None:
        return []
    items = value if isinstance(value, list) else re.split(r"[,;\n]", value)
    out: list[str] = []
    for item in items:
        n = normalize(item)
        if n and n not in out:
            out.append(n)
    return out


def clean_display(text: str) -> str:
    """Curăță text pentru afișare (spații, dubluri tip „ArgeșȘ”)."""
    text = re.sub(r"([șş])[ȘŞ]", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,;")


# --------------------------------------------------------------------------
# Detectare blocare WAF
# --------------------------------------------------------------------------

_BLOCK_MARKERS = ("<title>blocked</title>", "fingerprint_risk", "bad_client")


def looks_blocked(status: int, body_head: str = "", redirected: bool = False, final_url: str = "") -> bool:
    """True dacă răspunsul pare pagina „Blocked” a WAF-ului."""
    if status in (401, 403, 429):
        return True
    low = (body_head or "").lower()
    if any(m in low for m in _BLOCK_MARKERS):
        return True
    return bool(redirected and "block" in (final_url or "").lower())


# --------------------------------------------------------------------------
# Linkuri de săptămână din pagina-listă
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WeekLink:
    start: date
    end: date
    url: str
    label: str = ""

    @property
    def period(self) -> str:
        return f"{self.start:%d.%m.%Y} - {self.end:%d.%m.%Y}"


_A_RE = re.compile(r"<a\b[^>]*?href\s*=\s*([\"'])(.*?)\1[^>]*>(.*?)</a>", re.I | re.S)
# fileName=... saptamana 26_10_2026 - 01_11_2026   |   27_12 - 02_01_2022
_FILE_RANGE_RE = re.compile(
    r"(\d{1,2})_(\d{1,2})(?:_(\d{4}))?\s*-\s*(\d{1,2})_(\d{1,2})_(\d{4})"
)
# text: „Intreruperi 26.10 - 01.11.2026”
_TEXT_RANGE_RE = re.compile(
    r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{4})"
)


def _range_from_match(m: re.Match[str]) -> tuple[date, date] | None:
    d1, m1, y1, d2, m2, y2 = m.groups()
    try:
        end = date(int(y2), int(m2), int(d2))
        if y1:
            start = date(int(y1), int(m1), int(d1))
        else:
            start = date(end.year, int(m1), int(d1))
            if start > end:  # trecere de an (27.12 - 02.01.2022)
                start = date(end.year - 1, int(m1), int(d1))
    except ValueError:
        return None
    if start > end:
        return None
    return start, end


def parse_week_links(page_html: str, base_url: str) -> list[WeekLink]:
    """Extrage linkurile PDF de săptămână, cele mai noi primele, fără dubluri.

    Intervalul se ia din `fileName` din URL; dacă nu merge, din textul linkului.
    Textul linkului poate fi rupt în mai multe <a> (ex. „A” + „rges - ...”), de
    aceea numele fișierului e sursa principală.
    """
    found: dict[str, WeekLink] = {}
    for m in _A_RE.finditer(page_html):
        href = html_lib.unescape(m.group(2)).strip()
        label = clean_display(html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(3))))
        low = href.lower()
        if "connector.php" not in low and not low.endswith(".pdf"):
            continue
        url = urljoin(base_url, href)
        qs = parse_qs(urlparse(url).query)
        fname = unquote(qs.get("fileName", [""])[0]) or unquote(urlparse(url).path)
        rng = None
        fm = _FILE_RANGE_RE.search(fname)
        if fm:
            rng = _range_from_match(fm)
        if rng is None:
            tm = _TEXT_RANGE_RE.search(label)
            if tm:
                rng = _range_from_match(tm)
        if rng is None or url in found:
            continue
        found[url] = WeekLink(rng[0], rng[1], url, label)
    return sorted(found.values(), key=lambda w: (w.start, w.url), reverse=True)


def select_weeks(links: list[WeekLink], today: date, max_weeks: int = 3) -> list[WeekLink]:
    """Săptămâna care conține azi + toate cele viitoare (max `max_weeks`, cele mai noi).

    Dacă nu e nimic relevant, ia cea mai nouă săptămână (doar informativ).
    """
    rel = [w for w in links if w.end >= today]
    rel.sort(key=lambda w: w.start, reverse=True)
    rel = rel[:max_weeks]
    if not rel and links:
        rel = [max(links, key=lambda w: w.start)]
    return sorted(rel, key=lambda w: w.start)


# --------------------------------------------------------------------------
# Extragere text PDF
# --------------------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_pdf_text(data: bytes) -> str:
    """Text din PDF cu pypdf (apel blocant: rulează în executor)."""
    from pypdf import PdfReader  # import târziu: HA instalează cerința la nevoie

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


# --------------------------------------------------------------------------
# Parsare tabel
# --------------------------------------------------------------------------


@dataclass
class Row:
    date: date
    interval: str
    locality: str
    zone: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return clean_display(" ".join(self.lines))

    @property
    def norm(self) -> str:
        return normalize(" ".join(self.lines))


_TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{2})(?:\.(\d{4}))?\b")
_SKIP_RE = re.compile(r"^(pagina\s+\d+\s*/\s*\d+|\d{2}-\d{2}-\d{2}_P\d+.*)$", re.I)
_END_RE = re.compile(r"(lucr\w+\s+pot\s+fi\s+reprogramate|distribu\w+\s+oltenia\s+avertizeaz)", re.I)
_HEADER_RE = re.compile(r"^(data|interval orar|de [iî]ntrerupere|localitatea|zona de [iî]ntrerupere)", re.I)
_ZONE_MARK_RE = re.compile(
    r"\b(PTAb|PTA|PTCZ|PTM|PT|Str[aăâ]d[aăâ]|Str[aăâ]zile|Str|Sat|Satul|Satele|B-dul|Bd|Calea|Aleea"
    r"|[IÎ]ntrarea|Drumul|[SȘŞ]oseaua|Pia[tțţ]a|Splaiul|Bulevardul)\b"
)


def _resolve_date(day: int, month: int, week_start: date, week_end: date) -> date | None:
    for year in (week_start.year, week_end.year):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if week_start <= d <= week_end:
            return d
    try:
        return date(week_start.year, month, day)
    except ValueError:
        return None


def _split_locality_zone(joined: str) -> tuple[str, str]:
    joined = clean_display(joined)
    m = _ZONE_MARK_RE.search(joined)
    if not m:
        return joined, ""
    return joined[: m.start()].strip(" ,;"), joined[m.start() :].strip()


def parse_rows(text: str, week_start: date, week_end: date) -> list[Row]:
    """Împarte textul PDF în rânduri (blocuri) Data / Interval / Localitate / Zonă.

    Un rând începe la o linie cu interval orar; liniile următoare (fără interval,
    dată sau antet de pagină) îi aparțin (localitate ruptă, liste de PTA).
    """
    rows: list[Row] = []
    current_date: date | None = None
    cur: Row | None = None
    in_table = False

    def close() -> None:
        nonlocal cur
        if cur is not None:
            cur.locality, cur.zone = _split_locality_zone(" ".join(cur.lines))
            rows.append(cur)
        cur = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _END_RE.search(line):
            close()
            break
        if _SKIP_RE.match(line):
            continue
        if _HEADER_RE.match(line) and cur is None:
            continue
        dm = _DATE_RE.match(line)
        if dm:
            rest = line[dm.end() :].strip()
            if not rest or _TIME_RE.match(rest):
                d = _resolve_date(int(dm.group(1)), int(dm.group(2)), week_start, week_end)
                if d is not None:
                    close()
                    current_date = d
                    in_table = True
                    line = rest
                    if not line:
                        continue
        tm = _TIME_RE.match(line)
        if not in_table:
            continue
        if tm is not None:
            close()
            cur = Row(current_date, f"{tm.group(1)} - {tm.group(2)}", "", "", [])  # type: ignore[arg-type]
            rest = line[tm.end() :].strip()
            if rest:
                cur.lines.append(rest)
        elif cur is not None:
            cur.lines.append(line)
    close()
    return rows


# --------------------------------------------------------------------------
# Potriviri
# --------------------------------------------------------------------------


@dataclass
class Match:
    week: str  # perioada, ex. „26.10.2026 - 01.11.2026”
    week_start: date
    date: date
    interval: str
    locality: str
    zone: str
    text: str
    keyword: str
    kind: str  # „cuvant” | „pta”
    relevant: bool = True  # False = cuvânt-cheie într-o altă localitate
    locality_unknown: bool = False  # localitatea nu a putut fi determinată (tratat ca relevant)

    @property
    def key(self) -> str:
        zone_hash = hashlib.sha1(normalize(self.zone).encode()).hexdigest()[:12]
        return f"{self.week_start.isoformat()}|{self.date.isoformat()}|{zone_hash}|{normalize(self.keyword)}"

    def as_dict(self) -> dict:
        return {
            "saptamana": self.week,
            "data": self.date.isoformat(),
            "interval_orar": self.interval,
            "localitate": self.locality,
            "zona": self.zone,
            "text": self.text[:500],
            "cuvant": self.keyword,
            "tip": self.kind,
            "relevant": self.relevant,
            "localitate_necunoscuta": self.locality_unknown,
        }

    def start_datetime(self) -> datetime | None:
        m = _TIME_RE.search(self.interval)
        if not m:
            return None
        hh, mm = m.group(1).split(":")
        try:
            return datetime.combine(self.date, time(int(hh), int(mm)))
        except ValueError:
            return None


def classify_locality(row: Row, locality: str | None) -> tuple[bool, bool]:
    """(relevant, localitate_necunoscută) pentru o potrivire pe cuvânt-cheie.

    - `locality` gol/None: fără filtru => relevant.
    - localitatea urmărită apare în bloc => relevant.
    - localitatea rândului nu poate fi determinată (goală sau împărțire eșuată,
      fără zonă și text lung) => relevant, dar marcat „necunoscută”
      (mai bine fals pozitiv decât ratat).
    - altfel (ex. Pitești) => nerelevant.
    """
    watched = normalize(locality or "")
    if not watched or watched in row.norm:
        return True, False
    loc_norm = normalize(row.locality)
    if not loc_norm or (not row.zone and len(loc_norm.split()) > 6):
        return True, True
    return False, False


def find_matches(
    rows: list[Row],
    week: WeekLink,
    keywords: list[str],
    ptas: list[str],
    locality: str | None = None,
) -> list[Match]:
    """Potriviri de cuvinte-cheie / PTA (liste deja normalizate).

    Potrivirea pe PTA e mereu relevantă; cea pe cuvânt-cheie doar dacă rândul e
    în `locality` (vezi `classify_locality`). Cele nerelevante se returnează
    totuși, cu `relevant=False`, pentru istoric.
    """
    out: list[Match] = []
    for row in rows:
        row_norm = row.norm
        loc_relevant, loc_unknown = classify_locality(row, locality)
        for kind, terms in (("cuvant", keywords), ("pta", ptas)):
            for term in terms:
                if not term or term not in row_norm:
                    continue
                hit_lines = [clean_display(l) for l in row.lines if term in normalize(l)]
                zone = "; ".join(hit_lines) if hit_lines else (row.zone or row.text)
                out.append(
                    Match(
                        week=week.period,
                        week_start=week.start,
                        date=row.date,
                        interval=row.interval,
                        locality=row.locality,
                        zone=zone,
                        text=row.text,
                        keyword=term,
                        kind=kind,
                        relevant=True if kind == "pta" else loc_relevant,
                        locality_unknown=False if kind == "pta" else loc_unknown,
                    )
                )
    return out


def count_locality_rows(rows: list[Row], locality: str) -> int:
    """Numărul de rânduri (blocuri) care menționează localitatea (normalizată)."""
    loc = normalize(locality)
    if not loc:
        return 0
    return sum(1 for r in rows if loc in r.norm)


def filter_new(matches: list[Match], notified: set[str], today: date) -> list[Match]:
    """Potrivirile RELEVANTE de azi/viitor care nu au fost încă notificate (fără dubluri)."""
    seen: set[str] = set()
    out: list[Match] = []
    for m in matches:
        if not m.relevant or m.date < today or m.key in notified or m.key in seen:
            continue
        seen.add(m.key)
        out.append(m)
    return out
