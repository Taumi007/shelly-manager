"""Batch-Änderungen, Snapshots, Undo, Neustarts und Umstellung von »Schalter als Licht«-Helfern."""

from __future__ import annotations

import asyncio
import copy
import logging
import secrets
import time
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er

from . import client
from .inventory import ENT_RE
from .model import build_row
from .settings import CATALOG, SettingError, build_calls, plan_device, public, undo_changes

if TYPE_CHECKING:
    from .inventory import Manager

_LOGGER = logging.getLogger(__name__)
MAX_JOBS = 30
MAX_SNAPSHOTS = 5
REBOOT_GAP = 3.0  # Sekunden zwischen Neustarts, damit nicht alle gleichzeitig ins WLAN drängen
REBOOT_FIRST_WAIT = 4   # s bis zur ersten Nachfrage nach dem Neustart
REBOOT_TIMEOUT = 90     # s bis ein Gerät als »nicht zurück« gilt


class Jobs:
    def __init__(self, manager: Manager) -> None:
        self.m = manager
        self.hass = manager.hass
        manager.data.setdefault("jobs", [])
        manager.data.setdefault("snapshots", {})
        # Aufträge, die beim letzten Beenden noch liefen, sind abgebrochen (das Gerät arbeitet ggf. weiter)
        for job in manager.data["jobs"]:
            if job.get("state") == "running":
                job["state"] = "interrupted"
                for dev in job["devices"].values():
                    if dev.get("state") in ("queued", "running", "preparing", "updating", "rebooting"):
                        dev["state"] = "interrupted"

    # ── Planung ───────────────────────────────────────────────────────────────
    def _config(self, dev: dict) -> dict | None:
        return (dev.get("shelly") or {}).get("config")

    def preview(self, macs: list[str], changes: list[dict]) -> list[dict]:
        out = []
        for mac in macs:
            dev = self.m.collect_one(mac)
            if dev is None:
                continue
            items = plan_device(mac, self._config(dev), changes, sleeping=dev.get("sleeping"), gen=dev.get("gen"))
            out.append({"mac": mac, "items": [public(i) for i in items]})
        return out

    def jobs(self) -> list[dict]:
        return self.m.data["jobs"]

    # ── Ausführen ─────────────────────────────────────────────────────────────
    def start(self, per_mac: dict[str, list[dict]], label: str, undo_of: str | None = None) -> str:
        # Validieren, bevor irgendetwas geschrieben wird
        for changes in per_mac.values():
            for ch in changes:
                if ch["id"] not in CATALOG:
                    raise SettingError(f"Unbekannte Einstellung {ch['id']}")
        job_id = secrets.token_hex(4)
        stored = {mac: [{**c, "value": None} if CATALOG[c["id"]].get("secret") else c for c in ch]
                  for mac, ch in per_mac.items()}
        job = {"id": job_id, "ts": time.time(), "label": label, "state": "running", "undo_of": undo_of,
               "changes": stored, "devices": {mac: {"state": "queued", "items": []} for mac in per_mac}}
        self.m.data["jobs"].insert(0, job)
        del self.m.data["jobs"][MAX_JOBS:]
        for mac in per_mac:
            self.m.busy.setdefault(mac, "queued")
        self.m.emit({"kind": "progress", "busy": dict(self.m.busy)})
        self.m.emit({"kind": "job", "job": job})
        self.m._spawn(self._run(job, per_mac))
        return job_id

    async def _run(self, job: dict, per_mac: dict[str, list[dict]]) -> None:
        async def one(mac: str, changes: list[dict]) -> None:
            res = job["devices"][mac]
            dev = self.m.collect_one(mac)
            try:
                if dev is None or not dev.get("host"):
                    res.update(state="error", error="Gerät nicht gefunden")
                    return
                async with self.m.sem:
                    self.m._set_busy(mac, "running")
                    res["state"] = "running"
                    self.m.emit({"kind": "job", "job": job})
                    pw = self.m.password_for(mac)
                    # Frisch einlesen: Planung auf dem echten Stand, Snapshot für den Notfall
                    try:
                        fresh = await client.fetch_all(self.m.session, dev["host"], port=dev.get("port"), password=pw)
                    except client.ShellyError as err:
                        res.update(state="error", error=str(err))
                        return
                    config = fresh["config"]
                    self._snapshot(mac, config)
                    items = plan_device(mac, config, changes, sleeping=dev.get("sleeping"), gen=dev.get("gen"))
                    calls = build_calls(items, config)
                    failed = False
                    for method, params, its in calls:
                        try:
                            await client.rpc(self.m.session, dev["host"], method, params, port=dev.get("port"), password=pw)
                            for it in its:
                                it["ok"] = True
                        except client.ShellyError as err:
                            failed = True
                            for it in its:
                                it.update(ok=False, error=str(err))
                    res["items"] = [public(i) for i in items]
                    await self.m._fetch(mac, dev)
                    status = (self.m.data["cache"].get(mac) or {}).get("status") or {}
                    res["restart_required"] = bool((status.get("sys") or {}).get("restart_required"))
                    res["state"] = "partial" if failed else "done"
            except SettingError as err:
                res.update(state="error", error=str(err))
            finally:
                self.m.busy.pop(mac, None)
                row_dev = self.m.collect_one(mac)
                self.m.emit({"kind": "row", "mac": mac, "busy": dict(self.m.busy),
                             "row": build_row(row_dev, self.m.areas()) if row_dev else None})
                self.m.emit({"kind": "job", "job": job})

        try:
            await asyncio.gather(*(one(mac, ch) for mac, ch in per_mac.items()))
        finally:
            states = {d["state"] for d in job["devices"].values()}
            job["state"] = "done" if states <= {"done"} else "partial"
            self.m._save()
            self.m.emit({"kind": "job", "job": job})
            self.m.emit({"kind": "changed"})

    def _snapshot(self, mac: str, config: dict) -> None:
        snaps = self.m.data["snapshots"].setdefault(mac, [])
        snaps.insert(0, {"ts": time.time(), "config": copy.deepcopy(config)})
        del snaps[MAX_SNAPSHOTS:]

    def undo(self, job_id: str) -> str:
        job = next((j for j in self.m.data["jobs"] if j["id"] == job_id), None)
        if job is None:
            raise SettingError("Auftrag nicht gefunden")
        per_mac = {}
        for mac, res in job["devices"].items():
            changes = undo_changes(res.get("items") or [])
            if changes:
                per_mac[mac] = changes
        if not per_mac:
            raise SettingError("Nichts rückgängig zu machen")
        return self.start(per_mac, f"Rückgängig: {job['label']}", undo_of=job_id)

    # ── Neustart ──────────────────────────────────────────────────────────────
    def reboot(self, macs: list[str], op_id: str | None = None) -> int:
        """Nacheinander neu starten und jeweils warten, bis das Gerät mit neuer Laufzeit zurück ist."""
        targets = [m for m in macs if m not in self.m.busy]
        for mac in targets:
            self.m.busy[mac] = "queued"
        self.m.emit({"kind": "progress", "busy": dict(self.m.busy)})

        async def uptime(dev: dict, pw: str | None) -> float | None:
            try:
                st = await client.rpc(self.m.session, dev["host"], "Sys.GetStatus", port=dev.get("port"), password=pw)
                return (st or {}).get("uptime")
            except client.ShellyError:
                return None

        async def one(mac: str) -> None:
            dev = self.m.collect_one(mac)
            self.m._set_busy(mac, "running")
            self.m.emit_op(op_id, mac, "running")
            try:
                if not dev or not dev.get("host"):
                    raise client.ShellyError("keine IP bekannt")
                pw = self.m.password_for(mac)
                before = await uptime(dev, pw)
                await client.rpc(self.m.session, dev["host"], "Shelly.Reboot", {"delay_ms": 500},
                                 port=dev.get("port"), password=pw)
                await asyncio.sleep(REBOOT_FIRST_WAIT)
                deadline = time.monotonic() + REBOOT_TIMEOUT
                back = False
                while time.monotonic() < deadline:
                    up = await uptime(dev, pw)
                    if up is not None and (before is None or up < before):
                        back = True
                        break
                    await asyncio.sleep(3)
                if not back:
                    raise client.ShellyError(f"nach {REBOOT_TIMEOUT} s nicht zurück")
                await self.m._fetch(mac, dev)
                status = (self.m.data["cache"].get(mac) or {}).get("status") or {}
                if (status.get("sys") or {}).get("restart_required"):
                    self.m.emit_op(op_id, mac, "error", "läuft wieder, meldet aber weiterhin »Neustart nötig«")
                else:
                    self.m.emit_op(op_id, mac, "done")
            except client.ShellyError as err:
                _LOGGER.warning("Neustart %s: %s", mac, err)
                self.m.emit_op(op_id, mac, "error", str(err))
            finally:
                self.m.busy.pop(mac, None)
                self.m._emit_row(mac)
                self.m.emit({"kind": "progress", "busy": dict(self.m.busy)})

        async def run() -> None:
            # Bewusst nacheinander: Geräte melden sich nach dem Neustart sonst gleichzeitig im WLAN an
            for mac in targets:
                await one(mac)
                await asyncio.sleep(REBOOT_GAP)
            self.m._save()
            self.m.emit({"kind": "changed"})

        if targets:
            self.m._spawn(run())
        return len(targets)

    # ── »Schalter als Licht«-Helfer → Consumption Type ────────────────────────
    def helper_info(self, helper_entity_id: str) -> dict:
        ent_reg = er.async_get(self.hass)
        helper = ent_reg.async_get(helper_entity_id)
        if helper is None or helper.platform != "switch_as_x":
            raise SettingError("Kein »Schalter als …«-Helfer")
        ce = self.hass.config_entries.async_get_entry(helper.config_entry_id)
        source = ce.options.get("entity_id") if ce else None
        src = ent_reg.async_get(source) if source else None
        if src is None or src.platform != "shelly":
            raise SettingError("Quelle ist kein Shelly-Schalter")
        m = ENT_RE.search(src.unique_id or "")
        if not m or not m.group(1).startswith("switch:"):
            raise SettingError("Shelly-Kanal nicht erkannt")
        shelly_entry = self.hass.config_entries.async_get_entry(src.config_entry_id)
        mac = (shelly_entry.unique_id or "").replace(":", "").upper()
        return {"helper": helper_entity_id, "helper_entry_id": ce.entry_id, "helper_name": helper.name or helper.original_name,
                "helper_area_id": helper.area_id, "helper_icon": helper.icon,
                "helper_labels": sorted(helper.labels or []), "helper_aliases": sorted(a for a in (helper.aliases or []) if isinstance(a, str)),
                "source": source,
                "source_unique_id": src.unique_id, "shelly_entry_id": shelly_entry.entry_id, "mac": mac,
                "key": m.group(1), "channel": int(m.group(1).split(":")[1]), "target_domain": ce.options.get("target_domain")}

    async def convert_helper(self, helper_entity_id: str, remove_switch: bool) -> dict:
        info = self.helper_info(helper_entity_id)
        if info["target_domain"] not in (None, "light"):
            raise SettingError("Nur Helfer »als Licht« lassen sich über den Gerätetyp ersetzen")
        steps: list[str] = []
        mac = info["mac"]
        dev = self.m.collect_one(mac)
        if dev is None or not dev.get("host"):
            raise SettingError("Shelly nicht erreichbar")
        pw = self.m.password_for(mac)
        self.m._set_busy(mac, "running")
        try:
            fresh = await client.fetch_all(self.m.session, dev["host"], port=dev.get("port"), password=pw)
            self._snapshot(mac, fresh["config"])
            items = plan_device(mac, fresh["config"], [{"id": "consumption_type", "value": "light", "channel": info["channel"]}])
            for method, params, _ in build_calls(items, fresh["config"]):
                await client.rpc(self.m.session, dev["host"], method, params, port=dev.get("port"), password=pw)
            steps.append(f"Gerätetyp von {info['key']} im Shelly auf »light« gesetzt")

            # Helfer entfernen: gibt seine Entity-ID frei, damit die neue Licht-Entity sie übernehmen kann
            await self.hass.config_entries.async_remove(info["helper_entry_id"])
            steps.append(f"Helfer {helper_entity_id} entfernt")
            await self.hass.config_entries.async_reload(info["shelly_entry_id"])
            steps.append("Shelly-Integration für das Gerät neu geladen")

            ent_reg = er.async_get(self.hass)
            light_id = None
            for _ in range(40):
                light_id = ent_reg.async_get_entity_id("light", "shelly", info["source_unique_id"])
                if light_id:
                    break
                await asyncio.sleep(0.5)
            if light_id is None:
                steps.append("Neue Licht-Entity nicht gefunden – bitte in HA prüfen")
                return {"ok": False, "steps": steps}
            updates: dict[str, Any] = {}
            if light_id != helper_entity_id and ent_reg.async_get(helper_entity_id) is None:
                updates["new_entity_id"] = helper_entity_id
            if info["helper_name"]:
                updates["name"] = info["helper_name"]
            if info["helper_area_id"]:
                updates["area_id"] = info["helper_area_id"]
            if info["helper_icon"]:
                updates["icon"] = info["helper_icon"]
            if info["helper_labels"]:
                updates["labels"] = set(info["helper_labels"]) | set(ent_reg.async_get(light_id).labels or [])
            if info["helper_aliases"]:
                updates["aliases"] = set(info["helper_aliases"])
            if updates:
                ent_reg.async_update_entity(light_id, **updates)
            final_id = updates.get("new_entity_id", light_id)
            steps.append(f"Neue Licht-Entity {final_id} übernimmt ID, Name, Bereich, Symbol, Labels und Aliasse des Helfers")
            if ent_reg.async_get(info["source"]) is None:
                steps.append(f"Schalter-Entity {info['source']} hat die Shelly-Integration bereits entfernt")
            elif remove_switch:
                ent_reg.async_remove(info["source"])
                steps.append(f"Verwaiste Schalter-Entity {info['source']} entfernt")
            await self.m._fetch(mac, dev)
            return {"ok": True, "steps": steps, "entity_id": final_id}
        finally:
            self.m.busy.pop(mac, None)
            self.m._save()
            self.m.emit({"kind": "changed"})
            self.m.emit({"kind": "progress", "busy": dict(self.m.busy)})


