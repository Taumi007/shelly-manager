"""Überwachung: eigener RSSI-Verlauf, Neustart- und Ausfall-Erkennung.

Bewusst ohne Home-Assistant-Import (testbar). Die RSSI-Entities sind im Recorder ausgeschlossen,
deshalb führt der Manager einen kompakten eigenen Verlauf:

    fein   10-Minuten-Werte der letzten 24 h          (144 Plätze)
    grob   Stundenwerte min/Ø/max der letzten 14 Tage   (336 Plätze)

Gespeichert als Ringe mit festem Raster (Startzeit + Werte), nicht als Zeitstempel pro Punkt.
"""

from __future__ import annotations

FINE_STEP, FINE_SLOTS = 600, 144
COARSE_STEP, COARSE_SLOTS = 3600, 336
MAX_EVENTS = 30
DAY = 86400

# ESP-IDF esp_reset_reason_t, wie Shelly sie in sys.reset_reason meldet
RESET_REASONS = {
    0: ("unbekannt", False), 1: ("Strom eingeschaltet", False), 2: ("externer Reset", False),
    3: ("Software-Neustart", False), 4: ("Absturz (Exception)", True), 5: ("Watchdog (Interrupt)", True),
    6: ("Watchdog (Task)", True), 7: ("Watchdog", True), 8: ("Aufwachen aus Tiefschlaf", False),
    9: ("Unterspannung (Brownout)", True), 10: ("SDIO-Reset", False),
}


def empty() -> dict:
    return {"version": 1, "devices": {}}


def _ring(step: int, slots: int) -> dict:
    return {"step": step, "slots": slots, "t0": None, "v": []}


def _push(ring: dict, ts: float, value) -> None:
    """Wert in den Slot von ts schreiben; Lücken mit None füllen, Ältestes verwerfen."""
    slot = int(ts // ring["step"])
    if ring["t0"] is None:
        ring["t0"], ring["v"] = slot, [value]
        return
    end = ring["t0"] + len(ring["v"]) - 1
    if slot < ring["t0"]:
        return
    if slot <= end:
        ring["v"][slot - ring["t0"]] = value
    else:
        ring["v"].extend([None] * (slot - end - 1))
        ring["v"].append(value)
    over = len(ring["v"]) - ring["slots"]
    if over > 0:
        ring["v"] = ring["v"][over:]
        ring["t0"] += over


def _coarse_push(ring: dict, ts: float, rssi: int) -> None:
    """Stundenwert als [min, Summe, Anzahl, max] – Ø wird beim Auslesen gebildet."""
    slot = int(ts // ring["step"])
    cur = None
    if ring["t0"] is not None and ring["t0"] <= slot < ring["t0"] + len(ring["v"]):
        cur = ring["v"][slot - ring["t0"]]
    if cur is None:
        cur = [rssi, rssi, 1, rssi]
    else:
        cur = [min(cur[0], rssi), cur[1] + rssi, cur[2] + 1, max(cur[3], rssi)]
    _push(ring, ts, cur)


def device(hist: dict, mac: str) -> dict:
    d = hist["devices"].setdefault(mac, {})
    d.setdefault("fine", _ring(FINE_STEP, FINE_SLOTS))
    d.setdefault("coarse", _ring(COARSE_STEP, COARSE_SLOTS))
    d.setdefault("reboots", [])
    d.setdefault("outages", [])
    return d


def sample(hist: dict, mac: str, ts: float, *, rssi=None, uptime=None, reset_reason=None,
           available: bool | None = None, sleeping: bool = False) -> list[str]:
    """Einen Messpunkt verarbeiten. Gibt erkannte Ereignisse zurück (»reboot«, »offline«, »online«)."""
    d = device(hist, mac)
    events: list[str] = []
    if isinstance(rssi, (int, float)):
        _push(d["fine"], ts, int(rssi))
        _coarse_push(d["coarse"], ts, int(rssi))
    if isinstance(uptime, (int, float)) and not sleeping:
        last = d.get("uptime")
        last_ts = d.get("uptime_ts")
        # Neustart: Laufzeit ist kleiner als die seit der letzten Messung erwartete (10 % + 60 s Toleranz)
        if last is not None and last_ts is not None:
            expected = last + (ts - last_ts)
            if uptime + 60 < expected * 0.9:
                reason, crash = RESET_REASONS.get(reset_reason, (f"Code {reset_reason}", False))
                d["reboots"].append({"ts": round(ts - uptime), "reason": reason, "code": reset_reason, "crash": crash})
                del d["reboots"][:-MAX_EVENTS]
                events.append("reboot")
        d["uptime"], d["uptime_ts"] = uptime, ts
    if available is not None and not sleeping:
        was = d.get("available")
        if was is True and available is False:
            d["outages"].append({"start": ts, "end": None})
            del d["outages"][:-MAX_EVENTS]
            events.append("offline")
        elif was is False and available is True and d["outages"] and d["outages"][-1]["end"] is None:
            d["outages"][-1]["end"] = ts
            events.append("online")
        d["available"] = available
    return events


def _fine_values(d: dict, now: float, window: int) -> list[int]:
    ring = d["fine"]
    if ring["t0"] is None:
        return []
    first = int((now - window) // ring["step"])
    return [v for i, v in enumerate(ring["v"]) if v is not None and ring["t0"] + i >= first]


def summary(hist: dict, mac: str, now: float) -> dict:
    """Kennzahlen für Tabelle und Prüfungen."""
    d = hist["devices"].get(mac)
    if not d:
        return {}
    vals = _fine_values(d, now, DAY)
    reboots = [r for r in d["reboots"] if r["ts"] >= now - DAY]
    crashes = [r for r in d["reboots"] if r.get("crash") and r["ts"] >= now - 7 * DAY]
    outages = [o for o in d["outages"] if (o["end"] or now) >= now - DAY]
    return {
        "rssi_avg": round(sum(vals) / len(vals)) if vals else None,
        "rssi_min": min(vals) if vals else None,
        "rssi_max": max(vals) if vals else None,
        "rssi_samples": len(vals),
        "reboots_24h": len(reboots),
        "crashes_7d": len(crashes),
        "last_reboot": d["reboots"][-1] if d["reboots"] else None,
        "outages_24h": len(outages),
        "offline_since": d["outages"][-1]["start"] if d["outages"] and d["outages"][-1]["end"] is None else None,
    }


def series(hist: dict, mac: str) -> dict:
    """Verlauf fürs Panel: fein als Werte, grob als [min, Ø, max]."""
    d = hist["devices"].get(mac)
    if not d:
        return {}
    fine, coarse = d["fine"], d["coarse"]
    return {
        "fine": {"t0": (fine["t0"] or 0) * fine["step"], "step": fine["step"], "v": fine["v"]},
        "coarse": {"t0": (coarse["t0"] or 0) * coarse["step"], "step": coarse["step"],
                   "v": [None if c is None else [c[0], round(c[1] / c[2]), c[3]] for c in coarse["v"]]},
        "reboots": d["reboots"], "outages": d["outages"],
    }
