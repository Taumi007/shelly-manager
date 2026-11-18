"""Sammel-Sensoren: Überblick über alle Shellys für Dashboards und Automationen."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_EVENT

MIN_INTERVAL = 30  # s – Zeilen höchstens so oft neu aufbauen


@dataclass(frozen=True)
class Stat:
    key: str
    name: str
    icon: str
    count: Callable[[dict], bool]


STATS = (
    Stat("devices", "Geräte", "mdi:chip", lambda r: r["in_ha"]),
    Stat("offline", "Offline", "mdi:lan-disconnect", lambda r: r["in_ha"] and r.get("available") is False and not r["sleeping"]),
    Stat("updates", "Firmware-Updates", "mdi:update", lambda r: bool(r.get("update"))),
    Stat("errors", "Kritische Probleme", "mdi:alert-circle", lambda r: r.get("severity") == "error"),
    Stat("warnings", "Warnungen", "mdi:alert", lambda r: r.get("severity") == "warning"),
    Stat("weak_wifi", "Schwaches WLAN", "mdi:wifi-strength-1-alert", lambda r: any(p["code"] == "rssi" for p in r["problems"])),
    Stat("reboots", "Neustarts (24 h)", "mdi:restart", lambda r: (r.get("reboots_24h") or 0) > 0),
    Stat("not_in_ha", "Nicht in HA", "mdi:help-network", lambda r: not r["in_ha"]),
)


class Stats:
    """Gemeinsame, gedrosselte Berechnung für alle Sensoren."""

    def __init__(self, manager) -> None:
        self.m = manager
        self.values: dict[str, Any] = {}
        self.names: dict[str, list[str]] = {}
        self._at = 0.0
        self._listeners: list[Callable[[], None]] = []

    @callback
    def refresh(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._at < MIN_INTERVAL:
            return
        self._at = time.monotonic()
        rows = self.m.rows()
        for st in STATS:
            hits = [r for r in rows if st.count(r)]
            self.values[st.key] = len(hits)
            self.names[st.key] = sorted(r["name"] for r in hits)[:50]
        for fn in self._listeners:
            fn()


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    manager = entry.runtime_data
    stats = Stats(manager)
    entities = [StatSensor(entry, stats, st) for st in STATS]

    @callback
    def on_event(event: dict) -> None:
        if event.get("kind") in ("sampled", "changed"):
            stats.refresh()

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_EVENT, on_event))
    stats.refresh(force=True)
    async_add_entities(entities)


class StatSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "Geräte"

    def __init__(self, entry, stats: Stats, stat: Stat) -> None:
        self._stats = stats
        self._stat = stat
        self._attr_unique_id = f"{entry.entry_id}_{stat.key}"
        self._attr_name = stat.name
        self._attr_icon = stat.icon
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Shelly Manager",
                                            manufacturer="Shelly Manager",
                                            entry_type=DeviceEntryType.SERVICE)

    async def async_added_to_hass(self) -> None:
        self._stats._listeners.append(self.async_write_ha_state)
        self.async_on_remove(lambda: self._stats._listeners.remove(self.async_write_ha_state))

    @property
    def native_value(self) -> int | None:
        return self._stats.values.get(self._stat.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"geraete": self._stats.names.get(self._stat.key, [])}
