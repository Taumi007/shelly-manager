"""Einstellungs-Katalog und Batch-Planung für Shelly Gen2+.

Bewusst ohne Home-Assistant-Import (testbar wie model.py).

Ein Eintrag beschreibt, wo der Wert in der Gerätekonfiguration steht und welcher RPC-Aufruf ihn setzt.
Mehrere Änderungen an derselben Komponente werden zu einem Aufruf zusammengefasst.
"""

from __future__ import annotations

import copy
from typing import Any, Iterable

# component: Schlüssel in Shelly.GetConfig (»sys«, »ble« …) oder Präfix bei Kanälen (»switch«)
# path:      Pfad innerhalb dieser Komponente
# method:    RPC-Methode; Kanal-Komponenten bekommen zusätzlich »id«
CATALOG: dict[str, dict[str, Any]] = {
    # Gerät
    "eco_mode": {"label": "Eco-Modus", "group": "Gerät", "component": "sys", "path": ("device", "eco_mode"),
                 "type": "bool", "method": "Sys.SetConfig"},
    "discoverable": {"label": "Im Netzwerk auffindbar", "group": "Gerät", "component": "sys",
                     "path": ("device", "discoverable"), "type": "bool", "method": "Sys.SetConfig"},
    "tz": {"label": "Zeitzone", "group": "Gerät", "component": "sys", "path": ("location", "tz"),
           "type": "text", "method": "Sys.SetConfig", "placeholder": "Europe/Vienna"},
    "sntp": {"label": "Zeitserver (SNTP)", "group": "Gerät", "component": "sys", "path": ("sntp", "server"),
             "type": "text", "method": "Sys.SetConfig", "placeholder": "time.google.com"},
    "debug_ws": {"label": "Debug-Log über WebSocket", "group": "Gerät", "component": "sys",
                 "path": ("debug", "websocket", "enable"), "type": "bool", "method": "Sys.SetConfig"},
    # WLAN
    "ap_enable": {"label": "Geräte-Access-Point", "group": "WLAN", "component": "wifi", "path": ("ap", "enable"),
                  "type": "bool", "method": "WiFi.SetConfig"},
    "sta_ssid": {"label": "WLAN 1 – SSID", "group": "WLAN", "component": "wifi", "path": ("sta", "ssid"),
                 "type": "text", "method": "WiFi.SetConfig"},
    "sta_pass": {"label": "WLAN 1 – Passwort", "group": "WLAN", "component": "wifi", "path": ("sta", "pass"),
                 "type": "password", "method": "WiFi.SetConfig", "secret": True},
    "sta1_enable": {"label": "WLAN 2 (Ausweich-WLAN) aktiv", "group": "WLAN", "component": "wifi",
                    "path": ("sta1", "enable"), "type": "bool", "method": "WiFi.SetConfig"},
    "sta1_ssid": {"label": "WLAN 2 – SSID", "group": "WLAN", "component": "wifi", "path": ("sta1", "ssid"),
                  "type": "text", "method": "WiFi.SetConfig"},
    "sta1_pass": {"label": "WLAN 2 – Passwort", "group": "WLAN", "component": "wifi", "path": ("sta1", "pass"),
                  "type": "password", "method": "WiFi.SetConfig", "secret": True},
    # Bluetooth
    "ble_enable": {"label": "Bluetooth", "group": "Bluetooth", "component": "ble", "path": ("enable",),
                   "type": "bool", "method": "BLE.SetConfig"},
    "ble_rpc": {"label": "Bluetooth-RPC (Einrichtung per App)", "group": "Bluetooth", "component": "ble",
                "path": ("rpc", "enable"), "type": "bool", "method": "BLE.SetConfig"},
    "ble_observer": {"label": "BLE-Gateway (Observer)", "group": "Bluetooth", "component": "ble",
                     "path": ("observer", "enable"), "type": "bool", "method": "BLE.SetConfig"},
    # Dienste
    "cloud": {"label": "Shelly Cloud", "group": "Dienste", "component": "cloud", "path": ("enable",),
              "type": "bool", "method": "Cloud.SetConfig"},
    "mqtt_enable": {"label": "MQTT", "group": "Dienste", "component": "mqtt", "path": ("enable",),
                    "type": "bool", "method": "MQTT.SetConfig"},
    "mqtt_server": {"label": "MQTT-Server", "group": "Dienste", "component": "mqtt", "path": ("server",),
                    "type": "text", "method": "MQTT.SetConfig", "placeholder": "192.168.1.10:1883"},
    "mqtt_user": {"label": "MQTT-Benutzer", "group": "Dienste", "component": "mqtt", "path": ("user",),
                  "type": "text", "method": "MQTT.SetConfig"},
    "mqtt_pass": {"label": "MQTT-Passwort", "group": "Dienste", "component": "mqtt", "path": ("pass",),
                  "type": "password", "method": "MQTT.SetConfig", "secret": True},
    "ws_enable": {"label": "Ausgehender WebSocket", "group": "Dienste", "component": "ws", "path": ("enable",),
                  "type": "bool", "method": "Ws.SetConfig"},
    "ws_server": {"label": "Ausgehender WebSocket – Server", "group": "Dienste", "component": "ws",
                  "path": ("server",), "type": "text", "method": "Ws.SetConfig",
                  "placeholder": "ws://192.168.1.10:8123/api/shelly/ws"},
    # Kanäle
    "initial_state": {"label": "Zustand nach Stromausfall", "group": "Kanäle", "component": "switch",
                      "path": ("initial_state",), "type": "enum", "method": "Switch.SetConfig", "channel": True,
                      "options": {"off": "Aus", "on": "Ein", "restore_last": "Letzter Zustand",
                                  "match_input": "Wie Eingang"}},
    "in_mode": {"label": "Eingangsmodus", "group": "Kanäle", "component": "switch", "path": ("in_mode",),
                "type": "enum", "method": "Switch.SetConfig", "channel": True,
                "options": {"momentary": "Taster", "follow": "Schalter (folgen)", "flip": "Schalter (umschalten)",
                            "detached": "Getrennt (detached)", "activate": "Nur einschalten"}},
    "auto_off": {"label": "Automatisch aus", "group": "Kanäle", "component": "switch", "path": ("auto_off",),
                 "type": "bool", "method": "Switch.SetConfig", "channel": True},
    "auto_off_delay": {"label": "Automatisch aus nach (s)", "group": "Kanäle", "component": "switch",
                       "path": ("auto_off_delay",), "type": "number", "method": "Switch.SetConfig", "channel": True,
                       "min": 0.1, "max": 86400},
    "auto_on": {"label": "Automatisch ein", "group": "Kanäle", "component": "switch", "path": ("auto_on",),
                "type": "bool", "method": "Switch.SetConfig", "channel": True},
    "auto_on_delay": {"label": "Automatisch ein nach (s)", "group": "Kanäle", "component": "switch",
                      "path": ("auto_on_delay",), "type": "number", "method": "Switch.SetConfig", "channel": True,
                      "min": 0.1, "max": 86400},
    "consumption_type": {"label": "Gerätetyp für Drittsysteme", "group": "Kanäle", "component": "switch",
                         "type": "enum", "channel": True, "special": "consumption_type", "method": "Sys.SetConfig",
                         "options": {"": "(keiner)", "light": "Licht (light)", "fan": "Lüfter", "heater": "Heizung",
                                     "pump": "Pumpe", "socket": "Steckdose"}, "free": True},
}

