"""Einrichtung ohne Eingaben, Optionen für Scan und Abfragen."""

from __future__ import annotations

import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import (CONF_AUTO_PROVISION, CONF_CONCURRENCY, CONF_FIXED_RANGE, CONF_FULL_INTERVAL, CONF_MDNS, CONF_SCAN_RANGES,
                    CONF_WIFI_PASSWORD, CONF_WIFI_SSID, DEFAULT_CONCURRENCY,
                    DEFAULT_FULL_INTERVAL, DEFAULT_MDNS, DEFAULT_SCAN_RANGES, DOMAIN)


def parse_ranges(text: str) -> list[str]:
    """Kommagetrennte Netze prüfen; wirft ValueError mit dem fehlerhaften Eintrag."""
    out = []
    for part in (p.strip() for p in (text or "").replace(";", ",").split(",")):
        if not part:
            continue
        try:
            out.append(str(ipaddress.ip_network(part, strict=False)))
        except ValueError as err:
            raise ValueError(part) from err
    return out


class ShellyManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Shelly Manager", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ShellyManagerOptionsFlow()


class ShellyManagerOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"range": ""}
        if user_input is not None:
            try:
                parse_ranges(user_input.get(CONF_SCAN_RANGES, ""))
                parse_ranges(user_input.get(CONF_FIXED_RANGE, ""))
            except ValueError as err:
                errors[CONF_SCAN_RANGES] = "invalid_range"
                placeholders["range"] = str(err)
            else:
                return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(CONF_SCAN_RANGES, default=opts.get(CONF_SCAN_RANGES, DEFAULT_SCAN_RANGES)): str,
            vol.Optional(CONF_MDNS, default=opts.get(CONF_MDNS, DEFAULT_MDNS)): bool,
            vol.Optional(CONF_CONCURRENCY, default=opts.get(CONF_CONCURRENCY, DEFAULT_CONCURRENCY)):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
            vol.Optional(CONF_FULL_INTERVAL, default=opts.get(CONF_FULL_INTERVAL, DEFAULT_FULL_INTERVAL)):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=168)),
            vol.Optional(CONF_WIFI_SSID, default=opts.get(CONF_WIFI_SSID, "")): str,
            vol.Optional(CONF_WIFI_PASSWORD, default=opts.get(CONF_WIFI_PASSWORD, "")):
                TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Optional(CONF_FIXED_RANGE, default=opts.get(CONF_FIXED_RANGE, "")): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors,
                                    description_placeholders=placeholders)
