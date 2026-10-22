"""Test config flow: succes și erori distincte."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.deo_intreruperi.api import DeoBlockedError, DeoConnectError, DeoNoLinksError
from custom_components.deo_intreruperi.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
INPUT = {"url": "https://example.test/arges.html", "keywords": "cuza voda", "locality": "curtea de arges", "pta": ""}


async def _start(hass, side_effect):
    with patch("custom_components.deo_intreruperi.config_flow.async_get_clientsession"), patch(
        "custom_components.deo_intreruperi.config_flow.DeoClient.fetch_week_links",
        AsyncMock(side_effect=side_effect, return_value=[object()]),
    ), patch("custom_components.deo_intreruperi.async_setup_entry", return_value=True):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        return await hass.config_entries.flow.async_configure(r["flow_id"], INPUT)


@pytest.mark.parametrize(
    ("exc", "code"),
    [(DeoBlockedError("x"), "blocked"), (DeoConnectError("x"), "cannot_connect"), (DeoNoLinksError("x"), "no_links")],
)
async def test_errors(hass, exc, code):
    res = await _start(hass, exc)
    assert res["type"] == FlowResultType.FORM and res["errors"] == {"base": code}


async def test_success(hass):
    res = await _start(hass, None)
    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert res["data"]["keywords"] == "cuza voda"
