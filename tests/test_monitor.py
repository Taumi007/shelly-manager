"""Tests für monitor.py – python3 tests/test_monitor.py"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("sm_mon", Path(__file__).parents[1] / "monitor.py")
mon = importlib.util.module_from_spec(_spec)
sys.modules["sm_mon"] = mon
_spec.loader.exec_module(mon)

T0 = 1_800_000_000


def test_rings_and_summary():
    h = mon.empty()
    for i in range(400):  # 400 × 5 min > 24 h
        mon.sample(h, "M", T0 + i * 300, rssi=-60 - (i % 10))
    d = h["devices"]["M"]
    assert len(d["fine"]["v"]) == mon.FINE_SLOTS
    s = mon.summary(h, "M", T0 + 399 * 300)
    assert s["rssi_min"] == -69 and s["rssi_max"] == -61 and -66 <= s["rssi_avg"] <= -63
    ser = mon.series(h, "M")
    assert ser["coarse"]["v"][-1][0] <= ser["coarse"]["v"][-1][1] <= ser["coarse"]["v"][-1][2]


def test_gap_filled_with_none():
    h = mon.empty()
    mon.sample(h, "M", T0, rssi=-50)
    mon.sample(h, "M", T0 + 3 * 600, rssi=-55)
    assert h["devices"]["M"]["fine"]["v"] == [-50, None, None, -55]


def test_reboot_and_outage():
    h = mon.empty()
    assert mon.sample(h, "M", T0, uptime=10_000, available=True) == []
    assert mon.sample(h, "M", T0 + 300, uptime=10_300) == []
    ev = mon.sample(h, "M", T0 + 600, uptime=40, reset_reason=9, available=False)
    assert ev == ["reboot", "offline"]
    s = mon.summary(h, "M", T0 + 700)
    assert s["reboots_24h"] == 1 and s["crashes_7d"] == 1 and s["last_reboot"]["reason"].startswith("Unterspannung")
    assert s["offline_since"] == T0 + 600
    assert mon.sample(h, "M", T0 + 900, uptime=340, available=True) == ["online"]
    assert mon.summary(h, "M", T0 + 1000)["offline_since"] is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
