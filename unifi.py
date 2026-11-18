"""UniFi Network: Client-Aliasse und feste IPs der Shellys abgleichen.

Nutzt die Verbindung der vorhandenen UniFi-Integration in HA (aiounifi-Controller) –
keine eigenen Zugangsdaten. Geschrieben wird über die klassische Network-API:
    PUT /rest/user/<_id>  {"name": …}                          Alias
    PUT /rest/user/<_id>  {"use_fixedip": true, "fixed_ip": …,  feste IP (DHCP-Reservierung)
                           "network_id": …}
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .model import is_stale_reservation

_LOGGER = logging.getLogger(__name__)
UNIFI = "unifi"
WRITE_GAP = 0.5  # s zwischen zwei Schreibaufrufen an den Controller


def mac_colon(mac: str) -> str:
    m = mac.replace(":", "").lower()
    return ":".join(m[i:i + 2] for i in range(0, 12, 2))


OVERRIDE_TTL = 1800  # s: so lange gilt ein selbst geschriebener Wert, bis der Controller ihn auch meldet


class UniFi:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        # Selbst geschriebene Werte (Name, Reservierung): Controller-Listen (stat/sta, stat/alluser) hinken nach
        # einem Schreiben oft hinterher und würden den alten Stand wieder einspielen
        self._override: dict[str, dict] = {}

    # ── Verbindung ────────────────────────────────────────────────────────────
    def _api(self):
        for entry in self.hass.config_entries.async_entries(UNIFI):
            if entry.state is ConfigEntryState.LOADED:
                hub = getattr(entry, "runtime_data", None)
                api = getattr(hub, "api", None)
                if api is not None:
                    return entry, api
        return None, None

    def status(self) -> dict:
        entry, api = self._api()
        return {"available": api is not None, "title": entry.title if entry else None}

    def _raw(self, handler, mac: str) -> dict | None:
        try:
            item = handler.get(mac) if handler is not None else None
        except Exception:  # noqa: BLE001
            item = None
        return getattr(item, "raw", None) if item is not None else None

    def lookup(self, mac: str) -> dict | None:
        """UniFi-Sicht auf einen Shelly (verbundene Clients zuerst, sonst alle bekannten)."""
        _, api = self._api()
        if api is None:
            return None
        key = mac_colon(mac)
        live = self._raw(getattr(api, "clients", None), key)
        known = self._raw(getattr(api, "clients_all", None), key)
        raw = {**(known or {}), **(live or {})}
        if not raw:
            return {"found": False}
        ov = self._override.get(key)
        if ov:
            fresh = {k: v for k, v in ov.items() if k != "ts"}
            if time.time() - ov["ts"] > OVERRIDE_TTL or all((known or live or {}).get(k) == v for k, v in fresh.items()):
                self._override.pop(key, None)  # Controller hat aufgeholt (oder zu alt)
            else:
                known = {**(known or live or {}), **fresh}
                raw = {**raw, **fresh}
        # Alias und Reservierung sind Einstellungen: maßgeblich ist der gespeicherte Eintrag (rest/user),
        # die Live-Liste (stat/sta) hinkt nach Änderungen hinterher
        cfg = known or live or {}
        ap_mac = raw.get("ap_mac")
        ap = self._raw(getattr(api, "devices", None), ap_mac) if ap_mac else None
        return {
            "found": True, "connected": live is not None, "id": raw.get("_id") or (known or {}).get("_id"),
            "name": cfg.get("name") or None, "hostname": raw.get("hostname"), "ip": raw.get("ip"),
            "use_fixedip": bool(cfg.get("use_fixedip")), "fixed_ip": cfg.get("fixed_ip"),
            "network_id": raw.get("network_id") or (known or {}).get("network_id"),
            "essid": raw.get("essid"), "signal": raw.get("signal"), "rssi": raw.get("rssi"),
            "ap": (ap or {}).get("name") or ap_mac, "last_seen": raw.get("last_seen"),
        }

    # ── Planung ───────────────────────────────────────────────────────────────
    def _used_ips(self) -> set[str]:
        _, api = self._api()
        used: set[str] = set()
        for handler in ("clients_all", "clients"):
            h = getattr(api, handler, None)
            for item in (h.values() if h is not None and hasattr(h, "values") else []):
                raw = getattr(item, "raw", {}) or {}
                if raw.get("use_fixedip") and raw.get("fixed_ip"):
                    used.add(raw["fixed_ip"])
                if raw.get("ip"):
                    used.add(raw["ip"])
        return used

    def plan(self, rows: list[dict], what: list[str], fixed_range: str | None = None) -> tuple[list[dict], list[dict]]:
        """what: names | fixed_ip | cleanup (alte 192.168-Reservierungen) | release (Reservierung entfernen)."""
        import ipaddress
        ops, skipped = [], []
        net = ipaddress.ip_network(fixed_range, strict=False) if fixed_range else None
        free = None
        for r in rows:
            u = self.lookup(r["mac"])
            base = {"mac": r["mac"], "device": r["name"]}
            if u is None:
                skipped.append({**base, "reason": "UniFi-Integration nicht geladen"})
                continue
            if not u["found"] or not u.get("id"):
                skipped.append({**base, "reason": "In UniFi nicht bekannt"})
                continue
            if "names" in what and r.get("name_stage") in (1, 2, 3):
                skipped.append({**base, "reason": f"Name noch nicht festgelegt (Stufe {r['name_stage']} offen)"})
            elif "names" in what:
                want = (r.get("shelly_name") or "").strip() or r["name"]
                if want and want != (u["name"] or ""):
                    ops.append({**base, "kind": "name", "id": u["id"], "old": u["name"] or f"({u['hostname'] or '–'})",
                                "new": want, "data": {"name": want}})
            if "fixed_ip" in what:
                cur = u["fixed_ip"] if u["use_fixedip"] else None
                if net is None:
                    skipped.append({**base, "reason": "Kein Bereich für feste IPs in den Optionen gesetzt"})
                elif cur and ipaddress.ip_address(cur) in net:
                    pass  # hat bereits eine passende Reservierung
                elif not u.get("network_id"):
                    skipped.append({**base, "reason": "Netzwerk in UniFi unbekannt"})
                else:
                    if free is None:
                        used = self._used_ips()
                        free = [str(ip) for ip in net.hosts() if str(ip) not in used]
                    if not free:
                        skipped.append({**base, "reason": f"Keine freie Adresse mehr in {net}"})
                    else:
                        ip = free.pop(0)
                        ops.append({**base, "kind": "fixed_ip", "id": u["id"], "old": f"fest {cur}" if cur else (u["ip"] or "dynamisch"),
                                    "new": ip, "reboot": True,
                                    "data": {"use_fixedip": True, "fixed_ip": ip, "network_id": u["network_id"]}})
            if "cleanup" in what and u.get("fixed_ip"):
                stale = is_stale_reservation(u["fixed_ip"], u.get("ip"), fixed_range)
                if stale:
                    ops.append({**base, "kind": "clear_fixed", "id": u["id"],
                                "old": f"{'aktiv' if u['use_fixedip'] else 'alt'} {u['fixed_ip']}", "new": "entfernt",
                                "reboot": bool(u["use_fixedip"]), "data": {"use_fixedip": False, "fixed_ip": ""}})
            if "release" in what and (u.get("fixed_ip") or u["use_fixedip"]):
                ops.append({**base, "kind": "clear_fixed", "id": u["id"],
                            "old": f"{'aktiv' if u['use_fixedip'] else 'alt'} {u['fixed_ip']}", "new": "entfernt",
                            "reboot": bool(u["use_fixedip"]), "data": {"use_fixedip": False, "fixed_ip": ""}})
        return ops, skipped

    def _patch_local(self, api, op: dict) -> None:
        """Lokale Kopie sofort anpassen, damit Tabelle und Probleme nicht auf die nächste Abfrage warten."""
        key = mac_colon(op["mac"])
        data = {k: v for k, v in op["data"].items() if k in ("name", "use_fixedip", "fixed_ip")}
        self._override[key] = {**(self._override.get(key) or {}), **data, "ts": time.time()}
        for handler in ("clients_all", "clients"):
            h = getattr(api, handler, None)
            try:
                item = h.get(key) if h is not None else None
            except Exception:  # noqa: BLE001
                item = None
            raw = getattr(item, "raw", None)
            if isinstance(raw, dict):
                raw.update(data)

    # ── Funkdiagnose (nur lesen) ─────────────────────────────────────────────
    async def radio(self, ssid: str | None = None) -> dict:
        """Frische Funkwerte der APs und ihrer WLAN-Clients direkt vom Controller."""
        from aiounifi.models.api import ApiRequest

        _, api = self._api()
        if api is None:
            raise RuntimeError("UniFi-Integration nicht geladen")
        devs = (await api.request(ApiRequest(method="get", path="/stat/device"))).get("data", [])
        stas = (await api.request(ApiRequest(method="get", path="/stat/sta"))).get("data", [])
        wlans = (await api.request(ApiRequest(method="get", path="/rest/wlanconf"))).get("data", [])
        dkeys = ("radio", "channel", "tx_power", "cu_total", "cu_self_rx", "cu_self_tx", "satisfaction", "num_sta",
                 "tx_packets", "tx_retries", "rx_packets", "extchannel")
        rkeys = ("radio", "channel", "ht", "tx_power_mode", "tx_power", "min_rssi_enabled", "min_rssi",
                 "sens_level_enabled", "antenna_gain", "max_txpower", "min_txpower")
        aps = []
        for d in devs:
            if d.get("type") != "uap":
                continue
            aps.append({"name": d.get("name"), "mac": d.get("mac"), "model": d.get("model"),
                        "version": d.get("version"),
                        "cast": {f"{k}.{k2}": v2 for k, v in (d.get("stat") or {}).items() if isinstance(v, dict)
                                 for k2, v2 in v.items() if "cast" in k2 and ("wifi" in k2 or "user" in k2 or "tx" in k2) and ":" not in k2},
                        "vap_cast": [{"essid": v.get("essid"), "radio": v.get("radio"),
                                      **{k2: v2 for k2, v2 in v.items() if "cast" in k2}} for v in d.get("vap_table", [])],
                        "radio_table": [{k: r.get(k) for k in rkeys} for r in d.get("radio_table", [])],
                        "radio_stats": [{k: r.get(k) for k in dkeys} for r in d.get("radio_table_stats", [])],
                        "vap": [{k: v.get(k) for k in ("essid", "radio", "channel", "num_sta", "satisfaction", "tx_retries",
                                                        "tx_packets", "rx_packets", "ccq", "avg_client_signal")}
                                for v in d.get("vap_table", [])]})
        ckeys = ("mac", "name", "hostname", "essid", "ap_mac", "channel", "radio", "signal", "noise", "tx_rate", "rx_rate",
                 "tx_retries", "tx_packets", "satisfaction", "wifi_tx_attempts", "tx_retry_burst_count", "uptime",
                 "powersave_enabled", "idletime", "roam_count")
        clients = [{k: c.get(k) for k in ckeys} for c in stas if not c.get("is_wired") and (ssid is None or c.get("essid") == ssid)]
        wkeys = ("name", "enabled", "wlan_band", "wlan_bands", "minrate_ng_enabled", "minrate_ng_data_rate_kbps",
                 "mcastenhance_enabled", "dtim_ng", "dtim_mode", "bss_transition", "fast_roaming_enabled", "uapsd_enabled",
                 "multicast_to_unicast", "pmf_mode", "wpa_mode", "wpa3_support", "wpa3_transition", "proxy_arp",
                 "l2_isolation", "no2ghz_oui", "group_rekey", "ap_group_mode")
        return {"aps": aps, "clients": clients,
                "wlans": [{**{k: w.get(k) for k in wkeys}, **{k: v for k, v in w.items() if "minrate" in k or "rate" in k}} for w in wlans]}

    async def forget(self, mac: str) -> str | None:
        """Client in UniFi vergessen: entfernt Alias, IP-Reservierung und Verlauf."""
        from aiounifi.models.api import ApiRequest

        _, api = self._api()
        if api is None:
            raise RuntimeError("UniFi-Integration nicht geladen")
        info = self.lookup(mac) or {}
        if not info.get("found"):
            return "war in UniFi nicht bekannt"
        await api.request(ApiRequest(method="post", path="/cmd/stamgr", data={"cmd": "forget-sta", "macs": [mac_colon(mac)]}))
        self._override.pop(mac_colon(mac), None)
        for handler in ("clients_all", "clients"):
            h = getattr(api, handler, None)
            if h is not None and hasattr(h, "update"):
                try:
                    await h.update()
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("UniFi-Aktualisierung %s: %s", handler, err)
        parts = [p for p in (info.get("name") and f"Name »{info['name']}«",
                             info.get("use_fixedip") and f"Reservierung {info.get('fixed_ip')}") if p]
        return ("entfernt: " + ", ".join(parts)) if parts else None

    def shelly_candidates(self, known_ouis: set[str]) -> list[dict]:
        """Verbundene WLAN-/LAN-Clients, die nach Shelly aussehen (Hostname oder bekannte Herstellerkennung)."""
        _, api = self._api()
        h = getattr(api, "clients", None) if api is not None else None
        out = []
        for item in (h.values() if h is not None and hasattr(h, "values") else []):
            raw = getattr(item, "raw", {}) or {}
            mac, ip = (raw.get("mac") or "").lower(), raw.get("ip")
            if not mac or not ip:
                continue
            host = (raw.get("hostname") or raw.get("name") or "").lower()
            if host.startswith("shelly") or mac[:8] in known_ouis:
                out.append({"mac": mac.replace(":", "").upper(), "ip": ip, "hostname": raw.get("hostname")})
        return out

    WLAN_KEYS = ("mcastenhance_enabled", "minrate_ng_enabled", "minrate_ng_data_rate_kbps")

    async def set_wlan(self, name: str, data: dict) -> dict:
        """Einzelne, freigegebene WLAN-Einstellungen ändern; gibt den vorherigen Wert zurück."""
        from aiounifi.models.api import ApiRequest

        bad = [k for k in data if k not in self.WLAN_KEYS and not k.startswith("minrate")]
        if bad:
            raise ValueError(f"Nicht erlaubt: {', '.join(bad)}")
        _, api = self._api()
        if api is None:
            raise RuntimeError("UniFi-Integration nicht geladen")
        wlans = (await api.request(ApiRequest(method="get", path="/rest/wlanconf"))).get("data", [])
        w = next((w for w in wlans if w.get("name") == name), None)
        if w is None:
            raise ValueError(f"WLAN {name} nicht gefunden")
        before = {k: w.get(k) for k in data}
        await api.request(ApiRequest(method="put", path=f"/rest/wlanconf/{w['_id']}", data=data))
        # Zurücklesen: Der Controller ignoriert Felder stillschweigend, wenn Abhängigkeiten fehlen
        fresh = (await api.request(ApiRequest(method="get", path="/rest/wlanconf"))).get("data", [])
        now = next((x for x in fresh if x.get("_id") == w["_id"]), {})
        return {"name": name, "before": before, "after": {k: now.get(k) for k in data}}

    # ── Schreiben ─────────────────────────────────────────────────────────────
    async def apply(self, ops: list[dict], progress=None) -> list[dict]:
        from aiounifi.models.api import ApiRequest

        _, api = self._api()
        if api is None:
            raise RuntimeError("UniFi-Integration nicht geladen")
        results = []
        for op in ops:
            item = f"{op['mac']}|{op['kind']}"
            if progress:
                progress(item, "running")
            try:
                try:
                    await api.request(ApiRequest(method="put", path=f"/rest/user/{op['id']}", data=op["data"]))
                except Exception:
                    if op["kind"] != "clear_fixed":
                        raise
                    # Manche Controller lehnen ein leeres fixed_ip ab – dann nur die Reservierung deaktivieren
                    await api.request(ApiRequest(method="put", path=f"/rest/user/{op['id']}", data={"use_fixedip": False}))
                    op = {**op, "new": "deaktiviert (Adresse bleibt gespeichert)"}
                results.append({**op, "ok": True})
                self._patch_local(api, op)
                if progress:
                    progress(item, "done")
            except Exception as err:  # noqa: BLE001 – Ergebnis pro Gerät
                results.append({**op, "ok": False, "error": str(err)})
                if progress:
                    progress(item, "error", str(err))
            await asyncio.sleep(WRITE_GAP)
        # Neuen Stand laden, damit Tabelle und Vergleich stimmen
        for handler in ("clients_all", "clients"):
            h = getattr(api, handler, None)
            if h is not None and hasattr(h, "update"):
                try:
                    await h.update()
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("UniFi-Aktualisierung %s: %s", handler, err)
        return results
