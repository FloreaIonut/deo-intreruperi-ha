"""Test HTTP al clientului cu server local. Script separat (nu pytest):

    python -I tests/test_api_http.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))  # sys.path explicit (rulat cu -I)

from aiohttp import ClientSession, web  # noqa: E402

from custom_components.deo_intreruperi.api import (  # noqa: E402
    DeoBlockedError, DeoClient, DeoConnectError, DeoNoLinksError,
)


async def _run():
    page = (FIX / "lista_pagina.html").read_text(encoding="utf-8")
    blocked = (FIX / "pagina_blocked.html").read_text(encoding="utf-8")

    async def ok(_): return web.Response(text=page, content_type="text/html")
    async def blk(_): return web.Response(text=blocked, content_type="text/html")
    async def redir(_): raise web.HTTPFound("/blk")
    async def f403(_): return web.Response(status=403, text="no")
    async def f500(_): return web.Response(status=500, text="no")
    async def empty(_): return web.Response(text="<html>nimic</html>", content_type="text/html")
    async def pdf(_): return web.Response(body=b"%PDF-1.4 x", content_type="application/pdf")
    async def notpdf(_): return web.Response(body=b"<html>", content_type="application/pdf")

    app = web.Application()
    for path, h in (("/ok", ok), ("/blk", blk), ("/redir", redir), ("/403", f403),
                    ("/500", f500), ("/empty", empty), ("/pdf", pdf), ("/notpdf", notpdf)):
        app.router.add_get(path, h)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    async with ClientSession() as s:
        links = await DeoClient(s, base + "/ok").fetch_week_links()
        assert links[0].period == "26.10.2026 - 01.11.2026"
        for path, exc in (("/blk", DeoBlockedError), ("/redir", DeoBlockedError), ("/403", DeoBlockedError),
                          ("/500", DeoConnectError), ("/empty", DeoNoLinksError)):
            try:
                await DeoClient(s, base + path).fetch_week_links()
            except exc as e:
                print("OK  ", path, type(e).__name__, e)
            else:
                raise AssertionError(path)
        link = links[0].__class__(links[0].start, links[0].end, base + "/pdf")
        assert (await DeoClient(s, base).fetch_pdf(link)).startswith(b"%PDF")
        try:
            await DeoClient(s, base).fetch_pdf(link.__class__(link.start, link.end, base + "/notpdf"))
        except DeoConnectError as e:
            print("OK   notpdf", e)
        else:
            raise AssertionError("notpdf")
        try:
            await DeoClient(s, "http://127.0.0.1:1/x").fetch_week_links()
        except DeoConnectError as e:
            print("OK   conn refused", e)
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(_run())
    print("TOATE OK")
