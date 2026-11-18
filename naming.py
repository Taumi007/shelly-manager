"""Namensregeln für Shelly-Geräte und -Kanäle (Prüfung, keine Erzwingung).

Bewusst ohne Home-Assistant-Import. Regeln (Teile in Klammern optional, aber Pflicht,
sobald ein Bereich mehrere davon hat; <Unterort> ist ein freier Platzhalter):

    Steckdosen (PM Mini, mehrere Dosen)   <Bereich> (<Unterort>) Steckdosen <Lage>
    Zwischenstecker für mehrere Geräte    <Bereich> (<Unterort>) Steckdose <Lage>
    Dummes Gerät                          <Bereich> (<Unterort>) <Gerät> (<Lage>)
    Gerät mit eigener HA-Integration      <Bereich> (<Unterort>) <Gerät> (<Lage>) Steckdose
    Wandschalter                          <Bereich> (<Unterort>) Wandschalter (<Lage>)
    Mehrkanal-Modul                       <Bereich> (<Unterort>) Modul <Kennung>
    Zuleitung / Klimasensor               <Bereich> (<Unterort>) Zuleitung | Klimasensor

<Lage> = erst Himmelsrichtung (die acht), dann Höhe; sonst Nummerierung.
"""

from __future__ import annotations

import re
from typing import Iterable

DIRS = ("Nord", "Nordost", "Ost", "Südost", "Süd", "Südwest", "West", "Nordwest")
HEIGHTS = ("Oben", "Mitte", "Unten")
SIDES = ("Links", "Rechts", "Innen", "Außen", "Aussen", "Vorne", "Hinten")
COUNTS = ("Eins", "Zwei", "Drei", "Vier", "Fünf", "Sechs", "Sieben", "Acht", "Neun", "Zehn")
_DIR_LOWER = {d.lower(): d for d in DIRS}
_DIR_LIKE = re.compile(r"^(nord|süd|sued|ost|west)[-\s]?(nord|süd|sued|ost|west)*$", re.IGNORECASE)
_BAD_CHARS = re.compile(r"[/\\_|;:,+*#<>\"]")

# Texte ohne Doppelpunkt: der Probleme-Tab gruppiert nach dem Teil vor »:«
RULES = {
    "space": "Name enthält überflüssige Leerzeichen",
    "chars": "Sonderzeichen im Namen – landen verstümmelt in der Entity-ID",
    "empty": "Name besteht nur aus dem Bereich – Funktion fehlt",
    "direction": "Ungültige Himmelsrichtung (erlaubt Nord, Nordost, Ost, Südost, Süd, Südwest, West, Nordwest)",
    "order": "Lage in falscher Reihenfolge (erst Himmelsrichtung, dann Höhe)",
    "plug_plural": "Zwischenstecker heißt »Steckdose« (Einzahl)",
    "plug_order": "Nach »Steckdose« darf nur die Lage folgen – Gerät gehört davor",
    "outlets_singular": "Unterputz-Messung mehrerer Dosen heißt »Steckdosen« (Mehrzahl)",
    "lage_missing": "Lage fehlt nach »Steckdose(n)«",
    "single_module": "Einkanalgerät nach seiner Funktion benennen, nicht »Modul«",
    "module": "Mehrkanal-Modul sollte »Modul« im Namen tragen",
    "wallswitch": "Wandschalter sollte »Wandschalter« im Namen tragen",
    "sensor": "Klimasensor sollte »Klimasensor« im Namen tragen",
    "ambiguous": "Mehrfach im Bereich vorhanden – Lage oder Nummer angeben",
}


def _tokens(text: str) -> list[str]:
    return text.split()


def is_lage(tok: str) -> bool:
    return tok in DIRS or tok in HEIGHTS or tok in SIDES or tok in COUNTS or tok.isdigit()


def strip_area(name: str, area: str | None) -> str:
    n = " ".join(name.split())
    if area:
        a = " ".join(area.replace("-", " ").split()).casefold()
        if n.replace("-", " ").casefold() == a:
            return ""
        if n.replace("-", " ").casefold().startswith(a + " "):
            return n[len(a):].strip()
    return n


def split_lage(rest: str) -> tuple[list[str], list[str]]:
    """(Kern, Lage am Ende)."""
    toks = _tokens(rest)
    i = len(toks)
    while i > 0 and is_lage(toks[i - 1]):
        i -= 1
    return toks[:i], toks[i:]


