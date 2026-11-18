"""Shelly komplett zurücksetzen: Werksreset, aus HA und UniFi entfernen, Manager-Daten aufräumen.

Ablauf immer nach Vorschau (plan) und mit Fortschritt pro Schritt (run). Vorher wird eine Sicherung der
Gerätekonfiguration gespeichert, damit man Namen und Einstellungen später nachschlagen kann.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import client
from .unifi import mac_colon

if TYPE_CHECKING:
    from .inventory import Manager

_LOGGER = logging.getLogger(__name__)
SHELLY = "shelly"
GONE_TIMEOUT = 30      # s: so lange warten, bis das Gerät nach dem Werksreset vom Netz verschwindet
BACKUP_KEEP = 20       # so viele Sicherungen aufheben

STEP_LABELS = {
    "backup": "Konfiguration sichern",
    "factory": "Gerät auf Werkseinstellungen zurücksetzen",
    "name": "Namen im Gerät leeren",
    "fallback": "Fallback-Script entfernen",
    "history": "Zustandsverlauf löschen (Recorder)",
    "statistics": "Langzeitstatistiken löschen",
    "ha": "Aus Home Assistant entfernen",
    "unifi": "Aus UniFi entfernen (Name und IP-Reservierung)",
    "manager": "Daten im Shelly Manager aufräumen",
}


class Reset:
    def __init__(self, manager: Manager) -> None:
        self.m = manager
        self.hass = manager.hass
        manager.data.setdefault("reset_backups", {})

    def _entry(self, mac: str):
        for entry in self.hass.config_entries.async_entries(SHELLY):
            if (entry.unique_id or "").replace(":", "").upper() == mac:
                return entry
        return None

    # ── Vorschau ──────────────────────────────────────────────────────────────
    def plan(self, mac: str) -> dict:
        dev = self.m.collect_one(mac) or {"mac": mac}
        info = ((dev.get("shelly") or {}).get("info")) or {}
        cfg = ((dev.get("shelly") or {}).get("config")) or {}
        name = ((cfg.get("sys") or {}).get("device") or {}).get("name") or info.get("name")
        entry = self._entry(mac)
        ha = None
        if entry is not None:
            dev_reg, ent_reg = dr.async_get(self.hass), er.async_get(self.hass)
            devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
            entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
            ha = {"entry_id": entry.entry_id, "title": entry.title, "device_ids": [d.id for d in devices],
                  "devices": [d.name_by_user or d.name for d in devices], "entities": len(entities),
                  "entity_ids": sorted(e.entity_id for e in entities)}
        uni = self.m.unifi.lookup(mac)
        fb = self.m.fallback.status(mac) if self.m.fallback else {}
        ha_dev = dev.get("ha") or {}
        offline = bool(ha_dev) and (ha_dev.get("entry_state") in ("setup_retry", "setup_error", "failed_unload")
                                     or ha_dev.get("available") is False)
        reachable = bool(dev.get("host")) and not dev.get("sleeping") and not offline
        steps = [
            {"id": "backup", "default": True, "available": True, "fixed": True},
            {"id": "factory", "default": reachable, "available": reachable,
             "note": None if reachable else ("Akku-Gerät schläft – Werksreset nur am Gerät selbst" if dev.get("sleeping")
                                             else "nicht erreichbar – Werksreset ggf. am Gerät selbst (Taste lange drücken)"
                                             if offline else "keine IP bekannt")},
            {"id": "name", "default": False, "available": reachable, "exclusive_with": "factory",
             "note": "nur ohne Werksreset sinnvoll"},
            {"id": "fallback", "default": bool(fb.get("script_id") is not None or fb.get("enabled")),
             "available": bool(fb.get("enabled") or fb.get("script_id") is not None or (self.m.data.get("fallback") or {}).get(mac))},
            {"id": "history", "default": False, "available": bool(ha and ha["entity_ids"]),
             "note": "Verlauf aller Entitäten dieses Geräts – nicht wiederherstellbar"},
            {"id": "statistics", "default": False, "available": bool(ha and ha["entity_ids"]),
             "note": "z. B. Energie-Dashboard verliert die Werte dieses Geräts – nicht wiederherstellbar"},
            {"id": "ha", "default": entry is not None, "available": entry is not None},
            {"id": "unifi", "default": bool(uni and uni.get("found")), "available": bool(uni and uni.get("found"))},
            {"id": "manager", "default": True, "available": True},
        ]
        for s in steps:
            s["label"] = STEP_LABELS[s["id"]]
        return {
            "mac": mac, "name": name, "host": dev.get("host"), "model": dev.get("model"), "gen": dev.get("gen"),
            "sleeping": bool(dev.get("sleeping")), "auth": bool(info.get("auth_en")),
            "ha": ha, "unifi": uni if uni and uni.get("found") else None,
            "fallback": bool(steps[3]["available"]), "steps": steps,
        }

    # ── Ausführen ─────────────────────────────────────────────────────────────
    def start(self, mac: str, steps: list[str], op_id: str | None) -> list[str]:
        order = [s for s in STEP_LABELS if s in set(steps) | {"backup"}]
        if "factory" in order and "name" in order:
            order.remove("name")
        if "factory" in order and "fallback" in order:
            order.remove("fallback")  # der Werksreset löscht Scripts ohnehin, nur die Manager-Daten aufräumen
        if "factory" in order and self.m.provision is not None:
            self.m.provision.hold(mac)  # nicht sofort wieder automatisch einrichten
        self.m.busy[mac] = "running"
        self.m.emit({"kind": "progress", "busy": dict(self.m.busy)})
        self.m._spawn(self._run(mac, order, op_id))
        return order

    async def _run(self, mac: str, order: list[str], op_id: str | None) -> None:
        dev = self.m.collect_one(mac) or {"mac": mac}
        failed = False
        try:
            for step in order:
                if failed:
                    self.m.emit_op(op_id, step, "error", "übersprungen – vorheriger Schritt fehlgeschlagen")
                    continue
                self.m.emit_op(op_id, step, "running")
                try:
                    note = await getattr(self, f"_{step}")(mac, dev)
                    self.m.emit_op(op_id, step, "done", note)
                except Exception as err:  # noqa: BLE001 – Ergebnis pro Schritt
                    _LOGGER.warning("Zurücksetzen %s, Schritt %s: %s", mac, step, err)
                    self.m.emit_op(op_id, step, "error", str(err))
                    # Ohne Werksreset keine Spuren löschen: sonst ist das Gerät aktiv, aber nirgends mehr verwaltet
                    if step in ("backup", "factory"):
                        failed = True
        finally:
            self.m.busy.pop(mac, None)
            self.m._save()
            self.m.emit({"kind": "progress", "busy": dict(self.m.busy)})
            self.m.emit({"kind": "changed"})

    async def _call(self, dev: dict, method: str, params: dict | None = None):
        return await client.rpc(self.m.session, dev["host"], method, params, port=dev.get("port"),
                                password=self.m.password_for(dev["mac"]))

    async def _backup(self, mac: str, dev: dict) -> str | None:
        cache = self.m.data["cache"].get(mac) or {}
        backup = {"ts": time.time(), "host": dev.get("host"), "model": dev.get("model"),
                  "info": cache.get("info"), "config": cache.get("config"),
                  "ha": (dev.get("ha") or {}), "unifi": self.m.unifi.lookup(mac),
                  "fallback": (self.m.data.get("fallback") or {}).get(mac)}
        note = None
        if dev.get("host") and not dev.get("sleeping"):
            try:
                backup["config"] = await self._call(dev, "Shelly.GetConfig")
                backup["info"] = await self._call(dev, "Shelly.GetDeviceInfo")
            except client.ShellyError as err:
                note = f"nur zwischengespeicherte Konfiguration ({err})"
        store = self.m.data["reset_backups"]
        store[mac] = backup
        for old in sorted(store, key=lambda k: store[k].get("ts", 0))[:-BACKUP_KEEP]:
            del store[old]
        self.m._save()
        return note

    async def _factory(self, mac: str, dev: dict) -> str | None:
        await self._call(dev, "Shelly.FactoryReset")
        # Erfolg = Gerät verschwindet unter der alten Adresse (startet als Access Point neu)
        deadline = time.monotonic() + GONE_TIMEOUT
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            if await client.probe(self.m.session, dev["host"], dev.get("port")) is None:
                return None
        raise client.ShellyError(f"nach {GONE_TIMEOUT} s noch unter {dev['host']} erreichbar")

    async def _name(self, mac: str, dev: dict) -> str | None:
        await self._call(dev, "Sys.SetConfig", {"config": {"device": {"name": None}}})
        return None

    async def _fallback(self, mac: str, dev: dict) -> str | None:
        await self.m.fallback.remove(mac)
        return None

    def _entity_ids(self, mac: str) -> list[str]:
        entry = self._entry(mac)
        if entry is None:
            return []
        return sorted(e.entity_id for e in er.async_entries_for_config_entry(er.async_get(self.hass), entry.entry_id))

    async def _history(self, mac: str, dev: dict) -> str | None:
        ids = self._entity_ids(mac)
        if not ids:
            return "keine Entitäten"
        await self.hass.services.async_call("recorder", "purge_entities", {"entity_id": ids, "keep_days": 0}, blocking=True)
        return f"{len(ids)} Entitäten"

    async def _statistics(self, mac: str, dev: dict) -> str | None:
        from homeassistant.components.recorder import get_instance

        ids = self._entity_ids(mac)
        if not ids:
            return "keine Entitäten"
        get_instance(self.hass).async_clear_statistics(ids)
        return f"{len(ids)} Entitäten"

    async def _ha(self, mac: str, dev: dict) -> str | None:
        entry = self._entry(mac)
        if entry is None:
            return "war nicht in HA"
        await self.hass.config_entries.async_remove(entry.entry_id)
        return None

    async def _unifi(self, mac: str, dev: dict) -> str | None:
        return await self.m.unifi.forget(mac)

    async def _manager(self, mac: str, dev: dict) -> str | None:
        data = self.m.data
        for key in ("discovered", "cache", "new_devices", "fallback"):
            (data.get(key) or {}).pop(mac, None)
        # Aus »bekannt« streichen: Nach dem Wiedereinrichten erscheint es als neues Gerät mit Assistent
        if isinstance(data.get("known_macs"), list) and mac in data["known_macs"]:
            data["known_macs"].remove(mac)
        (self.m.history.get("devices") or {}).pop(mac, None)
        self.m._save()
        return None

    # ── Funde ohne HA-Eintrag aus der Liste nehmen ────────────────────────────
    async def forget_found(self, mac: str, permanent: bool) -> str:
        """Bluetooth-/Netzwerk-Fund verwerfen. permanent: in HA ignorieren (taucht nicht wieder auf)."""
        from homeassistant.config_entries import SOURCE_IGNORE

        flow_mgr = self.hass.config_entries.flow
        done = []
        for flow in flow_mgr.async_progress_by_handler(SHELLY):
            ctx = flow.get("context") or {}
            if (ctx.get("unique_id") or "").replace(":", "").upper() != mac:
                continue
            title = (ctx.get("title_placeholders") or {}).get("name") or mac
            if permanent:
                await flow_mgr.async_init(SHELLY, context={"source": SOURCE_IGNORE, "unique_id": ctx["unique_id"]},
                                          data={"unique_id": ctx["unique_id"], "title": title})
                done.append("in HA ignoriert")
            else:
                flow_mgr.async_abort(flow["flow_id"])
                done.append("Fund verworfen")
        data = self.m.data
        for key in ("discovered", "cache", "new_devices"):
            (data.get(key) or {}).pop(mac, None)
        self.m.provision.release(mac) if self.m.provision else None
        self.m._save()
        self.m.emit({"kind": "changed"})
        return ", ".join(done) or "aus der Liste entfernt"

    def backup(self, mac: str) -> dict | None:
        return self.m.data["reset_backups"].get(mac)

    # ── Sicherung bei Wiederfund ──────────────────────────────────────────────
    def summary(self, mac: str) -> dict | None:
        b = self.backup(mac)
        if not b:
            return None
        cfg = b.get("config") or {}
        return {"ts": b.get("ts"), "name": ((cfg.get("sys") or {}).get("device") or {}).get("name"),
                "model": b.get("model"), "pending": mac in self.m.data.setdefault("restore_pending", {})}

    def notify(self, mac: str, found_name: str | None) -> None:
        """Einmalige HA-Meldung: Gerät im Einrichtungsmodus, aber Sicherung vorhanden."""
        from homeassistant.components import persistent_notification

        shown = self.m.data.setdefault("backup_notified", [])
        if mac in shown or not (info := self.summary(mac)):
            return
        when = time.strftime("%d.%m.%Y %H:%M", time.localtime(info["ts"] or 0))
        persistent_notification.async_create(
            self.hass,
            f"**{found_name or mac}** ist im Einrichtungsmodus, für dieses Gerät gibt es aber eine Sicherung vom {when} "
            f"(»{info['name'] or 'ohne Namen'}«).\n\nIm [Shelly Manager](/shellys) kannst du es einrichten und die Sicherung "
            "einspielen oder die Sicherung löschen.",
            title="Shelly mit Sicherung gefunden", notification_id=f"shelly_manager_backup_{mac}")
        shown.append(mac)
        self.m._save()

    def _dismiss(self, mac: str) -> None:
        from homeassistant.components import persistent_notification

        persistent_notification.async_dismiss(self.hass, f"shelly_manager_backup_{mac}")
        shown = self.m.data.setdefault("backup_notified", [])
        if mac in shown:
            shown.remove(mac)

    def delete_backup(self, mac: str) -> None:
        self.m.data["reset_backups"].pop(mac, None)
        self.m.data.setdefault("restore_pending", {}).pop(mac, None)
        self._dismiss(mac)
        self.m._save()
        self.m.emit({"kind": "changed"})

    def mark_restore(self, mac: str) -> None:
        self.m.data.setdefault("restore_pending", {})[mac] = time.time()
        self.m._save()

    async def restore(self, mac: str) -> dict:
        """Nach der Bluetooth-Einrichtung: Namen (Shelly + HA) und Kanal-Einstellungen aus der Sicherung schreiben."""
        from homeassistant.components import persistent_notification

        b = self.backup(mac)
        if not b:
            raise ValueError("Keine Sicherung vorhanden")
        self.m.data.setdefault("restore_pending", {}).pop(mac, None)
        # Warten, bis HA das Gerät eingerichtet hat und es erreichbar ist
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            dev = self.m.collect_one(mac)
            if dev and dev.get("host") and dev.get("ha"):
                break
            await asyncio.sleep(3)
        else:
            raise client.ShellyError("Gerät ist nach der Einrichtung nicht in HA aufgetaucht")
        cfg = b.get("config") or {}
        ha = b.get("ha") or {}
        done: list[str] = []
        name = ((cfg.get("sys") or {}).get("device") or {}).get("name")
        if name:
            await self.m.set_name(mac, "device", name, ha.get("area_id"))
            done.append("Gerätename")
        chans = [k for k in cfg if re.match(r"^(switch|light|cover|input):\d+$", k)]
        multi = len([k for k in chans if not k.startswith("input:")]) > 1
        for key in chans:
            cname = (cfg.get(key) or {}).get("name")
            if cname and (multi or key.startswith("input:")):
                area = ((ha.get("channels") or {}).get(key) or {}).get("area_id")
                await self.m.set_name(mac, key, cname, area)
                done.append(key)
        changes = []
        ctypes = ((cfg.get("sys") or {}).get("ui_data") or {}).get("consumption_types") or []
        for key in chans:
            comp, idx = key.split(":")
            c = cfg.get(key) or {}
            if comp == "input":
                continue
            if c.get("initial_state") is not None:
                changes.append({"id": "initial_state", "value": c["initial_state"], "channel": int(idx)})
            if c.get("in_mode"):
                changes.append({"id": "in_mode", "value": c["in_mode"], "channel": int(idx)})
            if comp == "switch" and int(idx) < len(ctypes) and ctypes[int(idx)]:
                changes.append({"id": "consumption_type", "value": ctypes[int(idx)], "channel": int(idx)})
        job_id = None
        if changes:
            job_id = self.m.jobs.start({mac: changes}, f"Sicherung einspielen: {name or mac}")
            done.append(f"{len(changes)} Einstellungen")
        self.m.data["new_devices"].pop(mac, None)
        # Als bekannt merken, sonst meldet die Neugeräte-Erkennung es gleich wieder als »neu eingerichtet«
        known = self.m.data.get("known_macs")
        if isinstance(known, list) and mac not in known:
            known.append(mac)
        self._dismiss(mac)
        self.m._save()
        persistent_notification.async_create(
            self.hass, f"**{name or mac}** wurde eingerichtet und die Sicherung eingespielt ({', '.join(done) or 'nichts zu tun'}).",
            title="Shelly wiederhergestellt", notification_id=f"shelly_manager_restored_{mac}")
        self.m.emit({"kind": "changed"})
        return {"done": done, "job_id": job_id}
