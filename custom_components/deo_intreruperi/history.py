"""Istoricul întreruperilor (logică pură, fără Home Assistant)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .parser import Match

HISTORY_LIMIT = 100
ATTR_LIMIT = 20
ATTR_ZONE_CHARS = 200


def record_from_match(m: Match, detected: str) -> dict[str, Any]:
    """Înregistrare persistentă (JSON) pentru o potrivire."""
    return {
        "cheie": m.key,
        "saptamana": m.week,
        "data": m.date.isoformat(),
        "interval_orar": m.interval,
        "localitate": m.locality,
        "zona": m.zone[:500],
        "cuvant": m.keyword,
        "tip": m.kind,
        "relevant": m.relevant,
        "localitate_necunoscuta": m.locality_unknown,
        "detectat": detected,
    }


def _sort_key(r: dict[str, Any]) -> tuple[str, str]:
    return (r.get("data", ""), r.get("interval_orar", ""))


def merge_history(
    history: list[dict[str, Any]],
    matches: list[Match],
    detected: str,
    limit: int = HISTORY_LIMIT,
) -> tuple[list[dict[str, Any]], int]:
    """Adaugă/actualizează potrivirile; păstrează ultimele `limit`. Întoarce (listă, nr. noi).

    La o înregistrare existentă se actualizează câmpurile, dar „detectat” rămâne
    data primei detecții.
    """
    by_key = {r["cheie"]: r for r in history if "cheie" in r}
    added = 0
    for m in matches:
        rec = record_from_match(m, detected)
        old = by_key.get(rec["cheie"])
        if old is None:
            added += 1
        else:
            rec["detectat"] = old.get("detectat", detected)
        by_key[rec["cheie"]] = rec
    merged = sorted(by_key.values(), key=_sort_key, reverse=True)[:limit]
    return merged, added


def view(history: list[dict[str, Any]], only_relevant: bool, n: int = ATTR_LIMIT) -> list[dict[str, Any]]:
    """Ultimele `n` (cele mai noi primele) pentru atributele senzorilor; zona scurtată."""
    out: list[dict[str, Any]] = []
    for r in sorted(history, key=_sort_key, reverse=True):
        if only_relevant and not r.get("relevant", True):
            continue
        item = {k: v for k, v in r.items() if k != "cheie"}
        item["zona"] = (item.get("zona") or "")[:ATTR_ZONE_CHARS]
        out.append(item)
        if len(out) >= n:
            break
    return out


def count_relevant(history: list[dict[str, Any]]) -> int:
    return sum(1 for r in history if r.get("relevant", True))


def last_relevant(history: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    """Cea mai recentă întrerupere relevantă cu data <= azi."""
    cands = [r for r in history if r.get("relevant", True) and r.get("data", "") <= today.isoformat()]
    return max(cands, key=_sort_key) if cands else None


def prune_processed(processed: dict[str, str], today: date, days: int = 400) -> dict[str, str]:
    limit = (today - timedelta(days=days)).isoformat()
    return {k: v for k, v in processed.items() if k >= limit}
