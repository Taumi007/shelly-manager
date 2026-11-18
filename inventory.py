"""Gerätekatalog: Shelly-Integration, HA-Registries, mDNS und IP-Scan zusammenführen.

Primärquelle sind die Config-Entries der Shelly-Integration: IP, Passwort, Geräte und Entities.
Konfiguration und Status liest der Manager aus dem bereits verbundenen Gerät der Integration
(kein zusätzlicher Netzwerkverkehr). Nur »Alle Geräteinfos« fragt die Geräte per HTTP ab.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store

from . import client, monitor
from .unifi import UniFi
from .config_flow import parse_ranges
from .const import (CONF_CONCURRENCY, CONF_FULL_INTERVAL, CONF_MDNS, CONF_SCAN_RANGES, DEFAULT_CONCURRENCY,
                    DEFAULT_FULL_INTERVAL, DEFAULT_MDNS, SAMPLE_INTERVAL, SCAN_CONCURRENCY, SCAN_MAX_HOSTS, SHELLY,
                    SIGNAL_EVENT)
from .model import build_row, build_rows, device_kind, name_ops
from .naming import check as check_rules

_LOGGER = logging.getLogger(__name__)

# Sub-Gerät: Identifier »<MAC>-switch:1« (Phasen: »<MAC>-em:0-a«)
SUB_RE = re.compile(r"-([a-z0-9_]+:\d+)(?:-[a-z0-9_]+)?$")
# Entity: unique_id »<MAC>-switch:0« oder »<MAC>-switch:0-power«
ENT_RE = re.compile(r"-([a-z0-9_]+:\d+)(?:-(.+))?$")
OUTPUT_DOMAINS = ("switch", "light", "cover", "valve", "fan")
POWER_ATTRS = ("power", "act_power", "apower", "total_act_power")
MDNS_TYPE = "_shelly._tcp.local."
UNIFI_DISCOVER_MIN = 10        # Minuten zwischen zwei Durchläufen
UNIFI_REPROBE_S = 24 * 3600    # Nicht-Shellys (gleiche Herstellerkennung) erst nach einem Tag erneut prüfen
UNIFI_DISCOVER_MAX = 40        # höchstens so viele Abfragen pro Durchlauf


def empty_store() -> dict:
    return {"version": 1, "discovered": {}, "cache": {}}


class Manager:
    """Laufzeitdaten: Katalog, Abfragen, Discovery und Fortschritt."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: Store, data: dict) -> None:
        self.hass = hass
        self.entry = entry
        self.store = store
        self.data = data
        self.session = async_get_clientsession(hass)
        opts = entry.options
        self.ranges: list[str] = parse_ranges(opts.get(CONF_SCAN_RANGES, ""))
        self.mdns_enabled: bool = opts.get(CONF_MDNS, DEFAULT_MDNS)
        self.full_interval: int = opts.get(CONF_FULL_INTERVAL, DEFAULT_FULL_INTERVAL)
        self.sem = asyncio.Semaphore(opts.get(CONF_CONCURRENCY, DEFAULT_CONCURRENCY))
        self.busy: dict[str, str] = {}  # mac -> queued | running
        self.scan_state: dict[str, Any] = {"running": False}
        self._unsubs: list[CALLBACK_TYPE] = []
        self._changed_handle: CALLBACK_TYPE | None = None
        self._browser = None
        self._tasks: set[asyncio.Task] = set()
        self.unifi = UniFi(hass)
        self.fixed_range: str | None = (parse_ranges(opts.get("fixed_ip_range", "")) or [None])[0]
        self.history: dict = monitor.empty()
        self.history_store: Store | None = None
        self.jobs = None  # jobs.Jobs, wird in __init__ gesetzt
        self.firmware = None  # jobs.Firmware
        self.provision = None  # provision.Provisioner
        self.fallback = None  # fallback.Fallback
        self.presets = None  # presets.Presets
        self.reset = None  # reset.Reset
        self.refs = None  # refs.Refs
        self._unifi_probed: dict[str, float] = {}
        self._live_since: dict[str, tuple[int, float]] = {}  # mac → (Geräteobjekt der Shelly-Integration, seit wann)

    # ── Lebenszyklus ──────────────────────────────────────────────────────────
    async def async_start(self) -> None:
        for event in (dr.EVENT_DEVICE_REGISTRY_UPDATED, er.EVENT_ENTITY_REGISTRY_UPDATED, ar.EVENT_AREA_REGISTRY_UPDATED):
            self._unsubs.append(self.hass.bus.async_listen(event, self._registry_changed))
        if self.full_interval:
            self._unsubs.append(async_track_time_interval(
                self.hass, self._periodic, timedelta(hours=self.full_interval), name="shelly_manager full refresh"))
        if self.mdns_enabled:
            await self._start_mdns()
        self._unsubs.append(async_track_time_interval(
            self.hass, self._sample, timedelta(seconds=SAMPLE_INTERVAL), name="shelly_manager sample"))
        # Erste Messung, wenn die Shelly-Integration ihre Geräte verbunden hat
        self._unsubs.append(async_call_later(self.hass, 90, self._sample))
        # Neue Shellys über die UniFi-Clientliste finden (funktioniert ohne mDNS über VLAN-Grenzen)
        self._unsubs.append(async_track_time_interval(
            self.hass, self._unifi_discover, timedelta(minutes=UNIFI_DISCOVER_MIN), name="shelly_manager unifi discovery"))
        self._unsubs.append(async_call_later(self.hass, 120, self._unifi_discover))

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._changed_handle:
            self._changed_handle()
        if self._browser is not None:
            await self._browser.async_cancel()
            self._browser = None
        for task in list(self._tasks):
            task.cancel()
        await self.store.async_save(self.data)
        if self.history_store is not None:
            await self.history_store.async_save(self.history)

    def _spawn(self, coro) -> None:
        task = self.hass.async_create_background_task(coro, "shelly_manager job")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _save(self) -> None:
        self.store.async_delay_save(lambda: self.data, 15)

    def emit(self, event: dict) -> None:
        async_dispatcher_send(self.hass, SIGNAL_EVENT, event)

    def emit_op(self, op_id: str | None, item: str, state: str, error: str | None = None) -> None:
        """Fortschritt einer Dialog-Aktion (Liste zum Abhaken im Panel)."""
        if op_id:
            self.emit({"kind": "op", "op_id": op_id, "item": item, "state": state, "error": error})

    @callback
    def _registry_changed(self, _event: Event) -> None:
        # Gesammelt melden, damit Massenänderungen nicht hunderte Neuaufbauten auslösen
        if self._changed_handle:
            self._changed_handle()

        @callback
        def fire(_now) -> None:
            self._changed_handle = None
            self.emit({"kind": "changed"})

        self._changed_handle = async_call_later(self.hass, 2, fire)

    @callback
    def _sample(self, _now=None) -> None:
        """RSSI, Laufzeit und Verfügbarkeit aus dem Live-Status der Shelly-Integration aufzeichnen."""
        ts = time.time()
        changed = False
        for dev in self.collect():
            sh = dev.get("shelly") or {}
            if not sh.get("live"):
                continue
            status = sh.get("status") or {}
            available = (dev.get("ha") or {}).get("available")
            online = available is not False
            events = monitor.sample(
                self.history, dev["mac"], ts,
                rssi=(status.get("wifi") or {}).get("rssi") if online else None,
                uptime=(status.get("sys") or {}).get("uptime") if online else None,
                reset_reason=(status.get("sys") or {}).get("reset_reason"),
                available=available, sleeping=dev.get("sleeping"))
            changed = changed or bool(events)
        if self.history_store is not None:
            self.history_store.async_delay_save(lambda: self.history, 900)
        self.emit({"kind": "sampled"})
        if changed:
            self.emit({"kind": "changed"})

    def _with_history(self, dev: dict) -> dict:
        dev["history"] = monitor.summary(self.history, dev["mac"], time.time())
        dev["unifi"] = self.unifi.lookup(dev["mac"])
        fb = (self.data.get("fallback") or {}).get(dev["mac"]) or {}
        dev["fallback"] = {"enabled": bool(fb.get("enabled") and fb.get("script_id") is not None), "always": bool(fb.get("always")),
                           "bindings": len(fb.get("bindings") or []), "error": fb.get("error")}
        return dev

    async def _periodic(self, _now) -> None:
        macs = [d["mac"] for d in self.collect() if d.get("ha") and not d.get("sleeping") and d.get("gen") != 1]
        self.refresh("full", macs)

    # ── Katalog ───────────────────────────────────────────────────────────────
    def areas(self) -> dict[str, str]:
        return {a.id: a.name for a in ar.async_get(self.hass).async_list_areas()}

    def rows(self) -> list[dict]:
        return build_rows(self.collect(), self.areas())

    def device(self, mac: str) -> dict | None:
        return self.collect_one(mac)

    def collect(self) -> list[dict]:
        hass = self.hass
        dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
        helpers = self._switch_as_x(ent_reg)
        out: dict[str, dict] = {}
        for entry in hass.config_entries.async_entries(SHELLY):
            dev = self._from_entry(entry, dev_reg, ent_reg, helpers)
            if dev:
                out[dev["mac"]] = dev
        self._sync_flows(out)
        for mac, disc in self.data["discovered"].items():
            if mac in out:
                out[mac]["sources"] = sorted(set(out[mac]["sources"]) | set(disc.get("sources") or []))
            else:
                out[mac] = self._from_discovered(mac, disc)
        return [self._with_history(d) for d in out.values()]

    def collect_one(self, mac: str) -> dict | None:
        """Ein Gerät neu aufbauen (für Fortschrittsmeldungen ohne kompletten Katalog)."""
        dev_reg, ent_reg = dr.async_get(self.hass), er.async_get(self.hass)
        for entry in self.hass.config_entries.async_entries(SHELLY):
            if _mac(entry) == mac:
                dev = self._from_entry(entry, dev_reg, ent_reg, self._switch_as_x(ent_reg))
                if dev and (disc := self.data["discovered"].get(mac)):
                    dev["sources"] = sorted(set(dev["sources"]) | set(disc.get("sources") or []))
                return self._with_history(dev) if dev else None
        disc = self.data["discovered"].get(mac)
        return self._with_history(self._from_discovered(mac, disc)) if disc else None

    def _from_entry(self, entry: ConfigEntry, dev_reg, ent_reg, helpers: list[dict]) -> dict | None:
        mac = _mac(entry)
        if not mac:
            return None
        d = entry.data
        dev = {
            "mac": mac, "host": d.get("host"), "port": d.get("port"), "gen": d.get("gen"),
            "model_id": d.get("model"), "sleeping": bool(d.get("sleep_period")), "sources": ["ha"],
            "ha": self._ha_part(entry, mac, dev_reg, ent_reg),
            "helpers": [h for h in helpers if h["entry_id"] == entry.entry_id],
        }
        if dev["ha"]:
            dev["model"] = dev["ha"].pop("model", None)
        dev["shelly"] = self._shelly_part(entry, mac)
        if dev["gen"] is None and dev["shelly"].get("info"):
            dev["gen"] = dev["shelly"]["info"].get("gen")
        return dev

    def _from_discovered(self, mac: str, disc: dict) -> dict:
        cache = self.data["cache"].get(mac) or {}
        return {
            "mac": mac, "host": disc.get("host"), "port": disc.get("port"), "gen": disc.get("gen"),
            "model": disc.get("model"), "model_id": disc.get("model"), "fw": disc.get("fw"),
            "app": disc.get("app"), "sleeping": False, "sources": sorted(disc.get("sources") or []),
            "ha": None, "helpers": [], "flow_id": disc.get("flow_id"),
            "shelly": {"info": cache.get("info") or {"name": disc.get("name"), "ver": disc.get("fw"),
                                                     "auth_en": disc.get("auth")},
                       "config": cache.get("config"), "status": cache.get("status"),
                       "fetched_at": cache.get("fetched_at"), "error": cache.get("error"), "live": False},
        }

    def _sync_flows(self, known: dict[str, dict]) -> None:
        """Noch nicht eingerichtete Funde der Shelly-Integration (zeroconf / bluetooth) übernehmen."""
        active = set()
        for flow in self.hass.config_entries.flow.async_progress_by_handler(SHELLY):
            ctx = flow.get("context") or {}
            if ctx.get("source") in ("user", "reauth", "reconfigure"):
                continue
            mac = (ctx.get("unique_id") or "").replace(":", "").upper()
            if not mac or mac in known:
                continue
            active.add(mac)
            ph = ctx.get("title_placeholders") or {}
            disc = self.data["discovered"].setdefault(mac, {"sources": []})
            disc["flow_id"] = flow["flow_id"]
            disc.setdefault("name", ph.get("name"))
            if ctx.get("host"):
                disc["host"] = ctx["host"]
            src = f"ha_{ctx.get('source')}"
            if src not in disc["sources"]:
                disc["sources"].append(src)
        for mac, disc in self.data["discovered"].items():
            if mac not in active:
                disc.pop("flow_id", None)

    def _ha_part(self, entry: ConfigEntry, mac: str, dev_reg, ent_reg) -> dict | None:
        devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
        # Gerät dieses Eintrags ohne Elternteil (die MAC kann auch bei anderen Integrationen hängen)
        root = next((x for x in devices if x.via_device_id is None
                     and (dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac)) in x.connections), None)
        if root is None:
            root = next((x for x in devices if x.via_device_id is None), None)
        if root is None:
            return None
        channels: dict[str, dict] = {}
        for sub in devices:
            if sub.via_device_id != root.id:
                continue
            for dom, ident in sub.identifiers:
                m = SUB_RE.search(ident) if dom == SHELLY else None
                if m:
                    channels[m.group(1)] = {"device_id": sub.id, "name": sub.name, "name_by_user": sub.name_by_user,
                                            "area_id": sub.area_id}
        entities: dict[str, dict] = {}
        entity_ids: list[str] = []
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if ent.disabled_by:
                continue
            entity_ids.append(ent.entity_id)
            m = ENT_RE.search(ent.unique_id or "")
            if not m:
                continue
            key, attr = m.group(1), m.group(2) or ""
            slot = entities.setdefault(key, {})
            if key.startswith("input:") and ent.domain in ("event", "binary_sensor"):
                # Eingänge (z. B. i4): kein Sub-Gerät, Name und Bereich hängen an der Entity
                if ent.domain == "event" or "input" not in slot:
                    slot["input"] = {"entity_id": ent.entity_id, "name": ent.name, "original_name": ent.original_name,
                                     "area_id": ent.area_id}
            elif ent.domain in OUTPUT_DOMAINS and not attr:
                slot["switch"] = ent.entity_id
                slot["device_id"] = ent.device_id
            elif ent.domain == "sensor" and (ent.original_device_class or ent.device_class) == "power":
                # Pro 3EM: Summe statt einzelner Phasen; sonst exakte Kennung vor Varianten (power_light …)
                rank = 2 if attr in POWER_ATTRS else 1
                if rank > slot.get("_rank", 0):
                    slot["power"], slot["_rank"] = ent.entity_id, rank
        inputs = {}
        for key, slot in entities.items():
            slot.pop("_rank", None)
            if "input" in slot:
                inputs[key] = slot.pop("input")
        states = [self.hass.states.get(e) for e in entity_ids]
        available = any(s is not None and s.state != "unavailable" for s in states)
        return {
            "device_id": root.id, "entry_id": entry.entry_id, "entry_state": _state(entry), "entry_title": entry.title,
            "name": root.name, "name_by_user": root.name_by_user, "area_id": root.area_id,
            "model": root.model, "sw": root.sw_version,
            "channels": channels, "entities": entities, "inputs": inputs, "entity_ids": sorted(entity_ids), "available": available,
        }

    def _shelly_part(self, entry: ConfigEntry, mac: str) -> dict:
        """Daten aus dem verbundenen Gerät der Shelly-Integration, ergänzt um eigene Abfragen."""
        cache = self.data["cache"].get(mac) or {}
        info = config = status = None
        live = False
        rt = getattr(entry, "runtime_data", None) if entry.state is ConfigEntryState.LOADED else None
        coord = getattr(rt, "rpc", None) if rt is not None else None
        device = getattr(coord, "device", None)
        if device is not None:
            try:
                if getattr(device, "initialized", True):
                    info, config, status = device.shelly, device.config, device.status
                    live = True
            except Exception:  # noqa: BLE001 – aioshelly wirft bei nicht initialisierten Geräten
                info = config = status = None
        # Eigene Abfrage nur verwenden, wenn sie neuer ist als die Verbindung der Shelly-Integration.
        # HA lädt das Gerät bei jeder Konfigurationsänderung neu – ältere Abfragen wären sonst veraltet
        # (z. B. Namen, die später in der Shelly-App oder im Webinterface gesetzt wurden).
        if live:
            seen = self._live_since.get(mac)
            if seen is None or seen[0] != id(device):
                seen = (id(device), time.time())
                self._live_since[mac] = seen
            if cache.get("config") is not None and (cache.get("fetched_at") or 0) > seen[1]:
                config = cache["config"]
        elif cache.get("config") is not None:
            config = cache["config"]
        return {
            "info": info or cache.get("info"), "config": config,
            "status": status if status is not None else cache.get("status"),
            "fetched_at": cache.get("fetched_at"), "error": cache.get("error"), "live": live,
        }

    def _switch_as_x(self, ent_reg) -> list[dict]:
        out = []
        for ent in ent_reg.entities.values():
            if ent.platform != "switch_as_x" or not ent.config_entry_id:
                continue
            ce = self.hass.config_entries.async_get_entry(ent.config_entry_id)
            source = (ce.options.get("entity_id") if ce else None)
            src = ent_reg.async_get(source) if source else None
            if src is None or src.platform != SHELLY:
                continue
            out.append({"entity_id": ent.entity_id, "source": source, "entry_id": src.config_entry_id,
                        "target_domain": (ce.options.get("target_domain") if ce else None) or ent.domain,
                        "helper_entry_id": ent.config_entry_id})
        return out

    def retitle(self, mac: str, title: str | None = None) -> bool:
        """Titel des Shelly-Integrationseintrags auf den vollen Gerätenamen setzen."""
        for entry in self.hass.config_entries.async_entries(SHELLY):
            if _mac(entry) == mac:
                if title is None:
                    dev = self.collect_one(mac)
                    cfg = ((dev or {}).get("shelly") or {}).get("config") or {}
                    title = ((cfg.get("sys") or {}).get("device") or {}).get("name")
                title = " ".join((title or "").split())
                if title and entry.title != title:
                    self.hass.config_entries.async_update_entry(entry, title=title)
                    return True
                return False
        return False

    def password_for(self, mac: str) -> str | None:
        for entry in self.hass.config_entries.async_entries(SHELLY):
            if (entry.unique_id or "").replace(":", "").upper() == mac:
                return entry.data.get("password")
        return None

    # ── Abfragen ──────────────────────────────────────────────────────────────
    def refresh(self, mode: str, macs: list[str] | None = None) -> int:
        devices = {d["mac"]: d for d in self.collect()}
        targets = [m for m in (macs or list(devices)) if m in devices and m not in self.busy]
        if not targets:
            return 0
        if mode != "full":
            # Kanal-Status kommt live aus HA: nur kurz anzeigen und Zeilen neu aufbauen
            for mac in targets:
                self.busy[mac] = "running"
            self.emit({"kind": "progress", "busy": dict(self.busy)})

            @callback
            def done(_now) -> None:
                for mac in targets:
                    self.busy.pop(mac, None)
                self.emit({"kind": "progress", "busy": dict(self.busy)})
                self.emit({"kind": "changed"})

            async_call_later(self.hass, 0.4, done)
            return len(targets)
        for mac in targets:
            self.busy[mac] = "queued"
        self.emit({"kind": "progress", "busy": dict(self.busy)})
        self._spawn(self._run_full(targets, devices))
        return len(targets)

    async def _run_full(self, macs: list[str], devices: dict[str, dict]) -> None:
        async def one(mac: str) -> None:
            dev = devices[mac]
            try:
                if dev.get("host") and dev.get("gen") != 1 and not dev.get("sleeping"):
                    async with self.sem:
                        self._set_busy(mac, "running")
                        await self._fetch(mac, dev)
            finally:
                self.busy.pop(mac, None)
                fresh = self.collect_one(mac)
                self.emit({"kind": "row", "mac": mac, "busy": dict(self.busy),
                           "row": build_row(fresh, self.areas()) if fresh else None})

        try:
            await asyncio.gather(*(one(m) for m in macs), return_exceptions=True)
        finally:
            self._save()
            self.emit({"kind": "changed"})

    def _set_busy(self, mac: str, state: str) -> None:
        self.busy[mac] = state
        self.emit({"kind": "progress", "busy": dict(self.busy)})

    async def _fetch(self, mac: str, dev: dict) -> None:
        cache = self.data["cache"].setdefault(mac, {})
        try:
            result = await client.fetch_all(self.session, dev["host"], port=dev.get("port"),
                                            password=self.password_for(mac))
        except client.ShellyError as err:
            cache.update({"error": str(err), "error_at": time.time()})
            return
        cache.update(result)
        cache.update({"fetched_at": time.time(), "error": None})

    # ── Namen ─────────────────────────────────────────────────────────────────
    def plan_names(self, items: list[dict]) -> tuple[list[dict], list[dict]]:
        """items: [{mac, direction, keys?}] → (ops, skipped). Immer aus dem aktuellen Stand berechnet."""
        areas = self.areas()
        area_ids = {name: aid for aid, name in areas.items()}
        ops: list[dict] = []
        skipped: list[dict] = []
        for item in items:
            dev = self.collect_one(item["mac"])
            if dev is None:
                skipped.append({"mac": item["mac"], "key": "device", "reason": "Gerät nicht gefunden"})
                continue
            o, s = name_ops(build_row(dev, areas), item["direction"], item.get("keys"), area_ids, item.get("only"))
            kind = device_kind(dev)
            for op in o:
                if op["target"] == "shelly" and op.get("new"):
                    k = kind if op["key"] == "device" else ("input" if op.get("item_kind") == "input" else "channel")
                    op["rules"] = check_rules(op["new"], op.get("area"), k)
            ops.extend(o)
            skipped.extend(s)
        return ops, skipped

    async def apply_names(self, ops: list[dict], op_id: str | None = None) -> list[dict]:
        """Shelly-Namen per RPC und HA-Namen/-Bereiche in der Geräte-Registry setzen."""
        dev_reg = dr.async_get(self.hass)
        results: list[dict] = []
        by_mac: dict[str, list[dict]] = {}
        for op in ops:
            if op["target"] == "ha":
                self.emit_op(op_id, op["mac"], "running")
                try:
                    if op.get("entity_id"):
                        er.async_get(self.hass).async_update_entity(op["entity_id"], name=op["name_by_user"], area_id=op["area_id"])
                    else:
                        dev_reg.async_update_device(op["device_id"], name_by_user=op["name_by_user"], area_id=op["area_id"])
                    results.append({**op, "ok": True})
                except Exception as err:  # noqa: BLE001 – Ergebnis pro Operation melden
                    results.append({**op, "ok": False, "error": str(err)})
            else:
                by_mac.setdefault(op["mac"], []).append(op)

        async def write(mac: str, mac_ops: list[dict]) -> None:
            dev = self.collect_one(mac)
            async with self.sem:
                self._set_busy(mac, "running")
                self.emit_op(op_id, mac, "running")
                try:
                    for op in mac_ops:
                        try:
                            await client.rpc(self.session, dev["host"], op["method"], op["params"],
                                             port=dev.get("port"), password=self.password_for(mac))
                            results.append({**op, "ok": True})
                            if op["key"] == "device" and op.get("new"):
                                self.retitle(mac, op["new"])
                        except client.ShellyError as err:
                            results.append({**op, "ok": False, "error": str(err)})
                    # Neu einlesen, damit Tabelle und Vergleich sofort den neuen Stand zeigen
                    await self._fetch(mac, dev)
                finally:
                    self.busy.pop(mac, None)
                    bad = [r for r in results if r["mac"] == mac and not r.get("ok")]
                    self.emit_op(op_id, mac, "error" if bad else "done", bad[0].get("error") if bad else None)
                    fresh = self.collect_one(mac)
                    self.emit({"kind": "row", "mac": mac, "busy": dict(self.busy),
                               "row": build_row(fresh, self.areas()) if fresh else None})

        for mac in {op["mac"] for op in ops if op["target"] == "ha"} - set(by_mac):
            bad = [r for r in results if r["mac"] == mac and not r.get("ok")]
            self.emit_op(op_id, mac, "error" if bad else "done", bad[0].get("error") if bad else None)
        for mac in by_mac:
            self.busy.setdefault(mac, "queued")
        if by_mac:
            self.emit({"kind": "progress", "busy": dict(self.busy)})
            await asyncio.gather(*(write(m, o) for m, o in by_mac.items()))
            self._save()
        self.emit({"kind": "changed"})
        return results

    async def set_input(self, mac: str, key: str, name: str | None = None, enable: bool | None = None) -> dict:
        """Eingang benennen oder deaktivieren."""
        if enable is not None and name is None:
            dev = self.collect_one(mac)
            if dev is None or not dev.get("host"):
                raise client.ShellyError("Gerät nicht gefunden")
            await client.rpc(self.session, dev["host"], "Input.SetConfig",
                             {"id": int(key.split(":")[1]), "config": {"enable": enable}},
                             port=dev.get("port"), password=self.password_for(mac))
            await self._refresh_one(mac, dev)
            return {"ok": True}
        return await self.set_name(mac, key, name or "")

    async def set_name(self, mac: str, key: str, name: str, area_id: str | None = None) -> dict:
        """Vollen Namen in den Shelly schreiben und in HA den reinen Namen (ohne Bereich) setzen."""
        from .model import shelly_name_call, strip_area
        dev = self.collect_one(mac)
        if dev is None or not dev.get("host"):
            raise client.ShellyError("Gerät nicht gefunden")
        ha = dev.get("ha") or {}
        areas = self.areas()
        self._set_busy(mac, "running")
        try:
            method, params = shelly_name_call(key, name)
            await client.rpc(self.session, dev["host"], method, params, port=dev.get("port"), password=self.password_for(mac))
            name = " ".join(name.split())
            if key == "device":
                self.retitle(mac, name)
            if key == "device" and ha.get("device_id"):
                aid = area_id or ha.get("area_id")
                pure = strip_area(name, areas.get(aid or ""))
                dr.async_get(self.hass).async_update_device(ha["device_id"], name_by_user=pure or name,
                                                            **({"area_id": area_id} if area_id else {}))
            elif key.startswith("input:"):
                inp = (ha.get("inputs") or {}).get(key)
                if inp and name:
                    area = areas.get(inp.get("area_id") or "") or areas.get(ha.get("area_id") or "")
                    er.async_get(self.hass).async_update_entity(inp["entity_id"], name=strip_area(name, area) or name)
            else:
                sub = (ha.get("channels") or {}).get(key)
                if sub and name:
                    area = areas.get(area_id or "") or areas.get(sub.get("area_id") or "") or areas.get(ha.get("area_id") or "")
                    dr.async_get(self.hass).async_update_device(sub["device_id"], name_by_user=strip_area(name, area) or name,
                                                                **({"area_id": area_id} if area_id else {}))
            await self._fetch(mac, dev)
        finally:
            self.busy.pop(mac, None)
            self._emit_row(mac)
            self._save()
        return {"ok": True}

    def disable_in_ha(self, mac: str, key: str) -> str:
        """Kanal in HA deaktivieren: Sub-Gerät (Mehrkanaler) bzw. Schalt-Entity; Eingänge über ihre Entity."""
        dev = self.collect_one(mac)
        ha = (dev or {}).get("ha") or {}
        sub = (ha.get("channels") or {}).get(key)
        if sub and sub.get("device_id"):
            dr.async_get(self.hass).async_update_device(sub["device_id"], disabled_by=dr.DeviceEntryDisabler.USER)
            return "device"
        ent = ((ha.get("inputs") or {}).get(key) or {}).get("entity_id") or ((ha.get("entities") or {}).get(key) or {}).get("switch")
        if ent:
            er.async_get(self.hass).async_update_entity(ent, disabled_by=er.RegistryEntryDisabler.USER)
            return "entity"
        raise client.ShellyError("Kein HA-Gegenstück für diesen Kanal gefunden")

    async def _refresh_one(self, mac: str, dev: dict) -> None:
        await self._fetch(mac, dev)
        self._emit_row(mac)
        self._save()

    def _emit_row(self, mac: str) -> None:
        fresh = self.collect_one(mac)
        self.emit({"kind": "row", "mac": mac, "busy": dict(self.busy),
                   "row": build_row(fresh, self.areas()) if fresh else None})

    def reload_entries(self, macs: list[str]) -> int:
        """Shelly-Einträge in HA neu laden – nacheinander mit Pause."""
        entries = [e for e in self.hass.config_entries.async_entries(SHELLY) if _mac(e) in set(macs)]

        async def run() -> None:
            for entry in entries:
                try:
                    await self.hass.config_entries.async_reload(entry.entry_id)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Neuladen von %s fehlgeschlagen: %s", entry.title, err)
                await asyncio.sleep(2)
            self.emit({"kind": "changed"})

        if entries:
            self._spawn(run())
        return len(entries)

    # ── Discovery ─────────────────────────────────────────────────────────────
    def _remember(self, data: dict, host: str, port: int | None, source: str) -> str | None:
        mac = client.mac_of(data)
        if not mac:
            return None
        disc = self.data["discovered"].setdefault(mac, {"sources": []})
        disc.update({"host": host, "port": port, "gen": data.get("gen"), "model": data.get("model") or data.get("type"),
                     "name": data.get("name"), "fw": data.get("ver") or data.get("fw"), "app": data.get("app"),
                     "auth": data.get("auth_en"), "seen": time.time()})
        if source not in disc["sources"]:
            disc["sources"].append(source)
        self._save()
        return mac

    def scan(self, ranges: list[str] | None = None) -> int:
        if self.scan_state.get("running"):
            return 0
        hosts = client.expand_ranges(ranges or self.ranges, SCAN_MAX_HOSTS)
        if not hosts:
            return 0
        self.scan_state = {"running": True, "total": len(hosts), "done": 0, "found": 0}
        self.emit({"kind": "scan", **self.scan_state})
        self._spawn(self._run_scan(hosts))
        return len(hosts)

    async def _run_scan(self, hosts: list[str]) -> None:
        sem = asyncio.Semaphore(SCAN_CONCURRENCY)
        last = 0.0

        async def one(host: str) -> None:
            nonlocal last
            async with sem:
                data = await client.probe(self.session, host)
            self.scan_state["done"] += 1
            if data and self._remember(data, host, None, "scan"):
                self.scan_state["found"] += 1
            now = time.monotonic()
            if now - last > 0.5:
                last = now
                self.emit({"kind": "scan", **self.scan_state})

        try:
            await asyncio.gather(*(one(h) for h in hosts))
        finally:
            self.scan_state["running"] = False
            self.emit({"kind": "scan", **self.scan_state})
            self.emit({"kind": "changed"})

    @callback
    def _unifi_discover(self, _now=None) -> None:
        known = {e.unique_id.replace(":", "").upper() for e in self.hass.config_entries.async_entries(SHELLY) if e.unique_id}
        known |= set(self.data["discovered"])
        ouis = {":".join(m.lower()[i:i + 2] for i in (0, 2, 4)) for m in known if len(m) == 12}
        now = time.time()
        todo = [c for c in self.unifi.shelly_candidates(ouis)
                if c["mac"] not in known and now - self._unifi_probed.get(c["mac"], 0) > UNIFI_REPROBE_S]
        if todo:
            self._spawn(self._run_unifi_discover(todo))

    async def _run_unifi_discover(self, todo: list[dict]) -> None:
        found = 0
        sem = asyncio.Semaphore(4)

        async def one(c: dict) -> None:
            nonlocal found
            self._unifi_probed[c["mac"]] = time.time()
            async with sem:
                data = await client.probe(self.session, c["ip"])
            if data and self._remember(data, c["ip"], None, "unifi"):
                found += 1

        await asyncio.gather(*(one(c) for c in todo[:UNIFI_DISCOVER_MAX]))
        if found:
            _LOGGER.info("UniFi-Clientliste: %d neue Shelly(s) gefunden", found)
            self.emit({"kind": "changed"})

    async def _start_mdns(self) -> None:
        if "zeroconf" not in self.hass.config.components:
            return
        try:
            from homeassistant.components import zeroconf as ha_zeroconf
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo
        except ImportError:
            return
        aiozc = await ha_zeroconf.async_get_async_instance(self.hass)

        seen: dict[str, float] = {}

        async def resolve(name: str) -> None:
            if time.monotonic() - seen.get(name, -1e9) < 600:
                return
            seen[name] = time.monotonic()
            info = AsyncServiceInfo(MDNS_TYPE, name)
            if not await info.async_request(aiozc.zeroconf, 3000):
                return
            addrs = info.parsed_addresses()
            host = next((a for a in addrs if ":" not in a), None)
            if not host:
                return
            port = info.port if info.port not in (80, None) else None
            data = await client.probe(self.session, host, port)
            if data and self._remember(data, host, port, "mdns"):
                self._registry_changed(None)  # gesammelt melden

        def on_change(zeroconf, service_type: str, name: str, state_change) -> None:
            if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
                self._spawn(resolve(name))

        self._browser = AsyncServiceBrowser(aiozc.zeroconf, [MDNS_TYPE], handlers=[on_change])


def _mac(entry: ConfigEntry) -> str:
    return (entry.unique_id or "").replace(":", "").upper()


def _state(entry: ConfigEntry) -> str:
    return entry.state.value if hasattr(entry.state, "value") else str(entry.state)