# ── Firmware ──────────────────────────────────────────────────────────────────

FW_RPC = {"switch": "Switch", "light": "Light", "rgb": "RGB", "rgbw": "RGBW", "cct": "CCT"}
FW_FIRST_WAIT = 15      # s nach Update-Start, bevor nach der neuen Version gefragt wird
FW_POLL = 10            # s zwischen Abfragen während des Updates (nur GET /shelly, ohne Auth)
FW_TIMEOUT = 25 * 60    # s bis ein Update als fehlgeschlagen gilt – Downloads dauern teils über 10 Minuten


class Firmware:
    """Gestaffelte Firmware-Updates mit Schutz der Schaltzustände."""

    def __init__(self, manager: Manager) -> None:
        self.m = manager
        self._cancel: dict[str, bool] = {}

    def _names(self, dev: dict) -> dict[str, str]:
        """Anzeigenamen der Ausgänge (Einkanaler: Gerätename)."""
        row = build_row(dev, self.m.areas())
        outs = [c for c in row["channels"] if c["component"] in FW_RPC]
        if len(outs) == 1:
            return {outs[0]["key"]: row["name"]}
        return {c["key"]: (c.get("names") or {}).get("ha_full") or c.get("name") or c["key"] for c in outs}

    def preview(self, macs: list[str] | None, stage: str) -> list[dict]:
        from .firmware import preview
        out = []
        devices = [self.m.collect_one(m) for m in macs] if macs else self.m.collect()
        for dev in devices:
            if dev is None or dev.get("gen") == 1:
                continue
            sh = dev.get("shelly") or {}
            p = preview(dev["mac"], sh.get("info"), sh.get("config"), sh.get("status"), stage,
                        sleeping=dev.get("sleeping"), names=self._names(dev))
            if macs or p["state"] == "update":
                out.append(p)
        return out

    def check(self, macs: list[str] | None) -> int:
        """Geräte gestaffelt nach Updates suchen lassen (Shelly.CheckForUpdate)."""
        devices = [d for d in ([self.m.collect_one(m) for m in macs] if macs else self.m.collect())
                   if d and d.get("host") and d.get("gen") != 1 and not d.get("sleeping") and d["mac"] not in self.m.busy]
        for d in devices:
            self.m.busy[d["mac"]] = "queued"
        self.m.emit({"kind": "progress", "busy": dict(self.m.busy)})

        async def one(dev: dict) -> None:
            mac = dev["mac"]
            try:
                async with self.m.sem:
                    self.m._set_busy(mac, "running")
                    try:
                        await client.rpc(self.m.session, dev["host"], "Shelly.CheckForUpdate", port=dev.get("port"),
                                         password=self.m.password_for(mac))
                    except client.ShellyError:
                        pass
                    await self.m._fetch(mac, dev)
            finally:
                self.m.busy.pop(mac, None)
                fresh = self.m.collect_one(mac)
                self.m.emit({"kind": "row", "mac": mac, "busy": dict(self.m.busy),
                             "row": build_row(fresh, self.m.areas()) if fresh else None})

        async def run() -> None:
            await asyncio.gather(*(one(d) for d in devices))
            self.m._save()
            self.m.emit({"kind": "changed"})

        if devices:
            self.m._spawn(run())
        return len(devices)

    def start(self, macs: list[str], stage: str, concurrency: int, gap: float, keep_state: bool,
              stop_on_error: bool) -> str:
        job_id = secrets.token_hex(4)
        job = {"id": job_id, "ts": time.time(), "kind": "firmware", "label": f"Firmware-Update ({stage})",
               "state": "running", "options": {"stage": stage, "concurrency": concurrency, "gap": gap,
                                                "keep_state": keep_state, "stop_on_error": stop_on_error},
               "devices": {mac: {"state": "queued"} for mac in macs}}
        self.m.data["jobs"].insert(0, job)
        del self.m.data["jobs"][MAX_JOBS:]
        self._cancel[job_id] = False
        for mac in macs:
            self.m.busy.setdefault(mac, "queued")
        self.m.emit({"kind": "progress", "busy": dict(self.m.busy)})
        self.m.emit({"kind": "job", "job": job})
        self.m._spawn(self._run(job, macs))
        return job_id

    def cancel(self, job_id: str) -> None:
        self._cancel[job_id] = True

    async def _run(self, job: dict, macs: list[str]) -> None:
        opts = job["options"]
        sem = asyncio.Semaphore(max(1, min(4, opts["concurrency"])))
        start_lock = asyncio.Lock()
        last_start = 0.0

        async def one(mac: str) -> None:
            nonlocal last_start
            res = job["devices"][mac]
            try:
                async with sem:
                    if self._cancel.get(job["id"]):
                        res["state"] = "cancelled"
                        return
                    # Abstand zwischen zwei Update-Starts: nicht alle laden gleichzeitig herunter
                    async with start_lock:
                        wait = opts["gap"] - (time.monotonic() - last_start)
                        if last_start and wait > 0:
                            await asyncio.sleep(wait)
                        if self._cancel.get(job["id"]):
                            res["state"] = "cancelled"
                            return
                        last_start = time.monotonic()
                    self.m._set_busy(mac, "running")
                    await self._update_one(job, mac, res, opts)
                    if res["state"] == "failed" and opts["stop_on_error"]:
                        self._cancel[job["id"]] = True
            except Exception as err:  # noqa: BLE001 – Fehler pro Gerät melden, Rest läuft weiter
                _LOGGER.exception("Firmware-Update %s", mac)
                res.update(state="failed", error=str(err))
                if opts["stop_on_error"]:
                    self._cancel[job["id"]] = True
            finally:
                self.m.busy.pop(mac, None)
                fresh = self.m.collect_one(mac)
                self.m.emit({"kind": "row", "mac": mac, "busy": dict(self.m.busy),
                             "row": build_row(fresh, self.m.areas()) if fresh else None})
                self.m.emit({"kind": "job", "job": job})

        try:
            await asyncio.gather(*(one(m) for m in macs))
        finally:
            states = {d["state"] for d in job["devices"].values()}
            job["state"] = "done" if states <= {"done", "skipped"} else "partial"
            self._cancel.pop(job["id"], None)
            self.m._save()
            self.m.emit({"kind": "job", "job": job})
            self.m.emit({"kind": "changed"})

    async def _update_one(self, job: dict, mac: str, res: dict, opts: dict) -> None:
        from .firmware import available, reboot_effects
        emit = lambda: self.m.emit({"kind": "job", "job": job})  # noqa: E731
        dev = self.m.collect_one(mac)
        if dev is None or not dev.get("host"):
            res.update(state="failed", error="Gerät nicht gefunden")
            return
        host, port, pw = dev["host"], dev.get("port"), self.m.password_for(mac)
        res["state"] = "preparing"
        emit()
        fresh = await client.fetch_all(self.m.session, host, port=port, password=pw)
        target = available(fresh["status"], opts["stage"])
        old = (fresh["info"] or {}).get("ver")
        res.update(old=old, target=target)
        if not target:
            res.update(state="skipped", reason="bereits aktuell")
            return

        # Schaltzustände schützen: betroffene Ausgänge für die Dauer des Updates auf »letzter Zustand«
        restore: list[tuple[str, int, Any]] = []
        if opts["keep_state"]:
            for e in reboot_effects(fresh["config"], fresh["status"]):
                if e["change"]:
                    comp, idx = e["key"].split(":")
                    method = f"{FW_RPC[comp]}.SetConfig"
                    await client.rpc(self.m.session, host, method, {"id": int(idx), "config": {"initial_state": "restore_last"}},
                                     port=port, password=pw)
                    restore.append((method, int(idx), e["initial_state"]))
            res["protected"] = len(restore)

        res["state"] = "updating"
        emit()
        try:
            await client.rpc(self.m.session, host, "Shelly.Update", {"stage": opts["stage"]}, port=port, password=pw)
        except client.AuthRequired:
            raise
        except client.ShellyError as err:
            # Manche Geräte antworten erst nach dem Download – Zeitüberschreitung heißt nicht »fehlgeschlagen«
            if "nicht erreichbar" not in str(err):
                raise
            res.setdefault("warnings", []).append("Update-Befehl ohne Antwort – warte auf neue Version")
        res["state"] = "rebooting"
        emit()
        await asyncio.sleep(FW_FIRST_WAIT)
        started = time.monotonic()
        deadline = started + FW_TIMEOUT
        new = None
        last_uptime = (fresh["status"].get("sys") or {}).get("uptime")
        rebooted = False
        polls = 0
        while time.monotonic() < deadline:
            data = await client.probe(self.m.session, host, port)
            ver = (data or {}).get("ver")
            if ver and ver != old:
                new = ver
                break
            polls += 1
            # Jede dritte Runde die Laufzeit prüfen: sinkt sie, hat das Gerät neu gestartet
            if data and polls % 3 == 0:
                try:
                    up = ((await client.rpc(self.m.session, host, "Sys.GetStatus", port=port, password=pw)) or {}).get("uptime")
                except client.ShellyError:
                    up = None
                if isinstance(up, (int, float)) and isinstance(last_uptime, (int, float)):
                    if up < last_uptime:
                        rebooted = True
                    last_uptime = up
            await asyncio.sleep(FW_POLL)

        # Ursprüngliches Verhalten wiederherstellen – auch wenn das Update nicht durchlief
        for method, idx, init in restore:
            for attempt in range(3):
                try:
                    await client.rpc(self.m.session, host, method, {"id": idx, "config": {"initial_state": init}},
                                     port=port, password=pw)
                    break
                except client.ShellyError:
                    await asyncio.sleep(5 * (attempt + 1))
            else:
                res.setdefault("warnings", []).append(f"initial_state von Kanal {idx} nicht zurückgesetzt ({init})")
        await self.m._fetch(mac, dev)
        not_started = not rebooted
        if new:
            res.update(state="done", new=new)
        elif not_started:
            res.update(state="failed", error=f"Kein Neustart in {FW_TIMEOUT // 60} Minuten – Download hängt oder Version "
                                             "ist für dieses Gerät noch nicht freigegeben; später erneut versuchen")
        elif rebooted:
            res.update(state="failed", error="Gerät hat neu gestartet, meldet aber weiter die alte Version")
        else:
            res.update(state="failed", error=f"Keine neue Version nach {FW_TIMEOUT // 60} Minuten")
