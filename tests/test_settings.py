"""Tests für settings.py – laufen mit pytest oder direkt: python3 tests/test_settings.py"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("sm_settings", Path(__file__).parents[1] / "settings.py")
st = importlib.util.module_from_spec(_spec)
sys.modules["sm_settings"] = st
_spec.loader.exec_module(st)

CONFIG = {
    "sys": {"device": {"eco_mode": False, "name": "X"}, "ui_data": {}},
    "ble": {"enable": True, "rpc": {"enable": True}},
    "cloud": {"enable": True},
    "wifi": {"ap": {"enable": True}, "sta1": {"ssid": None, "enable": False}},
    "switch:0": {"initial_state": "off", "in_mode": "follow"},
    "switch:1": {"initial_state": "restore_last", "in_mode": "follow"},
}


def test_plan_and_merge_per_component():
    items = st.plan_device("M", CONFIG, [{"id": "ble_enable", "value": False}, {"id": "ble_rpc", "value": False},
                                         {"id": "ble_observer", "value": True}, {"id": "eco_mode", "value": True}])
    status = {i["setting"]: i["status"] for i in items}
    assert status == {"ble_enable": "change", "ble_rpc": "change", "ble_observer": "unsupported", "eco_mode": "change"}
    calls = st.build_calls(items, CONFIG)
    by = {m: p for m, p, _ in calls}
    assert by["BLE.SetConfig"] == {"config": {"enable": False, "rpc": {"enable": False}}}
    assert by["Sys.SetConfig"] == {"config": {"device": {"eco_mode": True}}}


def test_channels_all_and_single():
    items = st.plan_device("M", CONFIG, [{"id": "initial_state", "value": "restore_last"}])
    assert [(i["key"], i["status"]) for i in items] == [("switch:0", "change"), ("switch:1", "same")]
    calls = st.build_calls(items, CONFIG)
    assert calls == [("Switch.SetConfig", {"config": {"initial_state": "restore_last"}, "id": 0}, [items[0]])]
    items = st.plan_device("M", CONFIG, [{"id": "in_mode", "value": "detached", "channel": 1}])
    assert [i["key"] for i in items] == ["switch:1"]


def test_consumption_type_full_list():
    items = st.plan_device("M", CONFIG, [{"id": "consumption_type", "value": "light", "channel": 1}])
    calls = st.build_calls(items, CONFIG)
    assert calls[0][0] == "Sys.SetConfig"
    assert calls[0][1] == {"config": {"ui_data": {"consumption_types": ["", "light"]}}}


def test_secret_and_undo():
    items = st.plan_device("M", CONFIG, [{"id": "sta1_pass", "value": "geheim"}, {"id": "cloud", "value": False}])
    pub = [st.public(i) for i in items]
    assert all("geheim" not in str(p) for p in pub)
    for i in items:
        i["ok"] = True
    assert st.undo_changes(items) == [{"id": "cloud", "value": True}]


def test_validation():
    try:
        st.plan_device("M", CONFIG, [{"id": "initial_state", "value": "kaputt"}])
    except st.SettingError:
        pass
    else:
        raise AssertionError("ungültiger Wert akzeptiert")
    items = st.plan_device("M", None, [{"id": "cloud", "value": True}])
    assert items[0]["status"] == "offline"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
