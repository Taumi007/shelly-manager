"""Firmware: verfügbare Versionen und was ein Neustart mit den Ausgängen machen würde.

Bewusst ohne Home-Assistant-Import (testbar).
Ein Update startet das Gerät neu. Danach setzt der Shelly jeden Ausgang nach »initial_state«:
    off           → aus            on            → ein
    restore_last  → wie vorher     match_input   → wie der zugehörige Eingang
"""

from __future__ import annotations

from typing import Any

OUTPUT_COMPONENTS = ("switch", "light", "rgb", "rgbw", "cct")
STAGES = ("stable", "beta")
HIGH_LOAD_W = 20.0  # ab hier ist »geht aus« besonders kritisch (z. B. Kühlschrank)


def _keys(src: dict, comp: str) -> list[str]:
    out = []
    for k in src or {}:
        c, _, i = k.partition(":")
        if c == comp and i.isdigit():
            out.append((int(i), k))
    return [k for _, k in sorted(out)]


def available(status: dict | None, stage: str) -> str | None:
    """Zielversion für »stable« oder »beta«, sonst None."""
    upd = ((status or {}).get("sys") or {}).get("available_updates") or {}
    return (upd.get(stage) or {}).get("version")


def reboot_effects(config: dict | None, status: dict | None, names: dict[str, str] | None = None) -> list[dict]:
    """Pro Ausgang: aktueller Zustand, Zustand nach Neustart und ob er sich ändert."""
    config, status = config or {}, status or {}
    out = []
    for comp in OUTPUT_COMPONENTS:
        for key in _keys(config, comp):
            cfg, st = config.get(key) or {}, status.get(key) or {}
            now = st.get("output")
            init = cfg.get("initial_state")
            idx = key.split(":")[1]
            if init == "off":
                after = False
            elif init == "on":
                after = True
            elif init == "match_input":
                after = (status.get(f"input:{idx}") or {}).get("state")
            else:  # restore_last oder unbekannt
                after = now
            power = st.get("apower")
            change = isinstance(now, bool) and isinstance(after, bool) and now != after
            severity = None
            if change:
                severity = "error" if (now and isinstance(power, (int, float)) and power >= HIGH_LOAD_W) else "warning"
            out.append({"key": key, "name": (names or {}).get(key) or cfg.get("name"), "initial_state": init,
                        "now": now, "after": after, "power": power, "change": change, "severity": severity})
    return out


def preview(mac: str, info: dict | None, config: dict | None, status: dict | None, stage: str, *,
            sleeping: bool = False, names: dict[str, str] | None = None) -> dict[str, Any]:
    current = (info or {}).get("ver")
    target = available(status, stage)
    effects = reboot_effects(config, status, names)
    risky = [e for e in effects if e["change"]]
    if sleeping:
        state, reason = "skip", "Schlafendes Akku-Gerät – aktualisiert sich beim Aufwachen selbst über die App/Cloud"
    elif config is None:
        state, reason = "skip", "Keine Daten (offline?)"
    elif not target:
        state, reason = "current", "Kein Update verfügbar"
    else:
        state, reason = "update", None
    return {"mac": mac, "current": current, "target": target, "state": state, "reason": reason,
            "effects": effects, "risky": risky}
