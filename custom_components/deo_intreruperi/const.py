"""Constante pentru integrarea Întreruperi Distribuție Oltenia."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "deo_intreruperi"
NAME = "Întreruperi Distribuție Oltenia"

CONF_URL = "url"
CONF_KEYWORDS = "keywords"
CONF_LOCALITY = "locality"
CONF_PTA = "pta"

DEFAULT_URL = (
    "https://www.distributieoltenia.ro/ro/suport/"
    "lucrari-pentru-prevenire-si-corectare-avarii/arges.html"
)
DEFAULT_KEYWORDS = "cuza voda"
DEFAULT_LOCALITY = "curtea de arges"
DEFAULT_PTA = ""

UPDATE_INTERVAL = timedelta(hours=6)
MAX_WEEKS = 3
REQUEST_TIMEOUT = 30

EVENT_MATCH = "deo_intreruperi_potrivire"

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}.notificate"
# Cheile notificate mai vechi de atâtea zile (după începutul săptămânii) se șterg.
STORAGE_PRUNE_DAYS = 90

STORAGE_KEY_HISTORY_PREFIX = f"{DOMAIN}.istoric"
BACKFILL_WEEKS = 26
BACKFILL_PAUSE = (1.0, 2.0)  # secunde între cereri de PDF în backfill
RETRY_PAUSE = (2.0, 4.0)  # secunde între reîncercări la blocare WAF
PDF_ATTEMPTS = 3
BACKFILL_MAX_FAILED_STREAK = 3  # oprește backfill-ul după atâtea săptămâni eșuate la rând

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}
PDF_HEADERS = {
    **BROWSER_HEADERS,
    "Accept": "application/pdf,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}
