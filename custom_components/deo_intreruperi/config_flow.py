"""Config flow și options flow."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DeoBlockedError, DeoClient, DeoConnectError, DeoNoLinksError
from .const import (
    CONF_KEYWORDS,
    CONF_LOCALITY,
    CONF_PTA,
    CONF_URL,
    DEFAULT_KEYWORDS,
    DEFAULT_LOCALITY,
    DEFAULT_PTA,
    DEFAULT_URL,
    DOMAIN,
    NAME,
)

_LOGGER = logging.getLogger(__name__)


def _schema(d: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=d.get(CONF_URL, DEFAULT_URL)): str,
            vol.Optional(CONF_KEYWORDS, default=d.get(CONF_KEYWORDS, DEFAULT_KEYWORDS)): str,
            vol.Optional(CONF_LOCALITY, default=d.get(CONF_LOCALITY, DEFAULT_LOCALITY)): str,
            vol.Optional(CONF_PTA, default=d.get(CONF_PTA, DEFAULT_PTA)): str,
        }
    )


async def _validate(hass, url: str) -> str | None:
    """Cod de eroare sau None dacă pagina se descarcă și are linkuri."""
    client = DeoClient(async_get_clientsession(hass), url)
    try:
        await client.fetch_week_links()
    except DeoBlockedError as err:
        _LOGGER.error("Validare: pagina blocată de site (WAF): %s", err)
        return "blocked"
    except DeoNoLinksError as err:
        _LOGGER.error("Validare: %s", err)
        return "no_links"
    except DeoConnectError as err:
        _LOGGER.error("Validare: nu pot descărca pagina: %s", err)
        return "cannot_connect"
    return None


class DeoConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip()
            await self.async_set_unique_id(url.lower().rstrip("/"))
            self._abort_if_unique_id_configured()
            err = await _validate(self.hass, url)
            if err is None:
                return self.async_create_entry(title=NAME, data={**user_input, CONF_URL: url})
            errors["base"] = err
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DeoOptionsFlow()


class DeoOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            url = user_input[CONF_URL].strip()
            err = None if url == current.get(CONF_URL) else await _validate(self.hass, url)
            if err is None:
                return self.async_create_entry(data={**user_input, CONF_URL: url})
            errors["base"] = err
            current = {**current, **user_input}
        return self.async_show_form(step_id="init", data_schema=_schema(current), errors=errors)