def check(name: str | None, area: str | None, kind: str) -> list[dict]:
    """Regelverstöße eines vollen Shelly-Namens. kind: plug | outlets | single | module | wallswitch | sensor | meter | channel | input."""
    if name is None or not name.strip():
        return []
    out: list[dict] = []

    def add(code: str) -> None:
        out.append({"code": code, "text": RULES[code]})

    if name != " ".join(name.split()):
        add("space")
    if _BAD_CHARS.search(name):
        add("chars")
    rest = strip_area(name, area)
    toks = _tokens(rest)
    if not toks:
        add("empty")
        return out
    for t in toks:
        if _DIR_LIKE.match(t) and t not in DIRS:
            add("direction")
            break
    core, lage = split_lage(rest)
    seen_height = False
    for t in lage:
        if t in HEIGHTS:
            seen_height = True
        elif t in DIRS and seen_height:
            add("order")
            break

    has_one, has_many = "Steckdose" in toks, "Steckdosen" in toks
    if kind == "plug":
        if has_many:
            add("plug_plural")
        if has_one:
            after = toks[toks.index("Steckdose") + 1:]
            if any(not is_lage(t) for t in after):
                add("plug_order")
            elif toks.index("Steckdose") == 0 and not after:
                add("lage_missing")  # »<Bereich> Steckdose« ohne Lage
    elif kind == "outlets":
        if has_one and not has_many:
            add("outlets_singular")
        word = "Steckdosen" if has_many else "Steckdose" if has_one else None
        after = toks[toks.index(word) + 1:] if word else []
        if word and not after:
            add("lage_missing")
        elif any(not is_lage(t) for t in after):
            add("plug_order")
    elif kind == "single" and "Modul" in toks:
        add("single_module")
    elif kind == "module" and "Modul" not in toks:
        add("module")
    elif kind == "wallswitch" and "Wandschalter" not in toks:
        add("wallswitch")
    elif kind == "sensor" and "Klimasensor" not in toks:
        add("sensor")
    elif kind in ("channel", "input") and has_one:
        after = toks[toks.index("Steckdose") + 1:]
        if any(not is_lage(t) for t in after):
            add("plug_order")
    return out


def ambiguous(names: Iterable[tuple[str, str | None, str]]) -> set[str]:
    """Namen (ids), deren Kern im selben Bereich mehrfach vorkommt, ohne dass alle eine Lage tragen.

    names: (id, Bereich, voller Name)
    """
    groups: dict[tuple[str, str], list[tuple[str, bool]]] = {}
    for nid, area, name in names:
        if not name or not name.strip():
            continue
        core, lage = split_lage(strip_area(name, area))
        if not core:
            continue
        key = ((area or "").casefold(), " ".join(core).casefold())
        groups.setdefault(key, []).append((nid, bool(lage)))
    out: set[str] = set()
    for members in groups.values():
        if len(members) > 1:
            out.update(nid for nid, has_lage in members if not has_lage)
    return out


def suggest(name: str | None, area: str | None, kind: str) -> str | None:
    """Korrekturvorschlag für die mechanisch behebbaren Verstöße, sonst None."""
    if not name:
        return None
    full = " ".join(name.split())
    area_part = full[: len(full) - len(strip_area(full, area))].strip() if area else ""
    toks = _tokens(strip_area(full, area))
    if not toks:
        return None
    word = None
    if kind == "outlets" and "Steckdose" in toks and "Steckdosen" not in toks:
        toks[toks.index("Steckdose")] = "Steckdosen"
    if kind == "plug" and "Steckdosen" in toks:
        toks[toks.index("Steckdosen")] = "Steckdose"
    for w in ("Steckdosen", "Steckdose"):
        if w in toks:
            word = w
            break
    if word and kind in ("plug", "outlets", "channel", "input"):
        i = toks.index(word)
        before, after = toks[:i], toks[i + 1:]
        moved = [t for t in after if not is_lage(t)]
        lage = [t for t in after if is_lage(t)]
        # Höhe nach Richtung
        lage = [t for t in lage if t in DIRS] + [t for t in lage if t not in DIRS]
        toks = before + moved + [word] + lage
    if not word:
        # Lage am Ende: erst Himmelsrichtung, dann Höhe
        core, lage = split_lage(" ".join(toks))
        toks = core + [t for t in lage if t in DIRS] + [t for t in lage if t not in DIRS]
    for i, t in enumerate(toks):
        if _DIR_LIKE.match(t) and t not in DIRS:
            fixed = _DIR_LOWER.get(t.replace("-", "").lower())
            if fixed:
                toks[i] = fixed
    out = " ".join(x for x in [area_part, " ".join(toks)] if x)
    return out if out != name else None
