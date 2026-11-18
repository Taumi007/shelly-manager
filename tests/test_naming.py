"""Tests für naming.py – python3 tests/test_naming.py"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("sm_naming", Path(__file__).parents[1] / "naming.py")
nm = importlib.util.module_from_spec(_spec)
sys.modules["sm_naming"] = nm
_spec.loader.exec_module(nm)


def codes(name, area, kind):
    return [x["code"] for x in nm.check(name, area, kind)]


def test_plugs():
    assert codes("Computerraum Monitor Steckdose", "Computerraum", "plug") == []
    assert codes("Computerraum Steckdose West", "Computerraum", "plug") == []
    assert codes("Computerraum Steckdose Monitor", "Computerraum", "plug") == ["plug_order"]
    assert codes("Computerraum Steckdose", "Computerraum", "plug") == ["lage_missing"]
    assert codes("Computerraum Steckdosen West", "Computerraum", "plug") == ["plug_plural"]
    assert codes("Schlafzimmer Lavalampe", "Schlafzimmer", "plug") == []
    assert codes("Schlafzimmer Nanoleaf Ost Steckdose", "Schlafzimmer", "plug") == []


def test_outlets():
    assert codes("Bad EG Steckdosen Süd", "Bad EG", "outlets") == []
    assert codes("Gang Keller Treppe Steckdosen Oben", "Gang Keller", "outlets") == []
    assert codes("Dachboden Steckdosen Ost Süd West", "Dachboden", "outlets") == []
    assert codes("Küche Steckdose Nord", "Küche", "outlets") == ["outlets_singular"]
    assert codes("Küche Steckdose Anrichte Südost", "Küche", "outlets") == ["outlets_singular", "plug_order"]
    assert codes("Gang OG Verteiler Zuleitung", "Gang OG", "outlets") == []


def test_general():
    assert codes(" Schlafzimmer Wandschalter Tür Eins", "Schlafzimmer", "wallswitch") == ["space"]
    assert codes("Bad/Klo EG Zuleitung", "Gang EG", "outlets") == ["chars"]
    assert codes("Klo OG", "Klo OG", "outlets") == ["empty"]
    assert codes("Wohnzimmer Stehleuchte Nordwestnord", "Wohnzimmer", "single") == ["direction"]
    assert codes("Wohnzimmer Stehleuchte Oben Nord", "Wohnzimmer", "single") == ["order"]
    assert codes("Wohnzimmer Stehleuchte Nord Oben", "Wohnzimmer", "single") == []
    assert codes("Garage Schaltschrank Modul Vier", "Garage", "single") == ["single_module"]
    assert codes("Garten Ost Modul", "Garten Ost", "module") == []
    assert codes("Bad OG Wandschalter", "Bad OG", "wallswitch") == []


def test_ambiguous():
    names = [("a", "Küche", "Küche Dunstabzug"), ("b", "Küche", "Küche Dunstabzug Nord"),
             ("c", "Garage", "Garage Deckenleuchte Nord"), ("d", "Garage", "Garage Deckenleuchte Süd"),
             ("e", "Gartenhaus", "Gartenhaus Microinverter Eins Steckdose")]
    assert nm.ambiguous(names) == {"a"}



def test_suggest():
    assert nm.suggest("Computerraum Steckdose Monitor", "Computerraum", "plug") == "Computerraum Monitor Steckdose"
    assert nm.suggest("Computerraum Steckdose Regal Ost", "Computerraum", "plug") == "Computerraum Regal Steckdose Ost"
    assert nm.suggest("Küche Steckdose Anrichte Südost", "Küche", "outlets") == "Küche Anrichte Steckdosen Südost"
    assert nm.suggest("Küche Steckdose Nord", "Küche", "outlets") == "Küche Steckdosen Nord"
    assert nm.suggest(" Schlafzimmer Wandschalter Tür Eins", "Schlafzimmer", "wallswitch") == "Schlafzimmer Wandschalter Tür Eins"
    assert nm.suggest("Wohnzimmer Lampe Oben Nord", "Wohnzimmer", "single") == "Wohnzimmer Lampe Nord Oben"
    assert nm.suggest("Bad EG Steckdosen Süd", "Bad EG", "outlets") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
