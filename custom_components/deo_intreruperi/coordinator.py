"""Coordinator: descarcă pagina, PDF-urile, caută potriviri, emite evenimente."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    DeoBlockedError,
    DeoClient,
    DeoConnectError,
    DeoError,
    DeoNoLinksError,
    DeoNotPdfError,
)
from .const import (
    BACKFILL_MAX_FAILED_STREAK,
    BACKFILL_PAUSE,
    BACKFILL_WEEKS,
    PDF_ATTEMPTS,
    RETRY_PAUSE,
    STORAGE_KEY_HISTORY_PREFIX,
    CONF_KEYWORDS,
    CONF_LOCALITY,
    CONF_PTA,
    CONF_URL,
    DEFAULT_KEYWORDS,
    DEFAULT_LOCALITY,
    DEFAULT_PTA,
    DEFAULT_URL,
    DOMAIN,
    EVENT_MATCH,
    MAX_WEEKS,
    STORAGE_KEY_PREFIX,
    STORAGE_PRUNE_DAYS,
    STORAGE_VERSION,
    UPDATE_INTERVAL,
)
from .history import merge_history, prune_processed
from .parser import (
    Match,
    Row,
    WeekLink,
    count_locality_rows,
    extract_pdf_text,
    filter_new,
    find_matches,
    parse_rows,
    select_weeks,
    sha256_hex,
    split_list,
)

_LOGGER = logging.getLogger(__name__)


async def _pause(bounds: tuple[float, float]) -> None:
    """Pauză aleatoare (secunde); separată ca testele să o poată înlocui."""
    await asyncio.sleep(random.uniform(*bounds))


@dataclass
class WeekResult:
    link: WeekLink
    ok: bool
    error: str | None = None
    rows: int = 0


@dataclass
class DeoData:
    matches: list[Match] = field(default_factory=list)
    locality_rows: int = 0
    weeks: list[WeekResult] = field(default_factory=list)
    checked_at: datetime | None = None
    today: date | None = None

    @property
    def upcoming(self) -> list[Match]:
        """Potriviri RELEVANTE de azi/viitor (cele din alte localități sunt excluse)."""
        return sorted(
            (m for m in self.matches if m.relevant and self.today and m.date >= self.today),
            key=lambda m: (m.date, m.interval),
        )

    @property
    def last_week(self) -> WeekLink | None:
        ok = [w.link for w in self.weeks if w.ok]
        return max(ok, key=lambda l: l.start) if ok else None


class DeoCoordinator(DataUpdateCoordinator[DeoData]):
    """Actualizare la 6 ore."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=UPDATE_INTERVAL, config_entry=entry
        )
        conf = {**entry.data, **entry.options}
        self._client = DeoClient(async_get_clientsession(hass), conf.get(CONF_URL, DEFAULT_URL))
        self._keywords = split_list(conf.get(CONF_KEYWORDS, DEFAULT_KEYWORDS))
        self._ptas = split_list(conf.get(CONF_PTA, DEFAULT_PTA))
        self._locality = conf.get(CONF_LOCALITY, DEFAULT_LOCALITY)
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry.entry_id}")
        self._notified: dict[str, str] = {}  # cheie -> început săptămână (ISO)
        self._loaded = False
        # istoric persistent + săptămâni procesate (început săptămână ISO -> sha256)
        self._hstore: Store = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_HISTORY_PREFIX}.{entry.entry_id}"
        )
        self.history: list[dict[str, Any]] = []
        self._processed: dict[str, str] = {}
        self._backfill_task: asyncio.Task | None = None
        self.backfill_pending = 0
        # cache pe URL: (sha256, rânduri)
        self._cache: dict[str, tuple[str, list[Row]]] = {}
        self._digests: dict[str, str] = {}

    async def _load_store(self) -> None:
        if self._loaded:
            return
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._notified = dict(stored.get("notificate", {}))
        hstored = await self._hstore.async_load()
        if isinstance(hstored, dict):
            self.history = list(hstored.get("istoric", []))
            self._processed = dict(hstored.get("procesate", {}))
        self._loaded = True

    @property
    def history_loaded(self) -> bool:
        return self._loaded

    async def _save_history(self) -> None:
        await self._hstore.async_save({"istoric": self.history, "procesate": self._processed})

    async def _async_update_data(self) -> DeoData:
        await self._load_store()
        today = dt_util.now().date()
        _LOGGER.debug("Pas 1: descarc pagina-listă")
        try:
            links = await self._client.fetch_week_links()
        except DeoBlockedError as err:
            _LOGGER.error("Pagina-listă blocată de site (WAF): %s", err)
            raise UpdateFailed(f"Blocat de site (WAF): {err}") from err
        except DeoNoLinksError as err:
            _LOGGER.error("Nu s-au găsit linkuri pe pagină: %s", err)
            raise UpdateFailed(str(err)) from err
        except DeoConnectError as err:
            _LOGGER.error("Nu pot descărca pagina-listă: %s", err)
            raise UpdateFailed(str(err)) from err

        weeks = select_weeks(links, today, MAX_WEEKS)
        _LOGGER.debug("Pas 2: săptămâni relevante: %s", [w.period for w in weeks])

        data = DeoData(checked_at=dt_util.now(), today=today)
        first_error: Exception | None = None
        for week in weeks:
            try:
                rows = await self._rows_for_week(week)
            except DeoError as err:
                _LOGGER.error("Săptămâna %s: %s", week.period, err)
                first_error = first_error or err
                data.weeks.append(WeekResult(week, False, str(err)))
                continue
            except Exception as err:  # noqa: BLE001 - PDF corupt etc.
                _LOGGER.exception("Săptămâna %s: eroare la procesare", week.period)
                first_error = first_error or err
                data.weeks.append(WeekResult(week, False, f"procesare: {err}"))
                continue
            data.weeks.append(WeekResult(week, True, None, len(rows)))
            data.matches += find_matches(rows, week, self._keywords, self._ptas, self._locality)
            data.locality_rows += count_locality_rows(rows, self._locality)
            self._processed[week.start.isoformat()] = self._digests.get(week.url, "")

        if not any(w.ok for w in data.weeks):
            raise UpdateFailed(f"Nicio săptămână procesată: {first_error}")

        _LOGGER.info(
            "Verificare încheiată: %d săptămâni ok din %d, %d potriviri, %d rânduri localitate",
            sum(w.ok for w in data.weeks), len(data.weeks), len(data.matches), data.locality_rows,
        )
        await self._emit_new(data, today)
        self.history, _ = merge_history(
            self.history, data.matches, dt_util.now().isoformat(timespec="seconds")
        )
        self._processed = prune_processed(self._processed, today)
        await self._save_history()
        self._schedule_backfill(links, today)
        return data

    # ------------------------------------------------------------------
    # Descărcare cu reîncercări (WAF intermitent)
    # ------------------------------------------------------------------

    async def _fetch_pdf_retry(self, week: WeekLink) -> bytes:
        """PDF cu max `PDF_ATTEMPTS` încercări la blocare WAF sau răspuns non-PDF."""
        for attempt in range(1, PDF_ATTEMPTS + 1):
            try:
                return await self._client.fetch_pdf(week)
            except (DeoBlockedError, DeoNotPdfError) as err:
                if attempt == PDF_ATTEMPTS:
                    raise
                _LOGGER.warning(
                    "PDF %s: %s (încercarea %d/%d), reîncerc", week.period, err, attempt, PDF_ATTEMPTS
                )
                await _pause(RETRY_PAUSE)
        raise AssertionError("inaccesibil")  # pragma: no cover

    async def _parse_pdf(self, pdf: bytes, week: WeekLink) -> list[Row]:
        def _work() -> list[Row]:
            text = extract_pdf_text(pdf)
            if not text.strip():
                raise DeoError("PDF fără text (scanat/imagine?)")
            rows = parse_rows(text, week.start, week.end)
            if not rows:
                raise DeoError("Nu am găsit rânduri în tabel (format schimbat?)")
            return rows

        return await self.hass.async_add_executor_job(_work)

    # ------------------------------------------------------------------
    # Backfill tăcut (fără evenimente) al săptămânilor trecute
    # ------------------------------------------------------------------

    def _backfill_candidates(self, links: list[WeekLink], today: date) -> list[WeekLink]:
        past = sorted((l for l in links if l.end < today), key=lambda l: l.start, reverse=True)
        return [l for l in past[:BACKFILL_WEEKS] if l.start.isoformat() not in self._processed]

    def _schedule_backfill(self, links: list[WeekLink], today: date) -> None:
        todo = self._backfill_candidates(links, today)
        self.backfill_pending = len(todo)
        if not todo or (self._backfill_task and not self._backfill_task.done()):
            return
        _LOGGER.info("Backfill istoric: %d săptămâni de procesat în fundal", len(todo))
        self._backfill_task = self.config_entry.async_create_background_task(
            self.hass, self._run_backfill(todo), f"{DOMAIN}_backfill"
        )

    async def _run_backfill(self, weeks: list[WeekLink]) -> None:
        """Un PDF pe rând, pauză 1-2 s între cereri; fără evenimente/alerte."""
        failed_streak = 0
        first = True
        try:
            for week in weeks:
                if not first:
                    await _pause(BACKFILL_PAUSE)
                first = False
                try:
                    pdf = await self._fetch_pdf_retry(week)
                    rows = await self._parse_pdf(pdf, week)
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001 - rămâne neprocesată, se reia la rularea următoare
                    failed_streak += 1
                    _LOGGER.warning("Backfill %s eșuat (se reia la rularea următoare): %s", week.period, err)
                    if failed_streak >= BACKFILL_MAX_FAILED_STREAK:
                        _LOGGER.warning("Backfill oprit după %d eșecuri la rând", failed_streak)
                        break
                    continue
                failed_streak = 0
                matches = find_matches(rows, week, self._keywords, self._ptas, self._locality)
                self.history, _ = merge_history(
                    self.history, matches, dt_util.now().isoformat(timespec="seconds")
                )
                self._processed[week.start.isoformat()] = sha256_hex(pdf)
                self.backfill_pending = max(0, self.backfill_pending - 1)
                await self._save_history()
                self.async_update_listeners()
                _LOGGER.debug("Backfill %s: %d rânduri, %d potriviri", week.period, len(rows), len(matches))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Backfill: eroare neașteptată")
        finally:
            self.async_update_listeners()

    async def _rows_for_week(self, week: WeekLink) -> list[Row]:
        pdf = await self._fetch_pdf_retry(week)
        digest = sha256_hex(pdf)
        self._digests[week.url] = digest
        cached = self._cache.get(week.url)
        if cached and cached[0] == digest:
            _LOGGER.debug("PDF %s neschimbat (hash), folosesc cache", week.period)
            return cached[1]

        rows = await self._parse_pdf(pdf, week)
        _LOGGER.debug("PDF %s: %d rânduri", week.period, len(rows))
        self._cache[week.url] = (digest, rows)
        return rows

    async def _emit_new(self, data: DeoData, today: date) -> None:
        new = filter_new(data.matches, set(self._notified), today)
        for m in new:
            self._notified[m.key] = m.week_start.isoformat()
            payload = {**m.as_dict(), "entry_id": self.config_entry.entry_id}
            _LOGGER.info("Potrivire nouă: %s %s (%s)", m.date, m.interval, m.keyword)
            self.hass.bus.async_fire(EVENT_MATCH, payload)
        # curăță cheile vechi
        limit = (today - timedelta(days=STORAGE_PRUNE_DAYS)).isoformat()
        self._notified = {k: v for k, v in self._notified.items() if v >= limit}
        if new:
            await self._store.async_save({"notificate": self._notified})