RPC_ID_COMPONENTS = ("switch", "cover", "light", "input")


class SettingError(ValueError):
    pass


def catalog_for_frontend() -> list[dict]:
    out = []
    for sid, s in CATALOG.items():
        out.append({"id": sid, **{k: v for k, v in s.items() if k not in ("path", "method", "special")},
                    "options": [{"value": k, "label": v} for k, v in (s.get("options") or {}).items()]})
    return out


def _dig(d: Any, path: Iterable[str]) -> tuple[bool, Any]:
    """(vorhanden, Wert) – vorhanden heißt: der Elternknoten existiert in der Konfiguration."""
    path = list(path)
    for p in path[:-1]:
        if not isinstance(d, dict) or not isinstance(d.get(p), dict):
            return False, None
        d = d[p]
    if not isinstance(d, dict):
        return False, None
    return True, d.get(path[-1])


def _nest(path: Iterable[str], value: Any) -> dict:
    out: Any = value
    for p in reversed(list(path)):
        out = {p: out}
    return out


def deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def coerce(setting: dict, value: Any) -> Any:
    t = setting["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "1", "an", "on"):
            return True
        if str(value).lower() in ("false", "0", "aus", "off"):
            return False
        raise SettingError(f"{setting['label']}: ja/nein erwartet")
    if t == "number":
        try:
            v = float(value)
        except (TypeError, ValueError) as err:
            raise SettingError(f"{setting['label']}: Zahl erwartet") from err
        if not setting.get("min", float("-inf")) <= v <= setting.get("max", float("inf")):
            raise SettingError(f"{setting['label']}: außerhalb {setting.get('min')}–{setting.get('max')}")
        return v
    if t == "enum":
        v = "" if value is None else str(value)
        if v not in (setting.get("options") or {}) and not setting.get("free"):
            raise SettingError(f"{setting['label']}: ungültiger Wert »{v}«")
        return v
    v = "" if value is None else str(value)
    return v or None  # leerer Text = Feld leeren


def channel_keys(config: dict, component: str) -> list[str]:
    keys = []
    for k in config or {}:
        comp, _, idx = k.partition(":")
        if comp == component and idx.isdigit():
            keys.append((int(idx), k))
    return [k for _, k in sorted(keys)]


def _ctypes(config: dict) -> list:
    return list(((config.get("sys") or {}).get("ui_data") or {}).get("consumption_types") or [])


def plan_device(mac: str, config: dict | None, changes: list[dict], *, sleeping: bool = False,
                gen: int | None = None) -> list[dict]:
    """Ein Eintrag pro Einstellung × Ziel (Gerät oder Kanal) mit Status und altem/neuem Wert.

    changes: [{id, value, channel?}]  channel None = alle Kanäle, sonst Index.
    status:  change | same | unsupported | offline | sleeping
    """
    items: list[dict] = []
    for ch in changes:
        sid = ch["id"]
        s = CATALOG.get(sid)
        if s is None:
            raise SettingError(f"Unbekannte Einstellung {sid}")
        value = coerce(s, ch.get("value"))
        base = {"mac": mac, "setting": sid, "label": s["label"], "new": None if s.get("secret") else value,
                "_value": value, "secret": bool(s.get("secret"))}
        if gen == 1:
            items.append({**base, "key": s["component"], "status": "unsupported", "reason": "Gen1"})
            continue
        if sleeping:
            items.append({**base, "key": s["component"], "status": "sleeping", "reason": "Schlafendes Akku-Gerät"})
            continue
        if config is None:
            items.append({**base, "key": s["component"], "status": "offline", "reason": "Keine Konfiguration (offline?)"})
            continue
        if s.get("channel"):
            keys = channel_keys(config, s["component"])
            if ch.get("channel") is not None:
                keys = [k for k in keys if k == f"{s['component']}:{ch['channel']}"]
            if not keys:
                items.append({**base, "key": s["component"], "status": "unsupported", "reason": "Kanal nicht vorhanden"})
                continue
        else:
            keys = [s["component"]]
        for key in keys:
            if s.get("special") == "consumption_type":
                idx = int(key.split(":")[1])
                ct = _ctypes(config)
                old = ct[idx] if idx < len(ct) else ""
                exists = True
            else:
                exists, old = _dig(config.get(key), s["path"]) if isinstance(config.get(key), dict) else (False, None)
            if not exists:
                items.append({**base, "key": key, "status": "unsupported", "reason": "Vom Gerät nicht unterstützt"})
                continue
            same = (old or None) == (value or None) if not s.get("secret") else False
            items.append({**base, "key": key, "old": None if s.get("secret") else old,
                          "status": "same" if same else "change"})
    return items


def build_calls(items: list[dict], config: dict) -> list[tuple[str, dict, list[dict]]]:
    """Zu ändernde Einträge eines Geräts → [(method, params, items)], je Komponente ein Aufruf."""
    calls: dict[tuple[str, str], dict] = {}
    ct_new: dict[int, str] = {}
    ct_items: list[dict] = []
    for it in items:
        if it["status"] != "change":
            continue
        s = CATALOG[it["setting"]]
        if s.get("special") == "consumption_type":
            ct_new[int(it["key"].split(":")[1])] = it["_value"] or ""
            ct_items.append(it)
            continue
        comp = it["key"].split(":")[0]
        slot = calls.setdefault((s["method"], it["key"]), {"config": {}, "items": [], "comp": comp})
        slot["config"] = deep_merge(slot["config"], _nest(s["path"], it["_value"]))
        slot["items"].append(it)
    out: list[tuple[str, dict, list[dict]]] = []
    if ct_new:
        ct = _ctypes(config)
        n = max(len(ct), max(ct_new) + 1, len(channel_keys(config, "switch")))
        ct = (ct + [""] * n)[:n]
        for idx, v in ct_new.items():
            ct[idx] = v
        slot = calls.setdefault(("Sys.SetConfig", "sys"), {"config": {}, "items": [], "comp": "sys"})
        slot["config"] = deep_merge(slot["config"], {"ui_data": {"consumption_types": ct}})
        slot["items"].extend(ct_items)
    for (method, key), slot in calls.items():
        params: dict[str, Any] = {"config": slot["config"]}
        if slot["comp"] in RPC_ID_COMPONENTS and ":" in key:
            params["id"] = int(key.split(":")[1])
        out.append((method, params, slot["items"]))
    return out


def undo_changes(items: list[dict]) -> list[dict]:
    """Aus erfolgreich angewendeten Einträgen die Gegenänderung (ohne Passwörter)."""
    out = []
    for it in items:
        if it.get("ok") and not it.get("secret") and it["status"] == "change":
            ch = {"id": it["setting"], "value": it.get("old")}
            if ":" in it["key"]:
                ch["channel"] = int(it["key"].split(":")[1])
            out.append(ch)
    return out


def public(item: dict) -> dict:
    """Interne Felder (Klartextwerte, z. B. Passwörter) entfernen."""
    return {k: v for k, v in item.items() if not k.startswith("_")}
