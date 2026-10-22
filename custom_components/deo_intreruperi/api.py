"""Client HTTP pentru site-ul Distribuție Oltenia (pagina-listă și PDF-uri)."""

from __future__ import annotations

import logging

import aiohttp

from .const import BROWSER_HEADERS, PDF_HEADERS, REQUEST_TIMEOUT
from .parser import WeekLink, looks_blocked, parse_week_links

_LOGGER = logging.getLogger(__name__)


class DeoError(Exception):
    """Eroare de bază."""


class DeoConnectError(DeoError):
    """Nu s-a putut contacta site-ul (rețea, timeout, HTTP != 200)."""


class DeoBlockedError(DeoError):
    """Site-ul a răspuns cu pagina „Blocked” / 403 (WAF)."""


class DeoNotPdfError(DeoConnectError):
    """Răspunsul nu este PDF (de obicei pagină captcha/WAF intermitentă)."""


class DeoNoLinksError(DeoError):
    """Pagina s-a descărcat, dar nu conține linkuri de săptămână."""


class DeoClient:
    """Descarcă pagina-listă și PDF-urile, cu anteturi de browser."""

    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self._session = session
        self._url = url

    async def _get(self, url: str, headers: dict[str, str]) -> tuple[bytes, aiohttp.ClientResponse]:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        try:
            async with self._session.get(
                url, headers=headers, timeout=timeout, allow_redirects=True
            ) as resp:
                body = await resp.read()
                redirected = bool(resp.history)
                _LOGGER.debug(
                    "GET %s -> HTTP %s (redirectări: %s, %d octeți)",
                    _short(url), resp.status, len(resp.history), len(body),
                )
                head = body[:4000].decode("utf-8", errors="ignore")
                if looks_blocked(resp.status, head, redirected, str(resp.url)):
                    raise DeoBlockedError(
                        f"Site-ul a blocat cererea (HTTP {resp.status}"
                        f"{', redirectat' if redirected else ''}): probabil WAF"
                    )
                if resp.status != 200:
                    raise DeoConnectError(f"HTTP {resp.status} la {_short(url)}")
                return body, resp
        except DeoError:
            raise
        except TimeoutError as err:
            raise DeoConnectError(f"Timeout la {_short(url)}") from err
        except aiohttp.ClientError as err:
            raise DeoConnectError(f"Eroare de rețea la {_short(url)}: {err}") from err

    async def fetch_week_links(self) -> list[WeekLink]:
        """Pagina-listă -> linkurile de săptămână (cele mai noi primele)."""
        body, resp = await self._get(self._url, BROWSER_HEADERS)
        text = body.decode(resp.get_encoding() or "utf-8", errors="replace")
        links = parse_week_links(text, str(resp.url))
        if not links:
            raise DeoNoLinksError("Pagina nu conține linkuri de săptămână (format schimbat?)")
        _LOGGER.debug("Găsite %d linkuri de săptămână; cel mai nou: %s", len(links), links[0].period)
        return links

    async def fetch_pdf(self, link: WeekLink) -> bytes:
        """Descarcă PDF-ul unei săptămâni."""
        body, _ = await self._get(link.url, {**PDF_HEADERS, "Referer": self._url})
        if not body.startswith(b"%PDF"):
            raise DeoNotPdfError(f"Răspunsul pentru {link.period} nu este un PDF")
        return body


def _short(url: str) -> str:
    """URL fără query (conține hash) pentru log."""
    return url.split("?", 1)[0]
