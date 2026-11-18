"""Tests für firmware.py – python3 tests/test_firmware.py"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("sm_fw", Path(__file__).parents[1] / "firmware.py")
fw = importlib.util.module_from_spec(_spec)
sys.modules["sm_fw"] = fw
_spec.loader.exec_module(fw)

CONFIG = {"switch:0": {"initial_state": "off"}, "switch:1": {"initial_state": "restore_last"},
          "switch:2": {"initial_state": "match_input"}, "switch:3": {"initial_state": "on"}}
STATUS = {"sys": {"available_updates": {"stable": {"version": "1.5.0"}}},
          "switch:0": {"output": True, "apower": 54.0}, "switch:1": {"output": True},
          "switch:2": {"output": True}, "input:2": {"state": False}, "switch:3": {"output": True}}


def test_effects():
    eff = {e["key"]: e for e in fw.reboot_effects(CONFIG, STATUS)}
    assert eff["switch:0"]["change"] and eff["switch:0"]["severity"] == "error"  # Kühlschrank ginge aus
    assert not eff["switch:1"]["change"]
    assert eff["switch:2"]["change"] and eff["switch:2"]["after"] is False
    assert not eff["switch:3"]["change"]


def test_preview():
    p = fw.preview("M", {"ver": "1.4.4"}, CONFIG, STATUS, "stable")
    assert p["state"] == "update" and p["target"] == "1.5.0" and len(p["risky"]) == 2
    assert fw.preview("M", {"ver": "1.4.4"}, CONFIG, STATUS, "beta")["state"] == "current"
    assert fw.preview("M", {}, None, None, "stable")["state"] == "skip"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
