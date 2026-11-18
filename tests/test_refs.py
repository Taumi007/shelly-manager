"""Tests für das Finden und Ersetzen von Entitäts-IDs (ohne Home Assistant)."""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("refs", os.path.join(HERE, "..", "refs.py"))
refs = importlib.util.module_from_spec(spec)
sys.modules["refs"] = refs
spec.loader.exec_module(refs)

MAP = {"switch.foo": "light.foo_neu", "sensor.foo": "sensor.bar"}
PAT = refs.pattern(list(MAP))


def ids(text):
    return [m.group(0) for m in PAT.finditer(text)]


def test_word_boundaries():
    assert ids("entity_id: switch.foo") == ["switch.foo"]
    assert ids("sensor.foo_power binary_sensor.foo xsensor.foo") == []
    assert ids("{{ states.sensor.foo.state }}") == ["sensor.foo"]
    assert ids("states('sensor.foo')|float") == ["sensor.foo"]
    assert ids("- switch.foo\n- switch.foo2") == ["switch.foo"]
    assert ids('"entity":"sensor.foo"') == ["sensor.foo"]


def test_text_replace_keeps_everything_else():
    text = "# Kommentar switch.foo\r\nentity_id: switch.foo  # bleibt\r\nother: sensor.foo_power\r\n"
    found = refs.scan_text(text, PAT, MAP, "yaml:x")
    assert [f["line"] for f in found] == [1, 2]
    new = refs.replace_text(text, [found[1]])
    assert new == "# Kommentar switch.foo\r\nentity_id: light.foo_neu  # bleibt\r\nother: sensor.foo_power\r\n"


def test_text_replace_detects_changed_file():
    text = "a: switch.foo\n"
    f = refs.scan_text(text, PAT, MAP, "s")[0]
    try:
        refs.replace_text("xx" + text, [f])
    except ValueError:
        return
    raise AssertionError("Änderung nicht erkannt")


def test_obj_values_and_keys():
    obj = {"views": [{"cards": [{"entity": "switch.foo", "name": "x"},
                                {"entities": ["sensor.foo", "sensor.foo_power"]},
                                {"content": "{{ states('switch.foo') }} / {{ states('switch.foo') }}"}]}],
           "map": {"switch.foo": {"icon": "mdi:x"}, "other": 1}}
    found = refs.scan_obj(obj, PAT, MAP, "dashboard:x")
    assert len(found) == 5
    keyf = [f for f in found if f["key"]]
    assert len(keyf) == 1 and keyf[0]["where"] == "map.switch.foo"
    # nur die zweite Fundstelle im Template ersetzen
    tmpl = [f for f in found if f["where"].endswith("content")]
    new = refs.replace_obj(obj, [f for f in found if f not in tmpl] + [tmpl[1]], PAT)
    assert new["views"][0]["cards"][0]["entity"] == "light.foo_neu"
    assert new["views"][0]["cards"][1]["entities"] == ["sensor.bar", "sensor.foo_power"]
    assert new["views"][0]["cards"][2]["content"] == "{{ states('switch.foo') }} / {{ states('light.foo_neu') }}"
    assert list(new["map"]) == ["light.foo_neu", "other"]
    assert obj["views"][0]["cards"][0]["entity"] == "switch.foo"  # Original unverändert


def test_identity_mapping_ignored():
    assert refs.pattern(["a.b"]).search("x a.b y")


def test_orphan_candidates():
    pat = refs.id_pattern({"light", "sensor", "switch"})
    text = ("entity_id: switch.pool_pumpe\nicon: /local/light.png\nservice: light.turn_on\n"
            "{{ states('sensor.temp') }} binary_sensor.x sensor.1 sensor._x states.sensor.temp2")
    assert refs.candidates(text, pat) == {"switch.pool_pumpe", "sensor.temp", "sensor.temp2"}
    ctx = ("  - trigger: switch.turned_on\n    entity_id: switch.a\n  sound: light.caf\n"
           "  entity_id: '{{light.entity_id}}'\n  \"event_type\": \"light.flash\", \"entity\": \"light.b\"")
    assert refs.candidates(ctx, pat) == {"switch.a", "light.b"}


def test_suggestions():
    existing = {"light.pool_pumpe", "sensor.wohnzimmer_temperatur", "switch.neu"}
    assert refs.suggest("switch.alt", existing, {"switch.alt": "switch.neu"})[0] == "switch.neu"
    assert refs.suggest("switch.pool_pumpe", existing, {})[0] == "light.pool_pumpe"
    assert refs.suggest("sensor.ganz_anders", existing, {}) == (None, None)
    ex2 = {"sensor.lampe_leistung", "sensor.lampe_energie", "sensor.steckdose_sudwest_energie", "sensor.steckdose_sudwest_leistung"}
    assert refs.suggest("sensor.lampe_power", ex2, {}) == ("sensor.lampe_leistung", "Messgröße umbenannt (power → leistung)")
    # nie Leistung auf Energie abbilden
    assert refs.suggest("sensor.steckdose_west_power", ex2, {})[0] in (None, "sensor.steckdose_sudwest_leistung")
    assert refs.suggest("sensor.lampe_xyz_power", {"sensor.lampe_xyz_energie"}, {}) == (None, None)
    rooms = {"sensor.bad_og_heizung_heizleistung", "sensor.gang_eg_heizung_heizleistung", "binary_sensor.tur_sensor_contact"}
    assert refs.suggest("sensor.bad_eg_heizung_heizleistung", rooms, {}) == (None, None)
    assert refs.suggest("sensor.gang_heizung_heizleistung", rooms, {})[0] == "sensor.gang_eg_heizung_heizleistung"
    assert refs.suggest("binary_sensor.tursensor_contact", rooms, {})[0] == "binary_sensor.tur_sensor_contact"
    agg = {"sensor.computerraum_leistung", "sensor.usv_ausgang_server_steckdose_leistung"}
    assert refs.suggest("sensor.computerraum_steckdose_pc_power", agg, {}) == (None, None)
    assert refs.suggest("sensor.gang_keller_usv_ausgang_server_steckdose_power", agg, {})[0] == "sensor.usv_ausgang_server_steckdose_leistung"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
