"""Namenslogik, Kanäle, Tabellenzeilen und Prüfungen.

Bewusst ohne Home-Assistant-Import, damit es sich eigenständig testen lässt.

Ein Gerät kommt als Dict aus inventory.py:

    mac, host, port, gen, model, model_id, sleeping, sources
    ha      None oder {device_id, entry_id, entry_state, name, name_by_user, area_id,
                       channels: {key: {device_id, name, name_by_user, area_id}},
                       entities: {key: {switch, power, energy}}, entity_ids, available}
    shelly  None oder {info, config, status, fetched_at, error, live}
    helpers [{entity_id, source, target_domain}]   switch_as_x-Helfer auf Kanälen des Geräts

Namensregeln (Shelly-Integration in HA):
    Der Shelly-Gerätename wird 1:1 zum HA-Gerätenamen. HA stellt bei Entity-IDs und Anzeige
    den Bereich voran. Deshalb gilt:
        Shelly-Name   = Bereich + Gerätename          »Schlafzimmer Steckdose West«
        HA-Name       = nur der Gerätename            »Steckdose West«, Bereich »Schlafzimmer«
    0 Kanäle        nur Gerätename
    1 Kanal         nur Gerätename, Kanalname leer (sonst hängt HA ihn an die Entity-ID an)
    ≥2 Kanäle       Sub-Geräte; jeder Kanal trägt den vollen Namen inkl. Bereich
"""

from __future__ import annotations

import re
from typing import Any, Iterable

try:
    from .naming import RULES, ambiguous, check as check_rules, suggest as suggest_name
except ImportError:  # eigenständig geladen (Tests)
    from naming import RULES, ambiguous, check as check_rules, suggest as suggest_name

# Komponenten, aus denen die Shelly-Integration ab zwei Instanzen Sub-Geräte macht
SUB_DEVICE_COMPONENTS = ("switch", "cover", "light", "rgb", "rgbw", "cct", "em1")
# Komponenten mit Schaltausgang (für Schaltverhalten und Consumption Type)
OUTPUT_COMPONENTS = ("switch", "light", "rgb", "rgbw", "cct")
METER_COMPONENTS = ("pm1", "em1", "em")
KEY_RE = re.compile(r"^([a-z0-9_]+):(\d+)$")
# Standardnamen: ganzer Name generisch (»Kanal 2«) oder HAs englische Vorgabe am Ende (»… Output 1«)
GENERIC_RE = re.compile(
    r"^(output|ausgang|kanal|channel|switch|schalter|relay|relais|light|licht|cover|rgbw?|cct|energy meter|input|eingang)\s*\d*$"
    r"|\s(output|switch|light|cover|rgbw?|cct|energy meter)\s*\d+$",
    re.IGNORECASE)

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


# ── Namen ─────────────────────────────────────────────────────────────────────

def strip_area(name: str | None, area: str | None) -> str:
    """Bereich vorne abschneiden, falls vorhanden."""
    name = " ".join((name or "").split())
    if area and _starts_with_word(name, area):
        return name[len(" ".join(area.split())):].strip()
    return name


def norm(text: str | None) -> str:
    # Bindestrich wie Leerzeichen: Bereich »Hybrid-Inverter« passt auf »Hybrid Inverter …«
    return " ".join((text or "").replace("-", " ").split()).casefold()


def _starts_with_word(text: str, prefix: str) -> bool:
    t, p = norm(text), norm(prefix)
    return bool(p) and (t == p or t.startswith(p + " "))


def split_area(full: str | None, areas: Iterable[str], prefer: str | None = None) -> tuple[str | None, str]:
    """Bereich vom Gerätenamen trennen (längstes passendes Präfix, wortweise).

    `prefer` (z. B. der aktuelle HA-Bereich) gewinnt, wenn er passt, auch wenn ein längerer existiert.
    """
    full = " ".join((full or "").split())
    if prefer and _starts_with_word(full, prefer):
        return prefer, full[len(" ".join(prefer.split())):].strip()
    best = None
    for area in areas:
        if area and _starts_with_word(full, area) and (best is None or len(area) > len(best)):
            best = area
    if best is None:
        return None, full
    return best, full[len(" ".join(best.split())):].strip()


def compose(area: str | None, name: str | None) -> str:
    """Bereich + Name, ohne den Bereich zu verdoppeln."""
    name = " ".join((name or "").split())
    if not area:
        return name
    if not name:
        return area
    if _starts_with_word(name, area):
        return name
    return f"{area} {name}"


def is_generic(name: str | None) -> bool:
    return not (name or "").strip() or bool(GENERIC_RE.search(name.strip()))


# ── Shelly-Daten ──────────────────────────────────────────────────────────────

def component_keys(data: dict | None, component: str) -> list[str]:
    """Schlüssel wie »switch:0« einer Komponente, nach Index sortiert."""
    keys = []
    for key in (data or {}):
        m = KEY_RE.match(key)
        if m and m.group(1) == component:
            keys.append((int(m.group(2)), key))
    return [k for _, k in sorted(keys)]


def sub_device_keys(config: dict | None, status: dict | None) -> list[str]:
    """Kanäle, die in HA als Sub-Gerät erscheinen (nur Komponenten mit ≥2 Instanzen)."""
    src = status or config or {}
    out = []
    for comp in SUB_DEVICE_COMPONENTS:
        keys = component_keys(src, comp)
        if len(keys) >= 2:
            out.extend(keys)
    return out


