"""Fallback-Modus: Shelly-Script übernimmt Tasten, wenn Home Assistant nicht erreichbar ist.

Ablauf:
- Pro Gerät eine Liste von Belegungen (Doppeltaster / Einzeltaster / Schalter auf beliebigen Eingängen)
  mit Zielen (Hue-Lampe/-Raum inkl. Szenen, andere Shellys, eigener Ausgang).
- Der Manager erzeugt daraus ein Script (fallback_script.js + eingebettete Konfiguration) und lädt es hoch.
- Herzschlag: HA schreibt einmal pro Minute einen Zeitstempel in den KVS des Shellys. Bleibt er aus,
  übernimmt das Script. Der Shelly fragt selbst nie nach – keine zusätzliche Last im Netz.
- Ändert sich die IP eines Ziel-Shellys, wird das Script mit der neuen Adresse neu hochgeladen.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.event import async_track_time_interval

from . import client

if TYPE_CHECKING:
    from .inventory import Manager

_LOGGER = logging.getLogger(__name__)
SCRIPT_NAME = "shelly-manager-fallback"
TEMPLATE = Path(__file__).parent / "fallback_script.js"
HEARTBEAT = 60          # s zwischen zwei Herzschlägen
TIMEOUT = 180           # s ohne Herzschlag → Script übernimmt (nur wenn der WebSocket nie verbunden war)
GRACE = 15              # s getrennter WebSocket → Script übernimmt (greift auch bei HA-Neustarts)
SYNC_EVERY = 5          # jeder n-te Herzschlag prüft, ob sich Ziel-Adressen geändert haben
CHUNK = 1000            # Zeichen pro Script.PutCode
TYPE = {"double": "d", "single": "s", "switch": "w"}


class FallbackError(Exception):
    pass


class Fallback:
    def __init__(self, manager: Manager) -> None:
        self.m = manager
        self.hass = manager.hass
        self.cfg: dict[str, dict] = manager.data.setdefault("fallback", {})
        self._template: str | None = None
        self._tick = 0
        self._sem = asyncio.Semaphore(4)
        self._locks: dict[str, asyncio.Lock] = {}

    def async_start(self) -> None:
        self.m._unsubs.append(async_track_time_interval(self.hass, self._heartbeat, timedelta(seconds=HEARTBEAT),
                                                        name="shelly_manager fallback heartbeat"))

    # ── Hue ───────────────────────────────────────────────────────────────────
    def hue_bridge(self) -> dict | None:
        for entry in self.hass.config_entries.async_entries("hue"):
            if entry.state is ConfigEntryState.LOADED and entry.data.get("api_key") and entry.data.get("host"):
                return {"host": entry.data["host"], "key": entry.data["api_key"], "title": entry.title}
        return None

    async def hue_targets(self) -> dict:
        """Lampen, Räume/Zonen (grouped_light) und Szenen direkt von der Bridge (CLIP v2)."""
        bridge = self.hue_bridge()
        if bridge is None:
            return {"available": False}
        headers = {"hue-application-key": bridge["key"]}
        out: dict[str, Any] = {}
        async with aiohttp.ClientSession() as session:
            for rtype in ("light", "grouped_light", "room", "zone", "scene"):
                try:
                    async with session.get(f"https://{bridge['host']}/clip/v2/resource/{rtype}", headers=headers,
                                           ssl=False, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        out[rtype] = (await resp.json()).get("data", [])
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                    raise FallbackError(f"Hue-Bridge nicht erreichbar: {err}") from err
        # Das Script nutzt die lokale v1-API (HTTP ohne TLS – auf dem Shelly deutlich schneller),
        # deshalb zu jeder Ressource die v1-Kennung (»/lights/12«, »/groups/3«, »/scenes/abc«)
        gl_v1 = {g["id"]: g.get("id_v1") for g in out.get("grouped_light", [])}
        group_of = {}  # room/zone-id → grouped_light-id
        room_of_device = {}
        groups = []
        for kind in ("room", "zone"):
            for g in out.get(kind, []):
                gl = next((s["rid"] for s in g.get("services", []) if s.get("rtype") == "grouped_light"), None)
                if kind == "room":
                    for ch in g.get("children", []):
                        room_of_device[ch.get("rid")] = g["metadata"]["name"]
                if gl and gl_v1.get(gl):
                    group_of[g["id"]] = gl
                    groups.append({"id": gl, "rtype": "grouped_light", "v1": gl_v1[gl],
                                   "name": f"{g['metadata']['name']} ({'Raum' if kind == 'room' else 'Zone'})"})
        lights = [{"id": li["id"], "rtype": "light", "v1": li.get("id_v1"),
                   "name": f"{room_of_device.get(li.get('owner', {}).get('rid'), '')} {li['metadata']['name']}".strip()}
                  for li in out.get("light", []) if li.get("id_v1")]
        scenes = [{"id": sc["id"], "v1": sc.get("id_v1"), "name": sc["metadata"]["name"],
                   "group": group_of.get(sc.get("group", {}).get("rid")),
                   "group_v1": gl_v1.get(group_of.get(sc.get("group", {}).get("rid")))}
                  for sc in out.get("scene", []) if sc.get("id_v1")]
        return {"available": True, "bridge": bridge["title"],
                "targets": sorted(groups, key=lambda x: x["name"]) + sorted(lights, key=lambda x: x["name"]),
                "scenes": sorted(scenes, key=lambda x: x["name"])}

    # ── Konfiguration ─────────────────────────────────────────────────────────
    def get(self, mac: str) -> dict:
        return self.cfg.get(mac) or {"enabled": False, "bindings": []}

    def compile(self, mac: str) -> dict:
        """Belegungen → kompakte Script-Konfiguration (Ziel-IPs aufgelöst)."""
        conf = self.get(mac)
        hue = None
        out_b = []
        for n, b in enumerate(conf["bindings"]):
            targets = []
            for t in b.get("targets", []):
                if t["kind"] == "hue":
                    hue = hue or self.hue_bridge()
                    if hue is None:
                        raise FallbackError("Hue-Integration nicht geladen")
                    if not t.get("v1"):
                        raise FallbackError(f"Hue-Ziel »{t.get('name')}« bitte neu auswählen")
                    # »/lights/12« → lights/12/state, »/groups/3« → groups/3/action
                    path = t["v1"].strip("/")
                    targets.append({"k": "h", "p": path + ("/state" if path.startswith("lights/") else "/action")})
                elif t["kind"] == "shelly":
                    dev = self.m.collect_one(t["mac"])
                    if not dev or not dev.get("host"):
                        raise FallbackError(f"Ziel-Shelly {t.get('name') or t['mac']} hat keine bekannte IP")
                    targets.append({"k": "y", "ip": dev["host"], "ch": int(t.get("channel", 0))})
                elif t["kind"] == "local":
                    targets.append({"k": "l", "ch": int(t.get("channel", 0))})
            # Szenen: [Gruppe, Szene] in v1, Abruf über groups/<id>/action {"scene": …}
            scenes = []
            for sc in b.get("scenes", [])[:3]:  # Reihenfolge = Zyklus, höchstens drei
                if isinstance(sc, dict) and sc.get("v1") and sc.get("group_v1"):
                    scenes.append([sc["group_v1"].strip("/") + "/action", sc["v1"].rsplit("/", 1)[-1]])
            out_b.append({"n": n, "t": TYPE[b["type"]], "i": [int(i) for i in b["inputs"]], "dim": bool(b.get("dim", True)),
                          "g": targets, "sc": scenes})
        cfg: dict[str, Any] = {"timeout": TIMEOUT, "grace": GRACE, "ws": True, "b": out_b}
        if hue:
            # v1-API über HTTPS (die Bridge leitet HTTP um); ein Befehl dauert auf dem Shelly ca. 1,3 s
            cfg["hue"] = "https://" + hue["host"] + "/api/" + hue["key"] + "/"
        return cfg

    def script(self, mac: str) -> tuple[str, str]:
        # Bei jedem Erzeugen frisch lesen (läuft im Executor), damit Script-Updates ohne Neustart greifen
        template = TEMPLATE.read_text(encoding="utf-8")
        cfg = json.dumps(self.compile(mac), separators=(",", ":"))
        code = template.replace("__CFG__", cfg)
        return code, hashlib.sha1(code.encode()).hexdigest()[:12]

    # ── Gerät ─────────────────────────────────────────────────────────────────
    async def _rpc(self, mac: str, method: str, params: dict | None = None) -> Any:
        dev = self.m.collect_one(mac)
        if not dev or not dev.get("host"):
            raise FallbackError("Gerät nicht erreichbar")
        return await client.rpc(self.m.session, dev["host"], method, params, port=dev.get("port"),
                                password=self.m.password_for(mac))

    async def _script_id(self, mac: str, create: bool) -> int | None:
        res = await self._rpc(mac, "Script.List")
        for s in (res or {}).get("scripts", []):
            if s.get("name") == SCRIPT_NAME:
                return s["id"]
        if not create:
            return None
        return (await self._rpc(mac, "Script.Create", {"name": SCRIPT_NAME}))["id"]

    async def save(self, mac: str, bindings: list[dict], enabled: bool = True) -> dict:
        self.cfg[mac] = {"enabled": enabled, "bindings": bindings}
        self.m._save()
        if not enabled or not bindings:
            await self.remove(mac, keep_config=True)
            return {"deployed": False}
        return await self.deploy(mac)

    def ws_url(self) -> str:
        """Adresse, unter der HA ausgehende Shelly-WebSockets annimmt (wie die Shelly-Integration sie bildet)."""
        try:
            from homeassistant.components.shelly.utils import get_rpc_ws_url
            url = get_rpc_ws_url(self.hass)
            if url:
                return url
        except Exception:  # noqa: BLE001 – Fallback auf eigene Ermittlung
            pass
        from homeassistant.helpers.network import get_url
        base = get_url(self.hass, allow_external=False, allow_cloud=False, prefer_external=False, require_ssl=False)
        return base.replace("http://", "ws://").replace("https://", "wss://").rstrip("/") + "/api/shelly/ws"

    async def _ensure_ws(self, mac: str) -> bool:
        """Ausgehenden WebSocket zu HA einschalten; vorherige Einstellung merken. True = Neustart nötig."""
        url = self.ws_url()
        cur = await self._rpc(mac, "Ws.GetConfig") or {}
        if cur.get("enable") and cur.get("server") == url:
            return False
        self.cfg[mac].setdefault("prev_ws", {"enable": bool(cur.get("enable")), "server": cur.get("server")})
        res = await self._rpc(mac, "Ws.SetConfig", {"config": {"enable": True, "server": url}}) or {}
        return bool(res.get("restart_required"))

    async def deploy(self, mac: str) -> dict:
        # Nie zwei Uploads gleichzeitig auf dasselbe Gerät (sonst mischen sich die PutCode-Teilstücke)
        lock = self._locks.setdefault(mac, asyncio.Lock())
        async with lock:
            return await self._deploy(mac)

    async def _deploy(self, mac: str) -> dict:
        code, digest = await self.hass.async_add_executor_job(self.script, mac)
        self.m._set_busy(mac, "running")
        try:
            sid = await self._script_id(mac, create=True)
            try:
                await self._rpc(mac, "Script.Stop", {"id": sid})
            except client.ShellyError:
                pass
            for i in range(0, len(code), CHUNK):
                await self._rpc(mac, "Script.PutCode", {"id": sid, "code": code[i:i + CHUNK], "append": i > 0})
            await self._rpc(mac, "Script.SetConfig", {"id": sid, "config": {"enable": True}})
            await self._rpc(mac, "Script.Start", {"id": sid})
            # Kurz prüfen, ob das Script wirklich läuft (z. B. Syntaxfehler nach gestörtem Upload)
            await asyncio.sleep(1)
            stat = await self._rpc(mac, "Script.GetStatus", {"id": sid}) or {}
            if not stat.get("running"):
                raise client.ShellyError(f"Script startet nicht: {(stat.get('error_msg') or stat.get('errors') or '?')!s:.120}")
            await self._rpc(mac, "KVS.Set", {"key": "shm_hb", "value": str(int(time.time()))})
            restart = await self._ensure_ws(mac)
            self.cfg[mac].update(script_id=sid, hash=digest, deployed_at=time.time(), error=None)
            if restart:
                await self._rpc(mac, "Shelly.Reboot", {"delay_ms": 500})
            return {"deployed": True, "script_id": sid, "size": len(code), "rebooted": restart}
        except client.ShellyError as err:
            self.cfg[mac]["error"] = str(err)
            raise FallbackError(f"Hochladen fehlgeschlagen: {err}") from err
        finally:
            self.m._save()
            self.m.busy.pop(mac, None)
            self.m._emit_row(mac)

    async def remove(self, mac: str, keep_config: bool = False) -> None:
        sid = await self._script_id(mac, create=False)
        if sid is not None:
            for method in ("Script.Stop", "Script.Delete"):
                try:
                    await self._rpc(mac, method, {"id": sid})
                except client.ShellyError:
                    pass
        for key in ("shm_hb", "shm_force"):
            try:
                await self._rpc(mac, "KVS.Delete", {"key": key})
            except client.ShellyError:
                pass
        prev = (self.cfg.get(mac) or {}).pop("prev_ws", None)
        if prev is not None:
            try:
                await self._rpc(mac, "Ws.SetConfig", {"config": prev})
            except client.ShellyError as err:
                _LOGGER.warning("WebSocket-Einstellung von %s nicht zurückgesetzt: %s", mac, err)
        if keep_config and mac in self.cfg:
            self.cfg[mac].update(enabled=False, script_id=None, hash=None)
        else:
            self.cfg.pop(mac, None)
        self.m._save()

    async def test(self, mac: str, input_idx: int, event: str) -> Any:
        sid = (self.cfg.get(mac) or {}).get("script_id")
        if sid is None:
            raise FallbackError("Fallback ist auf diesem Gerät nicht eingerichtet")
        return await self._rpc(mac, "Script.Eval", {"id": sid, "code": f"test({int(input_idx)},{json.dumps(event)})"})

    # ── Herzschlag und Adress-Abgleich ────────────────────────────────────────
    async def _heartbeat(self, _now=None) -> None:
        self._tick += 1
        active = [mac for mac, c in self.cfg.items() if c.get("enabled") and c.get("script_id") is not None]
        stamp = str(int(time.time()))

        async def beat(mac: str) -> None:
            async with self._sem:
                try:
                    await self._rpc(mac, "KVS.Set", {"key": "shm_hb", "value": stamp})
                    self.cfg[mac]["last_beat"] = time.time()
                except (client.ShellyError, FallbackError) as err:
                    _LOGGER.debug("Herzschlag an %s fehlgeschlagen: %s", mac, err)
                if self._tick % SYNC_EVERY == 0:
                    try:
                        _code, digest = await self.hass.async_add_executor_job(self.script, mac)
                        if digest != self.cfg[mac].get("hash") and not self._locks.get(mac, asyncio.Lock()).locked():
                            _LOGGER.info("Fallback-Script für %s wird aktualisiert (Ziel-Adresse geändert)", mac)
                            await self.deploy(mac)
                    except (FallbackError, client.ShellyError) as err:
                        self.cfg[mac]["error"] = str(err)

        await asyncio.gather(*(beat(m) for m in active))

    async def live(self, mac: str) -> dict:
        out: dict[str, Any] = {"ws_connected": None, "always": None}
        try:
            out["ws_connected"] = (await self._rpc(mac, "Ws.GetStatus") or {}).get("connected")
            kv = await self._rpc(mac, "KVS.Get", {"key": "shm_force"})
            out["always"] = (kv or {}).get("value") == "1"
        except client.ShellyError as err:
            # KVS.Get meldet einen Fehler, wenn der Schlüssel fehlt → nicht dauerhaft aktiv
            if out["ws_connected"] is not None:
                out["always"] = False
            _LOGGER.debug("Fallback-Status %s: %s", mac, err)
        except FallbackError:
            pass
        return out

    async def set_always(self, mac: str, on: bool) -> None:
        """Fallback dauerhaft aktiv: das Script übernimmt immer, unabhängig von HA (KVS »shm_force«)."""
        if on:
            await self._rpc(mac, "KVS.Set", {"key": "shm_force", "value": "1"})
        else:
            try:
                await self._rpc(mac, "KVS.Delete", {"key": "shm_force"})
            except client.ShellyError:
                pass
        # Sofort wirksam machen (das Script liest den KVS sonst nur alle 20 s)
        sid = (self.cfg.get(mac) or {}).get("script_id")
        if sid is not None:
            try:
                await self._rpc(mac, "Script.Eval", {"id": sid, "code": f"FORCE={'true' if on else 'false'}"})
            except client.ShellyError:
                pass
        if mac in self.cfg:
            self.cfg[mac]["always"] = on
            self.m._save()

    def status(self, mac: str) -> dict:
        c = self.cfg.get(mac) or {}
        return {"enabled": bool(c.get("enabled")), "bindings": c.get("bindings", []), "script_id": c.get("script_id"),
                "always_cfg": bool(c.get("always")),
                "deployed_at": c.get("deployed_at"), "last_beat": c.get("last_beat"), "error": c.get("error")}
