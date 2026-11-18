"""Shelly Manager: Shelly-Geräte katalogisieren, vergleichen und verwalten – mit eigenem Panel."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from homeassistant.const import Platform

from . import monitor
from .const import DOMAIN, HISTORY_KEY, PANEL_ELEMENT, PANEL_URL, STATIC_URL, STORAGE_KEY, STORAGE_VERSION
from .inventory import Manager, empty_store
from .jobs import Firmware, Jobs
from .provision import Provisioner
from .fallback import Fallback
from .presets import Presets
from .reset import Reset
from .refs import Refs
from .websocket import async_register_commands

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR]
FRONTEND_DIR = Path(__file__).parent / "frontend"

type ShellyManagerConfigEntry = ConfigEntry[Manager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_commands(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ShellyManagerConfigEntry) -> bool:
    store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    data = await store.async_load() or empty_store()
    for key, val in empty_store().items():
        data.setdefault(key, val)
    manager = Manager(hass, entry, store, data)
    manager.history_store = Store(hass, 1, HISTORY_KEY)
    manager.history = await manager.history_store.async_load() or monitor.empty()
    manager.jobs = Jobs(manager)
    manager.firmware = Firmware(manager)
    manager.provision = Provisioner(manager)
    manager.fallback = Fallback(manager)
    manager.presets = Presets(manager)
    manager.reset = Reset(manager)
    manager.refs = Refs(hass)
    entry.runtime_data = manager
    await manager.async_start()
    manager.provision.async_start()
    manager.fallback.async_start()
    manager.provision.track_new()
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await _register_panel(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _options_updated(hass: HomeAssistant, entry: ShellyManagerConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ShellyManagerConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ShellyManagerConfigEntry) -> None:
    frontend.async_remove_panel(hass, PANEL_URL)
    await Store(hass, STORAGE_VERSION, STORAGE_KEY).async_remove()
    await Store(hass, 1, HISTORY_KEY).async_remove()


async def _register_panel(hass: HomeAssistant) -> None:
    flags = hass.data.setdefault(DOMAIN, {})
    if not flags.get("static"):
        # Version im Pfad, damit auch die relativ importierten Module neu geladen werden
        def newest() -> str:
            digest = hashlib.sha1()
            for p in sorted(FRONTEND_DIR.glob("*.js")):
                digest.update(p.read_bytes())
            return digest.hexdigest()[:10]

        flags["version"] = await hass.async_add_executor_job(newest)
        flags["base"] = f"{STATIC_URL}/{flags['version']}"
        await hass.http.async_register_static_paths([StaticPathConfig(flags["base"], str(FRONTEND_DIR), False)])
        flags["static"] = True
    if PANEL_URL in hass.data.get(frontend.DATA_PANELS, {}):
        return
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_ELEMENT,
        frontend_url_path=PANEL_URL,
        module_url=f"{flags['base']}/shelly-manager-panel.js",
        sidebar_title="Shellys",
        sidebar_icon="mdi:chip",
        require_admin=True,
        config={},
    )
