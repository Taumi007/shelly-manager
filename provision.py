"""Bluetooth-Einrichtung neuer Shellys über den Config-Flow der Shelly-Integration.

Die Shelly-Integration findet unprovisionierte Geräte per Bluetooth (auch über Proxys), scannt WLANs,
überträgt SSID/Passwort, wartet auf das Gerät im Netz und sichert es ab (Geräte-AP aus, BLE-RPC aus).
Der Manager füllt diesen Flow nur mit den gespeicherten Zugangsdaten – keine eigene BLE-Implementierung.

Zusätzlich merkt er sich neu hinzugekommene Geräte, damit das Panel sie zur Einrichtung anbietet.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_AUTO_PROVISION, CONF_WIFI_PASSWORD, CONF_WIFI_SSID, SHELLY

if TYPE_CHECKING:
    from .inventory import Manager

_LOGGER = logging.getLogger(__name__)
FLOW_TIMEOUT = 240  # s für Scan, Übertragung und das Wiederfinden im Netz
NEW_KEEP_DAYS = 30
TICK = 10           # s: so schnell werden neue Bluetooth-Funde automatisch eingerichtet
RETRY_FAILED = 600  # s: fehlgeschlagene automatische Einrichtung danach erneut versuchen
SCAN_RETRIES = 2    # WLAN-Scan über Bluetooth so oft wiederholen
HOLD_DAYS = 30      # zurückgesetzte Geräte so lange nicht automatisch einrichten


class Provisioner:
    def __init__(self, manager: Manager) -> None:
        self.m = manager
        self.hass = manager.hass
        self.running: dict[str, dict[str, Any]] = {}  # flow_id → Zustand
        self._failed: dict[str, float] = {}           # flow_id → Zeitpunkt des Fehlschlags
        manager.data.setdefault("known_macs", None)
        manager.data.setdefault("new_devices", {})
        manager.data.setdefault("reset_hold", {})     # mac → Zeitpunkt des Zurücksetzens
        if "auto_provision" not in manager.data:
            # Einmalig aus der früheren Option übernehmen; danach gilt der Schalter im Panel (Store)
            manager.data["auto_provision"] = bool(manager.entry.options.get(CONF_AUTO_PROVISION))

    # ── Optionen ──────────────────────────────────────────────────────────────
    @property
    def ssid(self) -> str:
        return self.m.entry.options.get(CONF_WIFI_SSID) or ""

    @property
    def password(self) -> str:
        return self.m.entry.options.get(CONF_WIFI_PASSWORD) or ""

    @property
    def auto(self) -> bool:
        return self.auto_enabled and self.has_wifi

    @property
    def auto_enabled(self) -> bool:
        return bool(self.m.data.get("auto_provision"))

    def set_auto(self, on: bool) -> None:
        self.m.data["auto_provision"] = bool(on)
        self.m._save()
        self.m.emit({"kind": "changed"})
        if on:
            self._tick()

    def hold(self, mac: str) -> None:
        """Gerät gerade zurückgesetzt: nicht automatisch wieder einrichten (manuell geht weiterhin)."""
        self.m.data["reset_hold"][mac] = time.time()
        self.m._save()

    def release(self, mac: str) -> None:
        if self.m.data["reset_hold"].pop(mac, None) is not None:
            self.m._save()

    @property
    def has_wifi(self) -> bool:
        """Standard-WLAN aus den Optionen oder aus der WLAN-Vorgabe (ggf. secrets.yaml)."""
        return bool(self.ssid) or bool(((self.m.data.get("presets") or {}).get("wifi") or {}).get("ssid"))

    def async_start(self) -> None:
        self.m._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=TICK),
                                                        name="shelly_manager provision"))

    # ── Bluetooth-Funde ───────────────────────────────────────────────────────
    def candidates(self) -> list[dict]:
        out = []
        for flow in self.hass.config_entries.flow.async_progress_by_handler(SHELLY):
            ctx = flow.get("context") or {}
            if ctx.get("source") != "bluetooth":
                continue
            ph = ctx.get("title_placeholders") or {}
            out.append({"flow_id": flow["flow_id"], "name": ph.get("name") or ctx.get("unique_id"),
                        "mac": (ctx.get("unique_id") or "").replace(":", "").upper(), "step": flow.get("step_id"),
                        "held": (ctx.get("unique_id") or "").replace(":", "").upper() in self.m.data["reset_hold"],
                        "backup": self.m.reset.summary((ctx.get("unique_id") or "").replace(":", "").upper()) if self.m.reset else None,
                        "state": self.running.get(flow["flow_id"], {}).get("state"),
                        "error": self.running.get(flow["flow_id"], {}).get("error")})
        return out

    @callback
    def _tick(self, _now=None) -> None:
        self.track_new()
        for c in self.candidates():
            if c["backup"] and not self.running.get(c["flow_id"]):
                self.m.reset.notify(c["mac"], c["name"])
        if not self.auto:
            return
        now = time.time()
        for c in self.candidates():
            fid = c["flow_id"]
            if c["held"] or now - self._failed.get(fid, 0) < RETRY_FAILED:
                continue
            if self.running.get(fid, {}).get("state") in ("connecting", "scanning", "sending", "joining"):
                continue
            if self.running.get(fid, {}).get("state") == "done":
                continue
            _LOGGER.info("Richte %s automatisch per Bluetooth ein", c["name"])
            self.start(fid, auto=True)

    def start(self, flow_id: str, ssid: str | None = None, password: str | None = None, auto: bool = False) -> None:
        if not auto:
            # Manuell gestartet: Sperre nach dem Zurücksetzen aufheben
            for c in self.candidates():
                if c["flow_id"] == flow_id and c["mac"]:
                    self.release(c["mac"])
            self._failed.pop(flow_id, None)
        self.running[flow_id] = {"state": "connecting", "ts": time.time(), "auto": auto}
        self.m.emit({"kind": "provision", "flow_id": flow_id, "state": "connecting"})
        self.m._spawn(self._provision(flow_id, ssid, password))

    async def _provision(self, flow_id: str, ssid: str, password: str) -> None:
        flow = self.hass.config_entries.flow
        st = self.running[flow_id]

        def set_state(state: str, **extra) -> None:
            st.update(state=state, **extra)
            self.m.emit({"kind": "provision", "flow_id": flow_id, "state": state, **extra})

        try:
            preset = await self.m.presets.wifi() if not ssid or password is None else None
            if not ssid:
                if self.ssid:
                    ssid, password = self.ssid, self.password
                elif preset is not None:
                    ssid, password = preset
                else:
                    raise RuntimeError("Kein WLAN: in den Optionen oder als WLAN-Vorgabe hinterlegen")
            elif password is None:
                # SSID angegeben, Passwort leer: gespeichertes Passwort zur selben SSID verwenden
                if ssid == self.ssid:
                    password = self.password
                elif preset is not None and preset[0] == ssid:
                    password = preset[1]
                else:
                    password = ""
            current = next((f for f in flow.async_progress_by_handler(SHELLY) if f["flow_id"] == flow_id), None)
            if current is None:
                raise RuntimeError("Fund nicht mehr vorhanden")
            res: dict[str, Any] = {"type": "form", "step_id": current.get("step_id")}
            scans = 0
            deadline = time.monotonic() + FLOW_TIMEOUT
            while time.monotonic() < deadline:
                step, rtype = res.get("step_id"), res.get("type")
                if rtype == "create_entry":
                    mac = (res.get("result").unique_id if res.get("result") else "") or ""
                    mac = mac.replace(":", "").upper()
                    self.mark_new(mac, via="bluetooth")
                    if mac not in self.m.data.get("restore_pending", {}):
                        set_state("done", title=res.get("title"))
                    else:
                        set_state("restoring", title=res.get("title"))
                        try:
                            await self.m.reset.restore(mac)
                            set_state("done", title=res.get("title"), restored=True)
                        except Exception as err:  # noqa: BLE001
                            _LOGGER.warning("Sicherung für %s nicht eingespielt: %s", mac, err)
                            set_state("done", title=res.get("title"), error=f"Sicherung nicht eingespielt: {err}")
                    self.m.emit({"kind": "changed"})
                    return
                if rtype == "abort":
                    raise RuntimeError(f"Abgebrochen: {res.get('reason')}")
                if rtype == "form" and step == "bluetooth_confirm":
                    set_state("scanning")
                    res = await flow.async_configure(flow_id, {})
                elif rtype == "form" and step == "wifi_scan":
                    set_state("sending")
                    res = await flow.async_configure(flow_id, {"ssid": ssid, "password": password})
                elif rtype == "form" and step in ("provision_failed", "wifi_scan_failed"):
                    # Beide Schritte bieten »erneut versuchen« an (zurück zum WLAN-Scan)
                    if scans >= SCAN_RETRIES:
                        raise RuntimeError(
                            "Gerät ist nach der WLAN-Übertragung nicht im Netz aufgetaucht – WLAN-Passwort prüfen"
                            if step == "provision_failed" else
                            "WLAN-Scan über Bluetooth fehlgeschlagen – Gerät neu einstecken (Einrichtungsfenster abgelaufen?)")
                    scans += 1
                    set_state("scanning")
                    await asyncio.sleep(2)
                    res = await flow.async_configure(flow_id, {})
                elif rtype in ("progress", "progress_done"):
                    set_state("joining")
                    if rtype == "progress":
                        await asyncio.sleep(3)
                    res = await flow.async_configure(flow_id)
                else:
                    raise RuntimeError(f"Unerwarteter Schritt {step or rtype}")
            raise RuntimeError("Zeitüberschreitung")
        except Exception as err:  # noqa: BLE001 – Fehler pro Gerät melden
            _LOGGER.warning("Bluetooth-Einrichtung %s fehlgeschlagen: %s", flow_id, err)
            self._failed[flow_id] = time.time()
            set_state("failed", error=str(err))

    # ── Neu hinzugekommene Geräte ─────────────────────────────────────────────
    def track_new(self) -> None:
        """Geräte merken, die nach dem ersten Start des Managers in HA dazukommen."""
        macs = {(e.unique_id or "").replace(":", "").upper() for e in self.hass.config_entries.async_entries(SHELLY)}
        macs.discard("")
        known = self.m.data.get("known_macs")
        if known is None:
            self.m.data["known_macs"] = sorted(macs)  # Erstlauf: Bestand gilt als bekannt
            self.m._save()
            return
        added = macs - set(known)
        for mac in added & set(self.m.data["reset_hold"]):
            self.release(mac)  # wieder eingerichtet
        for mac in added:
            self.mark_new(mac, via="ha")
        if added:
            self.m.data["known_macs"] = sorted(set(known) | macs)
            self.m._save()
            self.m.emit({"kind": "changed"})
        hold_cut = time.time() - HOLD_DAYS * 86400
        for mac, ts in list(self.m.data["reset_hold"].items()):
            if ts < hold_cut:
                self.release(mac)
        cutoff = time.time() - NEW_KEEP_DAYS * 86400
        for mac, info in list(self.m.data["new_devices"].items()):
            if info.get("ts", 0) < cutoff:
                del self.m.data["new_devices"][mac]

    def mark_new(self, mac: str, via: str) -> None:
        if mac:
            self.m.data["new_devices"].setdefault(mac, {"ts": time.time(), "via": via})
            self.m._save()

    def dismiss(self, mac: str) -> None:
        self.m.data["new_devices"].pop(mac, None)
        self.m._save()
        self.m.emit({"kind": "changed"})

    # ── Basis-Profil für den Assistenten ──────────────────────────────────────
    def profile(self) -> list[dict]:
        return self.m.data.get("profile") or [
            {"id": "ap_enable", "value": False},
            {"id": "cloud", "value": True},
            {"id": "debug_ws", "value": False},
        ]

    def save_profile(self, changes: list[dict]) -> None:
        self.m.data["profile"] = changes
        self.m._save()