def output_keys(config: dict | None, status: dict | None) -> list[str]:
    src = config or status or {}
    out = []
    for comp in OUTPUT_COMPONENTS + ("cover",):
        out.extend(component_keys(src, comp))
    return out


def consumption_types(config: dict | None) -> list[str]:
    ui = ((config or {}).get("sys") or {}).get("ui_data") or {}
    return list(ui.get("consumption_types") or [])


def _get(d: Any, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
    return default if d is None else d


# ── Namensvergleich ───────────────────────────────────────────────────────────

def _ha_effective(entry: dict | None) -> str:
    if not entry:
        return ""
    return entry.get("name_by_user") or entry.get("name") or ""


def _item(key: str, label: str, shelly_name: str | None, ha: dict | None, ha_area: str | None,
          areas: list[str], expect_empty: bool = False) -> dict:
    ha_name = _ha_effective(ha)
    ha_full = compose(ha_area, ha_name) if ha else None
    sh_area, sh_rest = split_area(shelly_name, areas, prefer=ha_area) if shelly_name else (None, "")
    hints: list[str] = []
    if ha is not None:
        if ha_area is None:
            hints.append("no_area")
        elif _starts_with_word(ha_name, ha_area):
            hints.append("ha_area_in_name")
        if not (ha.get("name_by_user") or "").strip() and "ha_area_in_name" not in hints:
            # Ohne eigenen HA-Namen übernimmt HA den Shelly-Namen (inkl. Bereich) beim nächsten Neuladen
            hints.append("ha_no_user_name")
    if shelly_name is not None and not expect_empty:
        if not shelly_name.strip():
            hints.append("shelly_empty")
        elif sh_area is None:
            hints.append("shelly_no_area")
    if shelly_name is None or ha is None:
        status = "unknown"
    elif expect_empty:
        status = "ok"
    elif norm(shelly_name) == norm(ha_full):
        status = "ok"
    else:
        status = "diff"
    return {
        "key": key, "label": label, "shelly": shelly_name, "ha_name": ha_name if ha else None,
        "ha_area": ha_area, "ha_full": ha_full, "ha_device_id": (ha or {}).get("device_id"),
        "ha_name_by_user": (ha or {}).get("name_by_user"),
        "shelly_area": sh_area, "shelly_rest": sh_rest, "status": status, "hints": hints,
        # Vorschläge für beide Richtungen
        "to_shelly": ha_full, "to_ha": {"area": sh_area or ha_area, "name": sh_rest} if shelly_name else None,
    }


def _coupled_output(config: dict, key: str) -> str | None:
    """Ausgang, den dieser Eingang direkt schaltet (gleicher Index, Modus nicht »detached«)."""
    idx = key.split(":")[1]
    for comp in ("switch", "light", "cover"):
        cfg = config.get(f"{comp}:{idx}")
        if cfg is not None and cfg.get("in_mode") != "detached":
            return f"{comp}:{idx}"
    return None


def _standalone_input(config: dict, key: str, outs: list[str]) -> bool:
    """Eingang ohne zugehörigen Ausgang: i4 & Co. oder Ausgang mit gleichem Index im Modus »detached«."""
    if not outs:
        return True
    idx = key.split(":")[1]
    for comp in ("switch", "light", "cover"):
        cfg = config.get(f"{comp}:{idx}")
        if cfg is not None:
            return cfg.get("in_mode") == "detached"
    return False


def analyze_names(dev: dict, areas_by_id: dict[str, str]) -> dict:
    """Shelly- und HA-Namen von Gerät und Kanälen gegenüberstellen."""
    areas = sorted(set(areas_by_id.values()), key=len, reverse=True)
    ha = dev.get("ha")
    config = _get(dev, "shelly", "config")
    status = _get(dev, "shelly", "status")
    root_area = areas_by_id.get((ha or {}).get("area_id") or "")
    shelly_name = _get(config, "sys", "device", "name", default="") if config is not None else None
    device = _item("device", "Gerät", shelly_name, ha, root_area, areas)

    subs = sub_device_keys(config, status) if (config or status) else sorted((ha or {}).get("channels", {}))
    channels: list[dict] = []
    issues: list[str] = []
    if len(subs) >= 2:
        for key in subs:
            ha_ch = ((ha or {}).get("channels") or {}).get(key)
            ch_area = areas_by_id.get((ha_ch or {}).get("area_id") or "") or root_area
            name = _get(config, key, "name", default="") if config is not None else None
            item = _item(key, key, name, ha_ch, ch_area, areas)
            if name is not None and is_generic(name):
                item["hints"].append("generic")
            channels.append(item)
    elif config is not None:
        # Einkanalig: ein gesetzter Kanalname landet zusätzlich in der Entity-ID
        for key in output_keys(config, status)[:1]:
            name = _get(config, key, "name", default="")
            if name and name.strip():
                issues.append("single_channel_named")
                channels.append({**_item(key, key, name, None, None, areas, expect_empty=True),
                                 "status": "diff", "to_shelly": "", "hints": ["single_channel_named"]})
    # Benannte Eingänge (i4 & Co.): HA-Gegenstück ist die Entity mit eigenem Namen und Bereich
    ha_inputs = (ha or {}).get("inputs") or {}
    outs = output_keys(config, status) if config is not None else []
    for key in (component_keys(config, "input") if config is not None else sorted(ha_inputs)):
        inp = ha_inputs.get(key)
        name = _get(config, key, "name", default="") if config is not None else None
        if config is not None and _get(config, key, "enable") is False:
            continue  # deaktivierter Eingang
        named = bool((name or "").strip() or (inp or {}).get("name") or (inp or {}).get("area_id"))
        if not named and config is not None and _standalone_input(config, key, outs):
            # Eigenständiger Taster ohne Namen (i4, oder Ausgang »detached«): benennen oder abschalten
            channels.append({**_item(key, key, name, None, None, areas, expect_empty=True),
                             "kind": "input", "unnamed": True, "hints": ["input_unnamed"], "status": "info",
                             "ha_entity_id": (inp or {}).get("entity_id"), "ha_area": root_area})
            continue
        coupled = _coupled_output(config, key) if config is not None else None
        if not named:
            # Am Ausgang gekoppelter Taster ohne Namen: gehört zum Kanal – nur anzeigen, kein Handlungsbedarf
            if coupled:
                channels.append({**_item(key, key, name, None, None, areas, expect_empty=True),
                                 "kind": "input", "coupled": coupled, "status": "ok", "hints": [],
                                 "in_mode": _get(config, coupled, "in_mode"),
                                 "ha_entity_id": (inp or {}).get("entity_id"), "ha_area": root_area})
            continue
        if inp is None:
            continue
        ha_in = {"name": inp.get("original_name") or name or key, "name_by_user": inp.get("name")}
        in_area = areas_by_id.get(inp.get("area_id") or "") or root_area
        item = _item(key, key, name, ha_in, in_area, areas)
        item.update({"kind": "input", "ha_entity_id": inp["entity_id"], "ha_entity_area_id": inp.get("area_id"),
                     "coupled": coupled})
        if name is not None and is_generic(name):
            item["hints"].append("generic")
        channels.append(item)
    # Gleiche Kanalnamen innerhalb eines Geräts sind fast immer ein Kopierfehler
    seen: dict[str, list[dict]] = {}
    for it in channels:
        if (it["shelly"] or "").strip():
            seen.setdefault(norm(it["shelly"]), []).append(it)
    for same in seen.values():
        if len(same) > 1:
            for it in same:
                it["hints"].append("duplicate_channel")
    for it in [device, *channels]:
        issues.extend(it["hints"])
    items = [device, *channels]
    if any(it["status"] == "diff" for it in items):
        worst = "diff"
    elif any(NAME_HINTS.get(x, ("warning",))[0] == "warning" for it in items for x in it["hints"]):
        worst = "warn"
    elif any(it["hints"] for it in items):
        worst = "info"
    elif device["status"] == "unknown":
        worst = "unknown"
    else:
        worst = "ok"
    return {"device": device, "channels": channels, "multi": len(subs) >= 2, "issues": sorted(set(issues)),
            "status": worst}


# ── Namensregeln ──────────────────────────────────────────────────────────────

RULE_SEVERITY = {"space": "warning", "chars": "warning", "empty": "warning", "direction": "warning",
                 "order": "warning", "plug_plural": "warning", "plug_order": "warning",
                 "outlets_singular": "warning", "single_module": "warning"}


def device_kind(dev: dict) -> str:
    """Geräteart für die Namensregeln."""
    sh = dev.get("shelly") or {}
    info, config, status = sh.get("info") or {}, sh.get("config"), sh.get("status")
    label = f"{info.get('app') or ''} {dev.get('model') or ''} {dev.get('model_id') or ''}".lower()
    if "plug" in label or "pl-" in label or "snpl" in label:
        return "plug"
    if "h&t" in label or label.strip().startswith("ht") or " ht" in label or "s3sn-0u12a" in label:
        return "sensor"
    src = config or status or {}
    outs = output_keys(config, status)
    if not outs:
        if component_keys(src, "em") or component_keys(src, "em1"):
            return "meter"
        if component_keys(src, "pm1"):
            return "outlets"
        if component_keys(src, "input"):
            return "wallswitch"
        return "other"
    return "module" if len(sub_device_keys(config, status)) >= 2 else "single"


def _function_name(dev: dict, item: dict) -> str | None:
    """Einkanaler »… Modul …«: Name nach Funktion – aus dem HA-Namen oder dem (noch gesetzten) Kanalnamen."""
    area = item.get("ha_area")
    config = _get(dev, "shelly", "config") or {}
    candidates = [item.get("ha_name")]
    for key in output_keys(config, None)[:1]:
        candidates.append(_get(config, key, "name"))
    for c in candidates:
        if c and c.strip() and "modul" not in norm(c):
            return compose(area, c)
    return None


def rule_issues(dev: dict, names: dict) -> list[dict]:
    """Regelverstöße der Shelly-Namen (Gerät, Kanäle, Eingänge)."""
    out = []
    kind = device_kind(dev)
    for it in [names["device"], *names["channels"]]:
        if not it.get("shelly"):
            continue
        k = kind if it["key"] == "device" else ("input" if it.get("kind") == "input" else "channel")
        issues = check_rules(it["shelly"], it.get("ha_area"), k)
        fix = suggest_name(it["shelly"], it.get("ha_area"), k) if issues else None
        if it["key"] == "device" and any(i["code"] == "single_module" for i in issues):
            fix = _function_name(dev, it) or fix
        for issue in issues:
            out.append({"key": it["key"], "name": it["shelly"], "suggest": fix, "area": it.get("ha_area"),
                        "kind": k, **issue})
    return out


# ── Prüfungen ─────────────────────────────────────────────────────────────────

# Texte je Hinweis; {what} = »Gerätename« oder »Kanalname«, {where} = »Gerät« oder »Kanal«
NAME_HINTS = {
    "no_area": ("info", "{where} hat in HA keinen Bereich"),
    "ha_area_in_name": ("warning", "{what} in HA enthält den Bereich – Anzeige und Entity-IDs doppeln ihn"),
    "ha_no_user_name": ("info", "{what} in HA nicht eigens gesetzt – HA übernimmt den Shelly-Namen"),
    "shelly_empty": ("warning", "{what} im Shelly ist leer"),
    "shelly_no_area": ("info", "{what} im Shelly beginnt mit keinem HA-Bereich"),
    "generic": ("warning", "Kanalname ist nur ein Standardname"),
    "duplicate_channel": ("warning", "Kanalname im Shelly mehrfach im selben Gerät vergeben"),
    "input_unnamed": ("info", "Eingang ohne Namen – benennen oder deaktivieren"),
    "single_channel_named": ("warning", "Einkanaliges Gerät hat einen Kanalnamen im Shelly – HA hängt ihn an die Entity-ID an"),
}


def hint_text(hint: str, scope: str) -> tuple[str, str]:
    sev, text = NAME_HINTS.get(hint, ("info", hint))
    what, where = ("Gerätename", "Gerät") if scope == "device" else ("Kanalname", "Kanal")
    return sev, text.format(what=what, where=where)


def is_stale_reservation(fixed_ip: str | None, current_ip: str | None, fixed_range: str | None) -> bool:
    """Reservierung, die nicht mehr passt: außerhalb des Bereichs für feste IPs (falls gesetzt),
    sonst außerhalb des /16-Netzes, in dem das Gerät heute ist (z. B. nach einem Netzumzug)."""
    import ipaddress

    try:
        fixed = ipaddress.ip_address(fixed_ip or "")
    except ValueError:
        return False
    try:
        if fixed_range:
            return fixed not in ipaddress.ip_network(fixed_range, strict=False)
        if current_ip:
            return fixed not in ipaddress.ip_network(f"{current_ip}/16", strict=False)
    except ValueError:
        return False
    return False



def problems(dev: dict, names: dict | None = None, rules: list[dict] | None = None) -> list[dict]:
    out: list[dict] = []

    def add(code: str, severity: str, text: str, **extra) -> None:
        out.append({"code": code, "severity": severity, "text": text, **extra})

    ha = dev.get("ha")
    sh = dev.get("shelly") or {}
    config, status = sh.get("config"), sh.get("status")
    if ha is None:
        add("not_in_ha", "warning", "Im Netzwerk gefunden, aber nicht in Home Assistant eingebunden")
    elif ha.get("entry_state") not in (None, "loaded"):
        add("entry_state", "error", f"Shelly-Integration kann das Gerät nicht laden: {ha.get('entry_state')}")
    elif not ha.get("available", True) and not dev.get("sleeping"):
        add("unavailable", "error", "In Home Assistant nicht verfügbar")
    if sh.get("error"):
        add("fetch_error", "warning", f"Abfrage fehlgeschlagen: {sh['error']}")
    if dev.get("gen") == 1:
        add("gen1", "info", "Gen1 – wird nicht verwaltet")
    hist = dev.get("history") or {}
    if (hist.get("reboots_24h") or 0) >= 3:
        add("reboots", "warning", f"Häufige Neustarts ({hist['reboots_24h']}× in 24 h)")
    if hist.get("crashes_7d"):
        last = hist.get("last_reboot") or {}
        add("crash", "warning", f"Absturz in den letzten 7 Tagen ({hist['crashes_7d']}×, zuletzt {last.get('reason')})")
    want_title = (_get(config, "sys", "device", "name") or "").strip() if config else ""
    if ha and want_title and ha.get("entry_title") and " ".join(ha["entry_title"].split()) != " ".join(want_title.split()):
        add("entry_title", "info", f"Integrationseintrag heißt anders: »{ha['entry_title']}«")
    uf = dev.get("unifi") or {}
    if uf.get("found") and ha:
        want = (_get(config, "sys", "device", "name") or "").strip() if config else ""
        if want and want != (uf.get("name") or ""):
            add("unifi_name", "info", f"Name in UniFi weicht ab: »{uf.get('name') or '(kein Alias)'}«")
        fixed = uf.get("fixed_ip") or ""
        if uf.get("use_fixedip") and fixed and uf.get("ip") and fixed != uf["ip"]:
            add("unifi_fixed_mismatch", "warning", f"Feste IP in UniFi ({fixed}) passt nicht zur aktuellen ({uf['ip']})")
        if fixed and is_stale_reservation(fixed, uf.get("ip"), None):
            add("unifi_stale", "info", f"Alte Reservierung in UniFi ({fixed})")
    if (hist.get("outages_24h") or 0) >= 3:
        add("outages", "warning", f"Häufig offline ({hist['outages_24h']}× in 24 h)")

    if status:
        upd = _get(status, "sys", "available_updates") or {}
        if upd.get("stable"):
            add("update", "info", f"Firmware-Update verfügbar: {upd['stable'].get('version')}")
        if _get(status, "sys", "restart_required"):
            add("restart_required", "warning", "Neustart erforderlich, damit Änderungen wirken")
        hist = dev.get("history") or {}
        rssi = _get(status, "wifi", "rssi")
        # Mit eigenem Verlauf zählt der 24-h-Durchschnitt, sonst der aktuelle Wert
        avg = hist.get("rssi_avg") if (hist.get("rssi_samples") or 0) >= 6 else None
        level = avg if avg is not None else rssi
        if isinstance(level, (int, float)):
            detail = f"Ø 24 h {avg} dBm, min {hist.get('rssi_min')}" if avg is not None else f"{rssi} dBm"
            if level <= -82:
                add("rssi", "error", f"Sehr schwaches WLAN ({detail})")
            elif level <= -75:
                add("rssi", "warning", f"Schwaches WLAN ({detail})")
        if avg is not None and hist.get("rssi_max") is not None and hist["rssi_max"] - hist["rssi_min"] >= 15:
            add("rssi_jitter", "info", f"WLAN schwankt stark ({hist['rssi_min']} bis {hist['rssi_max']} dBm in 24 h)")
        if config:
            if _get(config, "cloud", "enable") and _get(status, "cloud", "connected") is False:
                add("cloud", "warning", "Cloud aktiviert, aber nicht verbunden")
            if _get(config, "mqtt", "enable") and _get(status, "mqtt", "connected") is False:
                add("mqtt", "warning", "MQTT aktiviert, aber nicht verbunden")
            if _get(config, "ws", "enable") and _get(status, "ws", "connected") is False:
                add("ws", "warning", "Ausgehender WebSocket aktiviert, aber nicht verbunden")
        if "sys" in status and not _get(status, "sys", "time") and not dev.get("sleeping"):
            add("time", "warning", "Uhrzeit nicht synchronisiert")
        for key in component_keys(status, "switch"):
            t = _get(status, key, "temperature", "tC")
            if isinstance(t, (int, float)) and t >= 80:
                add("temperature", "warning", f"Hohe Temperatur {key}: {t:.0f} °C")
            errs = _get(status, key, "errors") or []
            if errs:
                add("channel_error", "error", f"{key}: {', '.join(errs)}")
    if config:
        if _get(config, "wifi", "ap", "enable") and _get(config, "wifi", "ap", "is_open", default=True):
            add("ap_open", "warning", "Geräte-Access-Point ist offen")

    for helper in dev.get("helpers") or []:
        if helper.get("target_domain") == "light":
            add("switch_as_light", "warning",
                f"Als Licht über Helfer {helper['entity_id']} – besser Consumption Type »light« im Shelly setzen",
                entity_id=helper["entity_id"], source=helper.get("source"))

    if names:
        for it in [names["device"], *names["channels"]]:
            scope = "device" if it["key"] == "device" else "channel"
            for hint in it["hints"]:
                sev, text = hint_text(hint, scope)
                detail = it["shelly"] if hint.startswith("shelly") or hint in ("generic", "single_channel_named") else it["ha_name"]
                if scope == "channel":
                    suffix = f": {it['key']} »{detail}«" if detail else f": {it['key']}"
                else:
                    suffix = f": »{detail}«" if detail else ""
                add(f"name_{scope}_{hint}", sev, text + suffix, key=it["key"])
        if names["device"]["status"] == "diff":
            add("name_device_diff", "info", "Gerätename in Shelly und HA unterschiedlich")
        diff_ch = [c["key"] for c in names["channels"] if c["status"] == "diff"]
        if diff_ch:
            add("name_channel_diff", "info", f"Kanalnamen in Shelly und HA unterschiedlich: {', '.join(diff_ch)}")
    for ri in rules or []:
        where = "" if ri["key"] == "device" else f"{ri['key']} "
        add(f"rule_{ri['code']}", RULE_SEVERITY.get(ri["code"], "info"), f"Namensregel – {ri['text']}: {where}»{ri['name']}«",
            key=ri["key"])
    out.sort(key=lambda p: SEVERITY_ORDER.get(p["severity"], 9))
    return out


# ── Namens-Stufen ─────────────────────────────────────────────────────────────
# Erst den Namen im Shelly festlegen, dann HA angleichen, dann daraus abgeleitete Namen übernehmen.
STAGES = {
    1: "Gerätename im Shelly festlegen",
    2: "Kanalnamen im Shelly festlegen",
    3: "Home Assistant angleichen",
    4: "Abgeleitete Namen übernehmen (Integration, UniFi)",
}
STAGE2_HINTS = ("ha_area_in_name", "ha_no_user_name", "no_area")


def problem_stage(code: str, key: str | None = None) -> int | None:
    if code in ("entry_title", "unifi_name"):
        return 4
    if code.startswith("name_") and code.endswith(STAGE2_HINTS):
        return 3
    if code.startswith("name_channel_"):
        return 2
    if code.startswith("rule_"):
        return 1 if key in (None, "device") else 2
    if code.startswith("name_") or code == "duplicate_name":
        return 1
    return None


def finalize_stages(row: dict) -> None:
    """Stufe je Problem setzen und Probleme späterer Stufen als blockiert markieren."""
    stages = []
    for p in row["problems"]:
        p["stage"] = problem_stage(p["code"], p.get("key"))
        if p["stage"]:
            stages.append(p["stage"])
    row["name_stage"] = min(stages) if stages else None  # None = Namensgebung fertig
    for p in row["problems"]:
        p["blocked"] = bool(p["stage"] and row["name_stage"] and p["stage"] > row["name_stage"])


def fleet_problems(rows: list[dict]) -> None:
    """Probleme, die nur im Vergleich mehrerer Geräte auffallen (in-place)."""
    by_name: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("shelly_name"):
            by_name.setdefault(norm(r["shelly_name"]), []).append(r)
    for same in by_name.values():
        if len(same) > 1:
            for r in same:
                r["problems"].append({"code": "duplicate_name", "severity": "warning",
                                      "text": f"Gerätename {len(same)}× vergeben"})
    # Namensregel »Pflicht, sobald es mehrere gibt«: gleicher Kern im selben Bereich ohne Lage
    entries = []
    for r in rows:
        for it in [r["names"]["device"], *r["names"]["channels"]]:
            # Kanalname eines Einkanalers wird ohnehin als eigener Fehler gemeldet (und doppelt sonst den Gerätenamen)
            if it.get("shelly") and "single_channel_named" not in it["hints"]:
                entries.append((f"{r['mac']}|{it['key']}", it.get("ha_area") or r.get("area"), it["shelly"]))
    amb = ambiguous(entries)
    by_mac = {r["mac"]: r for r in rows}
    for nid in sorted(amb):
        mac, key = nid.split("|", 1)
        r = by_mac[mac]
        it = next(x for x in [r["names"]["device"], *r["names"]["channels"]] if x["key"] == key)
        where = "" if key == "device" else f"{key} "
        r["rule_issues"].append({"key": key, "name": it["shelly"], "code": "ambiguous", "text": RULES["ambiguous"]})
        r["problems"].append({"code": "rule_ambiguous", "severity": "info", "key": key,
                              "text": f"Namensregel – {RULES['ambiguous']}: {where}»{it['shelly']}«"})
    for r in rows:
        r["problems"].sort(key=lambda p: SEVERITY_ORDER.get(p["severity"], 9))
        r["severity"] = r["problems"][0]["severity"] if r["problems"] else None
        finalize_stages(r)


# ── Tabellenzeile ─────────────────────────────────────────────────────────────

def _channels(dev: dict, names: dict) -> list[dict]:
    sh = dev.get("shelly") or {}
    config, status = sh.get("config"), sh.get("status")
    ha = dev.get("ha") or {}
    ctypes = consumption_types(config)
    name_items = {c["key"]: c for c in names["channels"]}
    keys = output_keys(config, status)
    for comp in METER_COMPONENTS:
        keys.extend(component_keys(config or status, comp))
    if not keys:
        # Ohne Shelly-Daten: Kanäle mit Schalt- oder Leistungs-Entity (Eingänge laufen über die Namensliste)
        keys = sorted(k for k, v in (ha.get("entities") or {}).items() if v.get("switch") or v.get("power"))
    out = []
    for key in keys:
        m = KEY_RE.match(key)
        comp, idx = (m.group(1), int(m.group(2))) if m else (key, 0)
        cfg = _get(config, key) or {}
        st = _get(status, key) or {}
        ents = (ha.get("entities") or {}).get(key) or {}
        out.append({
            "key": key, "component": comp, "index": idx,
            "name": cfg.get("name"),
            "names": name_items.get(key),
            "consumption_type": ctypes[idx] if comp == "switch" and idx < len(ctypes) else None,
            "initial_state": cfg.get("initial_state"),
            "in_mode": cfg.get("in_mode"),
            "auto_on": cfg.get("auto_on"), "auto_on_delay": cfg.get("auto_on_delay"),
            "auto_off": cfg.get("auto_off"), "auto_off_delay": cfg.get("auto_off_delay"),
            "output": st.get("output"),
            "apower": next((st[k] for k in ("apower", "act_power", "total_act_power") if isinstance(st.get(k), (int, float))), None),
            "temperature": _get(st, "temperature", "tC"),
            "entity": ents.get("switch"), "power_entity": ents.get("power"),
            "ha_device_id": ents.get("device_id") or ((ha.get("channels") or {}).get(key) or {}).get("device_id"),
        })
    return out


def build_row(dev: dict, areas_by_id: dict[str, str]) -> dict:
    """Flache Zeile für die Tabelle (Spalten siehe frontend/columns.js)."""
    ha = dev.get("ha") or {}
    sh = dev.get("shelly") or {}
    info, config, status = sh.get("info") or {}, sh.get("config"), sh.get("status")
    names = analyze_names(dev, areas_by_id)
    area = areas_by_id.get(ha.get("area_id") or "")
    channels = _channels(dev, names)
    powers = [c["apower"] for c in channels if isinstance(c["apower"], (int, float))]
    upd = _get(status, "sys", "available_updates") or {}
    host = dev.get("host") or _get(status, "wifi", "sta_ip")
    port = dev.get("port") or 80
    row = {
        "mac": dev["mac"],
        "name": compose(area, _ha_effective(ha)) if ha else (names["device"]["shelly"] or info.get("name") or dev["mac"]),
        "shelly_name": names["device"]["shelly"],
        "ha_name": _ha_effective(ha) if ha else None,
        "area": area, "area_id": ha.get("area_id"),
        "name_status": names["status"], "names": names,
        "model": dev.get("model") or info.get("model"), "model_id": dev.get("model_id") or info.get("model"),
        "app": info.get("app") or dev.get("app"), "gen": dev.get("gen") or info.get("gen"),
        "host": host, "port": port,
        "url": f"http://{host}" + ("" if port in (80, None) else f":{port}") if host else None,
        "fw": info.get("ver") or dev.get("fw"), "fw_id": info.get("fw_id"),
        "update": _get(upd, "stable", "version"), "beta": _get(upd, "beta", "version"),
        "uptime": _get(status, "sys", "uptime"),
        "restart_required": _get(status, "sys", "restart_required"),
        "rssi": _get(status, "wifi", "rssi"), "ssid": _get(status, "wifi", "ssid"),
        "bssid": _get(status, "wifi", "bssid"),
        "rssi_avg": (dev.get("history") or {}).get("rssi_avg"), "rssi_min": (dev.get("history") or {}).get("rssi_min"),
        "rssi_max": (dev.get("history") or {}).get("rssi_max"),
        "reboots_24h": (dev.get("history") or {}).get("reboots_24h"),
        "last_reboot": (dev.get("history") or {}).get("last_reboot"),
        "outages_24h": (dev.get("history") or {}).get("outages_24h"),
        "offline_since": (dev.get("history") or {}).get("offline_since"),
        "unifi": dev.get("unifi"),
        "fallback": dev.get("fallback"),
        "wifi_status": _get(status, "wifi", "status"),
        "eco": _get(config, "sys", "device", "eco_mode"),
        "discoverable": _get(config, "sys", "device", "discoverable"),
        "ble": _get(config, "ble", "enable"), "ble_rpc": _get(config, "ble", "rpc", "enable"),
        "ble_observer": _get(config, "ble", "observer", "enable"),
        "cloud": _get(config, "cloud", "enable"), "cloud_connected": _get(status, "cloud", "connected"),
        "mqtt": _get(config, "mqtt", "enable"), "mqtt_connected": _get(status, "mqtt", "connected"),
        "mqtt_server": _get(config, "mqtt", "server"),
        "ws": _get(config, "ws", "enable"), "ws_connected": _get(status, "ws", "connected"),
        "ws_server": _get(config, "ws", "server"),
        "ap": _get(config, "wifi", "ap", "enable"),
        "sta1": _get(config, "wifi", "sta1", "enable"),
        "auth": info.get("auth_en"),
        "tz": _get(config, "sys", "location", "tz"), "sntp": _get(config, "sys", "sntp", "server"),
        "debug_ws": _get(config, "sys", "debug", "websocket", "enable"),
        "channel_count": len([c for c in channels if c["component"] in OUTPUT_COMPONENTS + ("cover",)]),
        "channels": channels,
        "power": round(sum(powers), 1) if powers else None,
        "temperature": max((c["temperature"] for c in channels if isinstance(c["temperature"], (int, float))),
                           default=None),
        "consumption_types": sorted({c["consumption_type"] for c in channels if c["consumption_type"]}),
        "initial_states": sorted({c["initial_state"] for c in channels if c["initial_state"]}),
        "in_modes": sorted({c["in_mode"] for c in channels if c["in_mode"]}),
        "sleeping": bool(dev.get("sleeping")),
        "sources": sorted(dev.get("sources") or []),
        "in_ha": bool(dev.get("ha")),
        "ha_device_id": ha.get("device_id"), "ha_entry_id": ha.get("entry_id"), "entry_title": ha.get("entry_title"),
        "ha_state": ha.get("entry_state"), "available": ha.get("available") if ha else None,
        "entity_ids": ha.get("entity_ids") or [],
        "helpers": dev.get("helpers") or [],
        "fetched_at": sh.get("fetched_at"), "live": bool(sh.get("live")), "fetch_error": sh.get("error"),
        "has_config": config is not None,
    }
    row["kind"] = device_kind(dev)
    row["rule_issues"] = rule_issues(dev, names)
    row["problems"] = problems(dev, names, row["rule_issues"])
    finalize_stages(row)
    return row


def build_rows(devices: Iterable[dict], areas_by_id: dict[str, str]) -> list[dict]:
    rows = [build_row(d, areas_by_id) for d in devices]
    fleet_problems(rows)
    return sorted(rows, key=lambda r: norm(r["name"]))


# ── Namen schreiben ───────────────────────────────────────────────────────────

RPC_COMPONENT = {"switch": "Switch", "cover": "Cover", "light": "Light", "rgb": "RGB", "rgbw": "RGBW",
                 "cct": "CCT", "em1": "EM1", "pm1": "PM1", "input": "Input"}


def shelly_name_call(key: str, name: str | None) -> tuple[str, dict]:
    """RPC-Aufruf, der den Namen von Gerät (key »device«) oder Kanal setzt; leer = Name löschen."""
    value = (name or "").strip() or None
    if key == "device":
        return "Sys.SetConfig", {"config": {"device": {"name": value}}}
    m = KEY_RE.match(key)
    if not m or m.group(1) not in RPC_COMPONENT:
        raise ValueError(f"Unbekannter Kanal {key}")
    return f"{RPC_COMPONENT[m.group(1)]}.SetConfig", {"id": int(m.group(2)), "config": {"name": value}}


def _ha_target(it: dict, row: dict, area: str | None, area_ids: dict[str, str]) -> dict:
    """Ziel in HA: Gerät/Sub-Gerät oder (bei Eingängen) die Entity. Entities erben den Gerätebereich."""
    if it.get("ha_entity_id"):
        inherit = area is None or area == row.get("area")
        return {"target": "ha", "entity_id": it["ha_entity_id"], "area_id": None if inherit else area_ids.get(area)}
    return {"target": "ha", "device_id": it["ha_device_id"], "area_id": area_ids.get(area) if area else None}


def name_ops(row: dict, direction: str, keys: Iterable[str] | None, area_ids: dict[str, str],
             only: Iterable[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Schreiboperationen für eine Zeile (build_row) in eine Richtung.

    direction »to_shelly«: HA gewinnt, Shelly bekommt Bereich + Name (Kanal bei Einkanalern leer).
    direction »to_ha«:     Shelly gewinnt, HA bekommt den reinen Namen und den erkannten Bereich.
    Bei »to_shelly« wird in HA zusätzlich der reine Name als eigener Name festgeschrieben.
    area_ids: Bereichsname → area_id. only: optional »key|target« zum Einschränken. Gibt (ops, skipped) zurück.
    """
    names = row["names"]
    wanted = set(keys) if keys else None
    ops: list[dict] = []
    skipped: list[dict] = []
    for it in [names["device"], *names["channels"]]:
        if wanted is not None and it["key"] not in wanted:
            continue
        base = {"mac": row["mac"], "key": it["key"], "direction": direction, "device": row["name"],
                "area": it.get("ha_area") or row.get("area"), "item_kind": it.get("kind"),
                "label": "Gerät" if it["key"] == "device" else f"{'Eingang' if it.get('kind') == 'input' else 'Kanal'} {it['key']}"}
        if it["shelly"] is None:
            skipped.append({**base, "reason": "Shelly-Daten fehlen (Gerät nicht abgefragt oder offline)"})
            continue
        if it.get("unnamed"):
            skipped.append({**base, "reason": "Eingang ohne Namen – in der Detailansicht benennen oder deaktivieren"})
            continue
        if direction == "to_shelly":
            single = "single_channel_named" in it["hints"]
            if not single and it["ha_full"] is None:
                skipped.append({**base, "reason": "Kein Gegenstück in Home Assistant"})
                continue
            if not single and it["key"] != "device" and is_generic(it["ha_name"]):
                skipped.append({**base, "reason": f"HA hat nur einen Standardnamen (»{it['ha_name']}«)"})
                continue
            new = "" if single else it["to_shelly"]
            if (new or "") != (it["shelly"] or ""):
                if row.get("sleeping"):
                    skipped.append({**base, "reason": "Schlafendes Akku-Gerät – erst beim Aufwachen erreichbar"})
                else:
                    method, params = shelly_name_call(it["key"], new)
                    ops.append({**base, "target": "shelly", "old": it["shelly"], "new": new, "method": method, "params": params})
            # HA im selben Zug festschreiben: eigener Name ohne Bereich, sonst übernimmt HA den vollen Shelly-Namen
            if not single and (it.get("ha_device_id") or it.get("ha_entity_id")):
                pure = strip_area(it["ha_name"], it["ha_area"])
                if pure and pure != (it["ha_name_by_user"] or ""):
                    ops.append({**base, **_ha_target(it, row, it["ha_area"], area_ids),
                                "old": {"name": it["ha_name_by_user"] or f"({it['ha_name']} vom Shelly)", "area": it["ha_area"]},
                                "new": {"name": pure, "area": it["ha_area"]}, "name_by_user": pure})
        else:
            if it.get("ha_device_id") is None and it.get("ha_entity_id") is None:
                continue  # z. B. Kanalname eines Einkanalers: in HA kein eigenes Gerät
            to = it["to_ha"] or {}
            name, area = to.get("name") or "", to.get("area")
            if not name:
                reason = "Kein Name im Shelly" if not (it["shelly"] or "").strip() else "Shelly-Name besteht nur aus dem Bereich"
                skipped.append({**base, "reason": reason})
                continue
            area_id = area_ids.get(area) if area else None
            if area and area_id is None:
                skipped.append({**base, "reason": f"Bereich »{area}« nicht gefunden"})
                continue
            if name == (it["ha_name_by_user"] or "") and area == it["ha_area"]:
                continue
            ops.append({**base, **_ha_target(it, row, area, area_ids),
                        "old": {"name": it["ha_name"], "area": it["ha_area"]},
                        "new": {"name": name, "area": area}, "name_by_user": name})
    if only is not None:
        allowed = set(only)
        ops = [o for o in ops if f"{o['key']}|{o['target']}" in allowed]
    return ops, skipped
