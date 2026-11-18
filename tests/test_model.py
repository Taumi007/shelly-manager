"""Tests für model.py – laufen mit pytest oder direkt: python3 tests/test_model.py"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
_spec = importlib.util.spec_from_file_location("sm_model", Path(__file__).parents[1] / "model.py")
model = importlib.util.module_from_spec(_spec)
sys.modules["sm_model"] = model
_spec.loader.exec_module(model)

AREAS = {"a1": "Garten", "a2": "Garten Ost", "a3": "Schlafzimmer", "a4": "Garage", "a5": "Gang Keller"}


def plug(shelly_name="Schlafzimmer Steckdose West", ha_name="Schlafzimmer Steckdose West", user="Steckdose West",
         area="a3", ch_name=None):
    return {
        "mac": "AABBCCDDEEFF", "host": "192.168.1.10", "gen": 2, "sources": ["ha"],
        "ha": {"device_id": "d1", "entry_id": "e1", "entry_state": "loaded", "name": ha_name,
               "name_by_user": user, "area_id": area, "channels": {}, "entities": {}, "available": True},
        "shelly": {"info": {"ver": "1.5.0", "auth_en": True},
                   "config": {"sys": {"device": {"name": shelly_name}}, "switch:0": {"name": ch_name, "initial_state": "restore_last"}},
                   "status": {"sys": {"time": "12:00"}, "switch:0": {"output": True, "apower": 12.5}}},
    }


def pro4(channel_names, sub_users, sub_areas):
    keys = [f"switch:{i}" for i in range(4)]
    config = {"sys": {"device": {"name": "Garten Ost Modul"}}}
    status = {"sys": {"time": "12:00"}}
    channels = {}
    for i, k in enumerate(keys):
        config[k] = {"name": channel_names[i]}
        status[k] = {"output": False}
        channels[k] = {"device_id": f"s{i}", "name": channel_names[i], "name_by_user": sub_users[i], "area_id": sub_areas[i]}
    return {
        "mac": "112233445566", "host": "192.168.1.11", "gen": 2, "sources": ["ha"],
        "ha": {"device_id": "d2", "entry_id": "e2", "entry_state": "loaded", "name": "Garten Ost Modul",
               "name_by_user": "Modul", "area_id": "a2", "channels": channels, "entities": {}, "available": True},
        "shelly": {"info": {}, "config": config, "status": status},
    }


def test_split_area_longest_prefix():
    areas = AREAS.values()
    assert model.split_area("Garten Ost Fluter", areas) == ("Garten Ost", "Fluter")
    assert model.split_area("Garten Fluter", areas) == ("Garten", "Fluter")
    assert model.split_area("Gartenhaus Steckdose", areas) == (None, "Gartenhaus Steckdose")
    assert model.split_area("Garten Ost Fluter", areas, prefer="Garten") == ("Garten", "Ost Fluter")


def test_compose_no_double():
    assert model.compose("Schlafzimmer", "Steckdose West") == "Schlafzimmer Steckdose West"
    assert model.compose("Schlafzimmer", "Schlafzimmer Steckdose") == "Schlafzimmer Steckdose"
    assert model.compose(None, "X") == "X"


def test_single_ok():
    n = model.analyze_names(plug(), AREAS)
    assert n["status"] == "ok", n
    assert n["device"]["to_ha"] == {"area": "Schlafzimmer", "name": "Steckdose West"}
    assert not n["multi"]


def test_single_ha_name_contains_area():
    # Kein name_by_user: HA übernimmt den Shelly-Namen 1:1 → Bereich doppelt
    n = model.analyze_names(plug(user=None), AREAS)
    assert "ha_area_in_name" in n["issues"]
    assert n["device"]["status"] == "ok"
    assert n["status"] == "warn"


def test_single_channel_named():
    n = model.analyze_names(plug(ch_name="Fluter"), AREAS)
    assert "single_channel_named" in n["issues"]
    assert n["status"] == "diff"
    assert n["channels"][0]["to_shelly"] == ""


def test_diff_and_proposals():
    n = model.analyze_names(plug(shelly_name="Schlafzimmer Steckdose Ost"), AREAS)
    assert n["status"] == "diff"
    assert n["device"]["to_shelly"] == "Schlafzimmer Steckdose West"
    assert n["device"]["to_ha"] == {"area": "Schlafzimmer", "name": "Steckdose Ost"}


def test_multi_channel():
    d = pro4(["Schlafzimmer Klimaanlage", "Garten Ost Steckdose Eins", "Output 2", "Fluter"],
             ["Klimaanlage", "Steckdose Eins", None, None], ["a3", "a2", "a2", "a2"])
    n = model.analyze_names(d, AREAS)
    assert n["multi"]
    st = {c["key"]: c for c in n["channels"]}
    assert st["switch:0"]["status"] == "ok"
    assert st["switch:1"]["status"] == "ok"
    assert "generic" in st["switch:2"]["hints"]
    # »Fluter« ohne Bereich: HA zeigt Garten Ost Fluter, Shelly nur Fluter
    assert st["switch:3"]["status"] == "diff"
    assert st["switch:3"]["to_shelly"] == "Garten Ost Fluter"
    assert "shelly_no_area" in st["switch:3"]["hints"]


def test_rows_and_problems():
    d = plug()
    d["shelly"]["config"]["mqtt"] = {"enable": True}
    d["shelly"]["status"]["mqtt"] = {"connected": False}
    d["shelly"]["status"]["wifi"] = {"rssi": -80, "sta_ip": "192.168.1.10"}
    d["helpers"] = [{"entity_id": "light.x", "source": "switch.x", "target_domain": "light"}]
    rows = model.build_rows([d, plug()], AREAS)
    r = rows[0]
    codes = {p["code"] for p in r["problems"]}
    assert {"mqtt", "rssi", "switch_as_light", "duplicate_name"} <= codes, codes
    assert r["power"] == 12.5
    assert r["url"] == "http://192.168.1.10"
    assert r["name"] == "Schlafzimmer Steckdose West"


AREA_IDS = {v: k for k, v in AREAS.items()}


def test_ops_to_ha_cleans_area_in_name():
    rows = model.build_rows([plug(user=None)], AREAS)
    ops, skipped = model.name_ops(rows[0], "to_ha", None, AREA_IDS)
    assert len(ops) == 1 and not skipped
    assert ops[0]["name_by_user"] == "Steckdose West" and ops[0]["area_id"] == "a3"


def test_ops_to_shelly_single_channel_and_diff():
    rows = model.build_rows([plug(shelly_name="Schlafzimmer Steckdose Ost", ch_name="Fluter")], AREAS)
    ops, _ = model.name_ops(rows[0], "to_shelly", None, AREA_IDS)
    by = {o["key"]: o for o in ops}
    assert by["device"]["method"] == "Sys.SetConfig"
    assert by["device"]["params"] == {"config": {"device": {"name": "Schlafzimmer Steckdose West"}}}
    assert by["switch:0"]["params"] == {"id": 0, "config": {"name": None}}


def test_ops_multi_channel():
    d = pro4(["Schlafzimmer Klimaanlage", "Garten Ost Steckdose Eins", "Output 2", "Fluter"],
             ["Klimaanlage", "Steckdose Eins", None, None], ["a3", "a2", "a2", "a2"])
    row = model.build_rows([d], AREAS)[0]
    ops, _ = model.name_ops(row, "to_shelly", ["switch:3"], AREA_IDS)
    assert ops[0]["params"] == {"id": 3, "config": {"name": "Garten Ost Fluter"}}
    ops, _ = model.name_ops(row, "to_ha", None, AREA_IDS)
    keys = {o["key"]: o for o in ops}
    # »Fluter« hat keinen Bereich im Shelly-Namen → HA behält Garten Ost, der Name wird als eigener festgeschrieben
    assert keys["switch:3"]["name_by_user"] == "Fluter" and keys["switch:3"]["area_id"] == "a2"
    assert "device" not in keys  # name_by_user »Modul« stimmt bereits
    # HA → Shelly schreibt Shelly und pinnt in HA den reinen Namen
    ops, _ = model.name_ops(row, "to_shelly", ["switch:3"], AREA_IDS)
    assert [(o["target"], o.get("name_by_user")) for o in ops] == [("shelly", None), ("ha", "Fluter")]
    ops, _ = model.name_ops(row, "to_shelly", ["switch:3"], AREA_IDS, only=["switch:3|ha"])
    assert [o["target"] for o in ops] == ["ha"]


def test_hyphen_area():
    assert model.split_area("Hybrid Inverter Eingang", ["Hybrid-Inverter"]) == ("Hybrid-Inverter", "Eingang")



def test_problem_texts_scope():
    d = pro4(["Schlafzimmer Klimaanlage", "Garten Ost Steckdose Eins", "Output 2", "Fluter"],
             ["Klimaanlage", "Steckdose Eins", None, None], ["a3", "a2", "a2", "a2"])
    texts = [p["text"] for p in model.build_rows([d], AREAS)[0]["problems"]]
    assert "Kanalname im Shelly beginnt mit keinem HA-Bereich: switch:3 »Fluter«" in texts, texts
    assert not any(t.startswith("Gerätename im Shelly beginnt") for t in texts)



def test_i4_inputs():
    AREAS2 = {**AREAS, "g": "Gang EG"}
    ids = {v: k for k, v in AREAS2.items()}
    config = {"sys": {"device": {"name": "Schlafzimmer Wandschalter Tür Zwei"}},
              "input:0": {"name": "Taste Fünf"}, "input:1": {"name": None},
              "input:2": {"name": "Gang EG Wandschalter Süd Taste Links"}, "input:3": {"name": "Gang EG Wandschalter Süd Taste Links"}}
    inputs = {
        "input:0": {"entity_id": "event.a", "name": None, "original_name": "Taste Fünf", "area_id": None},
        "input:1": {"entity_id": "event.b", "name": None, "original_name": "Eingang 1", "area_id": None},
        "input:2": {"entity_id": "event.c", "name": "Wandschalter Süd Taste Links", "original_name": "x", "area_id": "g"},
        "input:3": {"entity_id": "event.d", "name": "Wandschalter Süd Taste Rechts", "original_name": "x", "area_id": "g"},
    }
    d = {"mac": "I4", "host": "h", "gen": 2, "sources": ["ha"],
         "ha": {"device_id": "d", "entry_id": "e", "entry_state": "loaded", "name": "x", "name_by_user": "Wandschalter Tür Zwei",
                "area_id": "a3", "channels": {}, "entities": {}, "inputs": inputs, "available": True},
         "shelly": {"info": {}, "config": config, "status": {"sys": {"time": "1"}}}}
    row = model.build_rows([d], AREAS2)[0]
    items = {c["key"]: c for c in row["names"]["channels"]}
    assert set(items) == {"input:0", "input:1", "input:2", "input:3"}
    assert items["input:1"]["unnamed"] and items["input:1"]["hints"] == ["input_unnamed"]
    assert "duplicate_channel" in items["input:3"]["hints"]
    assert items["input:3"]["status"] == "diff" and items["input:3"]["to_shelly"] == "Gang EG Wandschalter Süd Taste Rechts"
    ops, _ = model.name_ops(row, "to_shelly", ["input:3", "input:0"], ids)
    by = {(o["key"], o["target"]): o for o in ops}
    assert by[("input:3", "shelly")]["method"] == "Input.SetConfig"
    assert by[("input:0", "shelly")]["new"] == "Schlafzimmer Taste Fünf"
    assert by[("input:0", "ha")]["entity_id"] == "event.a" and by[("input:0", "ha")]["area_id"] is None



def test_detached_input_only():
    d = plug()
    d["shelly"]["config"]["input:0"] = {"name": None}
    # Gekoppelter Taster ohne Namen: wird angezeigt, aber ohne Hinweis/Problem
    row = model.build_rows([d], AREAS)[0]
    ch = [c for c in row["names"]["channels"] if c.get("kind") == "input"]
    assert len(ch) == 1 and ch[0]["coupled"] == "switch:0" and ch[0]["hints"] == [] and ch[0]["status"] == "ok"
    assert not [p for p in row["problems"] if "input" in p["code"] or p.get("key") == "input:0"]
    d["shelly"]["config"]["switch:0"]["in_mode"] = "detached"
    ch = [c for c in model.build_rows([d], AREAS)[0]["names"]["channels"] if c.get("kind") == "input"]
    assert ch and ch[0]["unnamed"]



def test_stages_block_later_names():
    d = plug(shelly_name="Schlafzimmer Steckdose Ost")  # Abweichung Shelly ↔ HA: Stufe 1
    d["ha"]["entry_title"] = "shellyplusplugs-xyz"
    row = model.build_rows([d], AREAS)[0]
    assert row["name_stage"] == 1
    title = next(p for p in row["problems"] if p["code"] == "entry_title")
    assert title["stage"] == 4 and title["blocked"]
    clean = model.build_rows([plug()], AREAS)[0]
    assert clean["name_stage"] is None



def test_channel_stage():
    d = pro4(["Schlafzimmer Klimaanlage", "Garten Ost Steckdose Eins", "Output 2", "Fluter"],
             ["Klimaanlage", "Steckdose Eins", None, None], ["a3", "a2", "a2", "a2"])
    row = model.build_rows([d], AREAS)[0]
    assert row["name_stage"] == 2  # Gerätename passt, Kanäle nicht



def test_single_module_suggestion():
    d = plug(shelly_name="Garage Schaltschrank Modul Vier", ha_name="Garage Schaltschrank Modul Vier",
             user="Tor Antrieb Aktivierung", area="a4", ch_name="Garage Torantrieb Aktivierung")
    d["model"] = "Shelly Plus 1"
    row = model.build_rows([d], AREAS)[0]
    ri = [x for x in row["rule_issues"] if x["code"] == "single_module"]
    assert ri and ri[0]["suggest"] == "Garage Tor Antrieb Aktivierung", ri


def test_stale_reservation():
    s = model.is_stale_reservation
    assert s("192.168.1.20", "10.0.31.5", None)            # anderes /16 als heute → alt
    assert not s("10.0.30.20", "10.0.31.5", None)          # gleiches /16 → passt
    assert not s("10.0.30.20", "10.0.31.5", "10.0.30.0/24")
    assert s("10.0.31.99", "10.0.31.5", "10.0.30.0/24")    # außerhalb des Bereichs für feste IPs
    assert not s("kein-ip", "10.0.31.5", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
