"""Verweise auf Entitäts-IDs finden und umschreiben – ohne KI, per eindeutiger Wortgrenzen-Suche.

Quellen:
  yaml       YAML-Dateien im Konfigurationsordner (Text ersetzen, Formatierung/Kommentare bleiben)
  dashboard  Dashboards im UI-Modus (über die Lovelace-Speicherfunktionen von HA)
  energy     Energie-Dashboard (über den Energie-Manager von HA)
  entry      Optionen/Daten von Integrationseinträgen (z. B. Helfer: Gruppe, Template, Verbrauchszähler)
  storage    sonstige .storage-Dateien fremder Integrationen – nur anzeigen

Vor dem Schreiben wird jede betroffene Quelle gesichert; Rückgängig stellt genau diesen Stand wieder her.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

BACKUP_DIR = "shelly_manager_backups"
BACKUP_KEEP = 10
SKIP_DIRS = {".git", ".storage", "custom_components", "www", "deps", "tts", "node_modules", "backups", "__pycache__",
             ".cloud", "media", "image", BACKUP_DIR, "blueprints", ".venv", "venv"}
SKIP_FILES = {"secrets.yaml"}
# Nur anzeigen: Archive, generierte Dateien fremder Integrationen, ESPHome (müsste neu geflasht werden)
READONLY_HINTS = (("archiv", "Archiv – nur angezeigt"), ("archive", "Archiv – nur angezeigt"),
                  ("stromnetz/stromnetz_generated", "wird aus stromnetz.yaml neu erzeugt"),
                  ("esphome", "ESPHome – nach Änderung neu flashen"))
# Stromnetz (Ordner): Quelle stromnetz.yaml darf geändert werden, danach erzeugt generate.py die Ausgaben neu
STROMNETZ_SRC = os.path.join("stromnetz", "stromnetz.yaml")
STROMNETZ_GEN = os.path.join("stromnetz", "generate.py")
STROMNETZ_OUT = (os.path.join("stromnetz", "stromnetz_generated.yaml"), os.path.join("stromnetz", "STROMNETZ.md"))
UI_YAML = {"automations.yaml": "automation", "scripts.yaml": "script", "scenes.yaml": "scene"}
# .storage-Dateien, die HA selbst nachzieht oder die keine Konfiguration sind
STORAGE_IGNORE = ("core.entity_registry", "core.device_registry", "core.restore_state", "core.config_entries",
                  "homeassistant.exposed_entities", "core.area_registry", "trace.", "recorder", "lovelace", "energy",
                  "shelly_manager", "auth", "onboarding", "http", "core.uuid", "core.analytics", "backup",
                  "repairs", "core.logger", "bluetooth.", "hassio", "person", "stromnetz")
SKIP_ENTRY_DOMAINS = {"shelly", "shelly_manager"}
MAX_FILE = 5_000_000
CONTEXT = 70


def pattern(ids: list[str]) -> re.Pattern:
    """Entitäts-IDs als eigenständige Wörter: davor/dahinter kein Buchstabe, keine Ziffer, kein Unterstrich.

    So trifft »sensor.foo« weder »sensor.foo_power« noch »binary_sensor.foo«; »states.sensor.foo« schon.
    """
    alts = "|".join(re.escape(i) for i in sorted(set(ids), key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{alts})(?![A-Za-z0-9_])")


def plain(obj: Any) -> Any:
    """Mapping-Proxies, Tupel usw. in reine JSON-Strukturen wandeln."""
    return json.loads(json.dumps(obj, default=lambda o: dict(o) if hasattr(o, "keys") else list(o) if hasattr(o, "__iter__") else str(o)))


def _fid(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _snip(text: str, start: int, end: int) -> tuple[str, str, str]:
    a = max(0, start - CONTEXT)
    b = min(len(text), end + CONTEXT)
    return text[a:start].split("\n")[-1].lstrip("\r"), text[start:end], text[end:b].split("\n")[0].rstrip("\r")


# ── Text (YAML) ──────────────────────────────────────────────────────────────
def scan_text(text: str, pat: re.Pattern, mapping: dict[str, str], source: str) -> list[dict]:
    out = []
    for n, m in enumerate(pat.finditer(text)):
        old = m.group(0)
        pre, _, post = _snip(text, m.start(), m.end())
        out.append({"fid": _fid(source, m.start(), old, n), "old": old, "new": mapping[old],
                    "line": text.count("\n", 0, m.start()) + 1, "pos": m.start(),
                    "before": pre, "after": post})
    return out


def replace_text(text: str, findings: list[dict]) -> str:
    for f in sorted(findings, key=lambda f: f["pos"], reverse=True):
        if text[f["pos"]:f["pos"] + len(f["old"])] != f["old"]:
            raise ValueError(f"Datei hat sich geändert (Zeile {f['line']})")
        text = text[:f["pos"]] + f["new"] + text[f["pos"] + len(f["old"]):]
    return text


# ── Strukturen (Dashboards, Energie, Einträge) ───────────────────────────────
def _path_str(path: tuple) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out


def scan_obj(obj: Any, pat: re.Pattern, mapping: dict[str, str], source: str, path: tuple = ()) -> list[dict]:
    out: list[dict] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                for n, m in enumerate(pat.finditer(k)):
                    out.append({"fid": _fid(source, path, "key", k, n), "old": m.group(0), "new": mapping[m.group(0)],
                                "path": list(path), "key": True, "keyname": k, "idx": n, "where": _path_str(path + (k,)),
                                "before": k[:m.start()][-CONTEXT:], "after": k[m.end():][:CONTEXT]})
            out.extend(scan_obj(v, pat, mapping, source, path + (k,)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(scan_obj(v, pat, mapping, source, path + (i,)))
    elif isinstance(obj, str):
        for n, m in enumerate(pat.finditer(obj)):
            out.append({"fid": _fid(source, path, n, m.group(0)), "old": m.group(0), "new": mapping[m.group(0)],
                        "path": list(path), "key": False, "idx": n, "where": _path_str(path),
                        "before": obj[:m.start()][-CONTEXT:], "after": obj[m.end():][:CONTEXT]})
    return out


def _replace_in_string(s: str, pat: re.Pattern, picks: dict[int, str]) -> str:
    parts, last = [], 0
    for n, m in enumerate(pat.finditer(s)):
        if n in picks:
            parts.append(s[last:m.start()])
            parts.append(picks[n])
            last = m.end()
    parts.append(s[last:])
    return "".join(parts)


def replace_obj(obj: Any, findings: list[dict], pat: re.Pattern) -> Any:
    """Kopie von obj mit ersetzten Fundstellen (Strings und Schlüssel)."""
    obj = json.loads(json.dumps(obj))
    vals: dict[tuple, dict[int, str]] = {}
    for f in findings:
        if not f["key"]:
            vals.setdefault(tuple(f["path"]), {})[f["idx"]] = f["new"]
    for path, picks in vals.items():
        parent = obj
        for p in path[:-1]:
            parent = parent[p]
        last = path[-1] if path else None
        if last is None:
            obj = _replace_in_string(obj, pat, picks)
        else:
            parent[last] = _replace_in_string(parent[last], pat, picks)
    # Schlüssel zuletzt, tiefste zuerst (Pfade der Werte oben bleiben gültig)
    kpicks: dict[tuple, dict[str, dict[int, str]]] = {}
    for f in findings:
        if f["key"]:
            kpicks.setdefault(tuple(f["path"]), {}).setdefault(f["keyname"], {})[f["idx"]] = f["new"]
    for path, per_key in sorted(kpicks.items(), key=lambda kv: len(kv[0]), reverse=True):
        parent = obj
        for p in path:
            parent = parent[p]
        for k, picks in per_key.items():
            if k in parent:
                _rename_key(parent, k, _replace_in_string(k, pat, picks))
    return obj


def _rename_key(d: dict, old: str, new: str) -> dict:
    items = list(d.items())
    d.clear()
    for k, v in items:
        d[new if k == old else k] = v
    return d


# ── Verwaiste Verweise ───────────────────────────────────────────────────────
FILE_EXT = {"png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "yaml", "yml", "json", "js", "css", "html", "py", "txt",
            "mp3", "mp4", "wav", "pdf", "md", "log", "db", "jinja", "csv", "xml", "zip", "tar", "gz", "local"}


def id_pattern(domains: set[str]) -> re.Pattern:
    """Alles, was wie eine Entitäts-ID einer bekannten Domain aussieht."""
    alts = "|".join(re.escape(d) for d in sorted(domains, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{alts})\.[a-z0-9_]+(?![A-Za-z0-9_])")


# Davor stehende Schlüssel, bei denen »domain.wort« keine Entität ist (Auslöser-/Bedingungstypen, Ereignisse, Töne …)
NOT_ENTITY_KEY = re.compile(r"""(?:^|[\s{,"'])(trigger|condition|sound|event_type|event|platform|type|domain|service|action)["']?\s*:\s*["']?$""")
TEMPLATE_VAR = re.compile(r"\{\{\s*$")


def candidates(text: str, pat: re.Pattern) -> set[str]:
    out = set()
    for m in pat.finditer(text):
        tok = m.group(0)
        obj = tok.split(".", 1)[1]
        if obj in FILE_EXT or obj.isdigit() or obj.startswith("_") or obj.endswith("_"):
            continue
        prefix = text[max(0, m.start() - 60):m.start()].rsplit("\n", 1)[-1]
        if NOT_ENTITY_KEY.search(prefix) or TEMPLATE_VAR.search(prefix):
            continue
        out.add(tok)
    return out


# Messgrößen-Endungen: englische (ältere) und deutsche Entitätsnamen von HA
SYNONYMS = {"power": "leistung", "energy": "energie", "voltage": "spannung", "current": "strom",
            "temperature": "temperatur", "humidity": "luftfeuchtigkeit", "battery": "batterie",
            "signal_strength": "signalstarke", "illuminance": "beleuchtungsstarke", "frequency": "frequenz",
            "power_factor": "leistungsfaktor", "pressure": "druck", "uptime": "betriebszeit"}
_ALL_SYN = {**SYNONYMS, **{v: k for k, v in SYNONYMS.items()}}


def _tail(obj: str) -> tuple[str, str]:
    """(Rest, Messgröße) – längste bekannte Endung zuerst, sonst das letzte Wort."""
    for suf in sorted(_ALL_SYN, key=len, reverse=True):
        if obj.endswith("_" + suf):
            return obj[: -len(suf) - 1], suf
    head, _, last = obj.rpartition("_")
    return head, last


def _norm(word: str) -> str:
    return SYNONYMS.get(word, word)


def _subseq(short: list[str], long: list[str]) -> bool:
    """short ist echte Teilfolge von long (Reihenfolge bleibt, Wörter nur eingefügt)."""
    if not short or len(short) >= len(long):
        return False
    it = iter(long)
    return all(w in it for w in short)


def suggest(orphan: str, existing: set[str], renamed: dict[str, str]) -> tuple[str | None, str | None]:
    """Vorschlag für eine verschwundene ID: (neue ID, Begründung). Lieber keiner als ein falscher."""
    import difflib

    if orphan in renamed and renamed[orphan] in existing:
        return renamed[orphan], "gleiche Entität (eindeutige ID), neue Entitäts-ID"
    domain, obj = orphan.split(".", 1)
    head, suf = _tail(obj)
    if suf in _ALL_SYN and f"{domain}.{head}_{_ALL_SYN[suf]}" in existing:
        return f"{domain}.{head}_{_ALL_SYN[suf]}", f"Messgröße umbenannt ({suf} → {_ALL_SYN[suf]})"
    same_obj = sorted(e for e in existing if e.split(".", 1)[1] == obj)
    if len(same_obj) == 1:
        return same_obj[0], "gleicher Name, andere Domain"
    # Ähnliche ID nur bei gleicher Messgröße und nur, wenn Wörter dazugekommen/weggefallen sind oder bloß
    # Unterstriche fehlen – nie, wenn ein Wort ersetzt wurde (Bad EG ≠ Bad OG, Leistung ≠ Energie)
    want = _norm(suf)
    words = [w for w in head.split("_") if w]
    best: list[tuple[int, str]] = []
    for e in existing:
        d, o = e.split(".", 1)
        if d != domain:
            continue
        h, sf = _tail(o)
        if _norm(sf) != want:
            continue
        other = [w for w in h.split("_") if w]
        if "".join(other) == "".join(words):
            best.append((0, e))
        elif _subseq(words, other):
            best.append((len(other) - len(words), e))  # Wörter eingefügt (z. B. Bereich »eg«)
        elif len(other) < len(words) and words[len(words) - len(other):] == other:
            best.append((len(words) - len(other), e))  # nur ein Präfix weggefallen (alter Bereich)
    best.sort()
    if best and (len(best) == 1 or best[0][0] < best[1][0]):
        return best[0][1], "ähnliche ID, gleiche Messgröße – bitte prüfen"
    return None, None


# ── Quellen ──────────────────────────────────────────────────────────────────
class Refs:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.root = hass.config.path()

    # Lovelace-Dashboards im UI-Modus
    def _dashboards(self) -> dict[str, Any]:
        data = self.hass.data.get("lovelace")
        dashes = getattr(data, "dashboards", None)
        if dashes is None and isinstance(data, dict):
            dashes = data.get("dashboards")
        out = {}
        for url, dash in (dashes or {}).items():
            if getattr(dash, "mode", None) == "storage":
                out[url or "lovelace"] = dash
        return out

    def _stromnetz(self):
        entries = self.hass.config_entries.async_loaded_entries("stromnetz") if hasattr(
            self.hass.config_entries, "async_loaded_entries") else [
            e for e in self.hass.config_entries.async_entries("stromnetz") if getattr(e, "runtime_data", None)]
        entry = entries[0] if entries else None
        return entry if entry is not None and hasattr(getattr(entry, "runtime_data", None), "data") else None

    async def _stromnetz_save(self, data: dict) -> None:
        """Wie »Speichern« im Stromnetz-Panel: prüfen, speichern, Sensoren neu aufbauen."""
        import importlib

        entry = self._stromnetz()
        if entry is None:
            raise ValueError("Stromnetz ist nicht geladen")
        ws = importlib.import_module("custom_components.stromnetz.websocket")
        desc = ws._describe(self.hass, entry, ws.normalize(data))
        if desc.get("errors"):
            raise ValueError(f"Stromnetz lehnt die Änderung ab: {desc['errors']}")
        entry.runtime_data.data = desc["data"]
        await entry.runtime_data.store.async_save(desc["data"])
        self.hass.config_entries.async_schedule_reload(entry.entry_id)

    def _stromnetz_generate(self) -> str:
        """stromnetz/generate.py ausführen (erzeugt stromnetz_generated.yaml und STROMNETZ.md neu)."""
        import contextlib
        import io
        import runpy
        import sys

        path = os.path.join(self.root, STROMNETZ_GEN)
        if not os.path.exists(path):
            return "kein generate.py"
        out = io.StringIO()
        argv = sys.argv
        try:
            sys.argv = [path]
            with contextlib.redirect_stdout(out):
                runpy.run_path(path, run_name="__main__")
        except SystemExit as err:
            if err.code not in (None, 0):
                raise ValueError(f"generate.py: {err.code}") from err
        finally:
            sys.argv = argv
        return out.getvalue().strip().splitlines()[0] if out.getvalue().strip() else "neu erzeugt"

    async def _energy(self):
        try:
            from homeassistant.components.energy.data import async_get_manager
        except ImportError:
            return None
        try:
            return await async_get_manager(self.hass)
        except Exception:  # noqa: BLE001
            return None

    def _yaml_files(self) -> list[tuple[str, str | None]]:
        out = []
        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for f in files:
                if not f.endswith((".yaml", ".yml", ".jinja")) or f in SKIP_FILES:
                    continue
                p = os.path.join(root, f)
                rel = os.path.relpath(p, self.root)
                note = next((n for hint, n in READONLY_HINTS if hint in rel.lower()), None)
                out.append((rel, note))
        return sorted(out)

    # ── Suchen ────────────────────────────────────────────────────────────────
    async def _texts(self) -> list[str]:
        """Inhalt aller durchsuchten Quellen als Text (nur zum Sammeln von Kandidaten)."""
        def files() -> list[str]:
            out = []
            for rel, _ in self._yaml_files():
                try:
                    with open(os.path.join(self.root, rel), encoding="utf-8") as fh:
                        out.append(fh.read(MAX_FILE))
                except (OSError, UnicodeDecodeError):
                    continue
            return out

        texts = await self.hass.async_add_executor_job(files)
        for dash in self._dashboards().values():
            try:
                texts.append(json.dumps(plain(await dash.async_load(False))))
            except Exception:  # noqa: BLE001
                continue
        mgr = await self._energy()
        if mgr is not None and mgr.data:
            texts.append(json.dumps(plain(mgr.data)))
        for entry in self.hass.config_entries.async_entries():
            if entry.domain not in SKIP_ENTRY_DOMAINS:
                texts.append(json.dumps(plain(entry.options)))
        return texts

    async def orphans(self) -> dict:
        """Verweise auf Entitäts-IDs, die es nicht (mehr) gibt – mit Vorschlag für die neue ID."""
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(self.hass)
        existing = set(self.hass.states.async_entity_ids()) | set(reg.entities)
        domains = {e.split(".", 1)[0] for e in existing}
        # Gelöschte Registry-Einträge: alte ID → heutige ID derselben Entität (gleiche Plattform + unique_id)
        by_uid = {(e.platform, e.unique_id): e.entity_id for e in reg.entities.values()}
        renamed: dict[str, str] = {}
        for d in getattr(reg, "deleted_entities", {}).values():
            new = by_uid.get((d.platform, d.unique_id))
            if new and new != d.entity_id:
                renamed[d.entity_id] = new
            domains.add(d.entity_id.split(".", 1)[0])
        pat = id_pattern(domains)
        found: set[str] = set()
        for text in await self._texts():
            found |= candidates(text, pat)
        orphan = sorted(t for t in found if t not in existing
                        and not self.hass.services.has_service(*t.split(".", 1)))
        if not orphan:
            return {"orphans": [], "total": 0}
        res = await self.scan({o: o for o in orphan}, same_ok=True)
        info: dict[str, dict] = {}
        for src in res["sources"]:
            for f in src["findings"]:
                o = info.setdefault(f["old"], {"id": f["old"], "count": 0, "sources": [], "writable": 0})
                o["count"] += 1
                o["writable"] += 1 if src["writable"] else 0
                if src["label"] not in o["sources"]:
                    o["sources"].append(src["label"])
        shelly = {e.entity_id for e in reg.entities.values() if e.platform == "shelly"}
        for o in info.values():
            o["suggestion"], o["reason"] = suggest(o["id"], existing, renamed)
            o["sure"] = bool(o["reason"]) and "bitte prüfen" not in o["reason"]
            o["shelly"] = bool(o["suggestion"] in shelly) or o["id"] in renamed
        out = sorted(info.values(), key=lambda o: (not o["shelly"], o["suggestion"] is None, -o["count"], o["id"]))
        return {"orphans": out, "total": sum(o["count"] for o in out)}

    async def count(self, ids: list[str]) -> dict[str, dict]:
        """Verwendungen je Entitäts-ID (für die Übersicht vor dem Umbenennen)."""
        res = await self.scan({i: i for i in ids}, same_ok=True)
        out: dict[str, dict] = {i: {"count": 0, "sources": []} for i in ids}
        for src in res["sources"]:
            for f in src["findings"]:
                o = out[f["old"]]
                o["count"] += 1
                if src["label"] not in o["sources"]:
                    o["sources"].append(src["label"])
        return out

    async def scan(self, mapping: dict[str, str], same_ok: bool = False) -> dict:
        mapping = {k: v for k, v in mapping.items() if k and v and (same_ok or k != v)}
        if not mapping:
            return {"sources": [], "total": 0}
        pat = pattern(list(mapping))
        sources: list[dict] = []

        def files() -> list[dict]:
            res = []
            for rel, note in self._yaml_files():
                p = os.path.join(self.root, rel)
                try:
                    if os.path.getsize(p) > MAX_FILE:
                        continue
                    with open(p, encoding="utf-8", newline="") as fh:
                        text = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                if not pat.search(text):
                    continue
                key = f"yaml:{rel}"
                reload = "stromnetz_generate" if rel == STROMNETZ_SRC else UI_YAML.get(rel, "all")
                if rel == STROMNETZ_SRC:
                    note = "Quelle von Stromnetz – danach wird neu erzeugt"
                res.append({"key": key, "kind": "yaml", "label": rel, "writable": rel == STROMNETZ_SRC or note is None,
                            "note": note, "reload": reload, "findings": scan_text(text, pat, mapping, key)})
            sdir = os.path.join(self.root, ".storage")
            for f in sorted(os.listdir(sdir)) if os.path.isdir(sdir) else []:
                if f.endswith(".bak") or any(f.startswith(x) for x in STORAGE_IGNORE):
                    continue
                p = os.path.join(sdir, f)
                try:
                    if os.path.getsize(p) > MAX_FILE:
                        continue
                    with open(p, encoding="utf-8") as fh:
                        text = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                if not pat.search(text):
                    continue
                key = f"storage:{f}"
                hits = scan_text(text, pat, mapping, key)
                for h in hits:  # Kontext aus .storage kann Zugangsdaten enthalten – nur die ID zeigen
                    h["before"] = h["after"] = ""
                res.append({"key": key, "kind": "storage", "label": f".storage/{f}", "writable": False,
                            "note": "Speicher einer anderen Integration – dort ändern", "findings": hits})
            return res

        sources.extend(await self.hass.async_add_executor_job(files))

        for url, dash in self._dashboards().items():
            try:
                cfg = await dash.async_load(False)
            except Exception:  # noqa: BLE001 – leeres/kaputtes Dashboard überspringen
                continue
            key = f"dashboard:{url}"
            found = scan_obj(plain(cfg), pat, mapping, key)
            if found:
                title = (getattr(dash, "config", None) or {}).get("title") if isinstance(getattr(dash, "config", None), dict) else None
                sources.append({"key": key, "kind": "dashboard", "label": f"Dashboard {title or url}", "url": url,
                                "writable": True, "note": None, "findings": found})

        sn = self._stromnetz()
        if sn is not None:
            found = scan_obj(plain(sn.runtime_data.data), pat, mapping, "stromnetz")
            if found:
                sources.append({"key": "stromnetz", "kind": "stromnetz", "label": "Stromnetz (Integration)", "writable": True,
                                "note": "Stromnetz prüft und berechnet seine Sensoren danach neu", "findings": found})

        mgr = await self._energy()
        if mgr is not None and mgr.data:
            found = scan_obj(plain(mgr.data), pat, mapping, "energy")
            if found:
                sources.append({"key": "energy", "kind": "energy", "label": "Energie-Dashboard", "writable": True,
                                "note": None, "findings": found})

        for entry in self.hass.config_entries.async_entries():
            if entry.domain in SKIP_ENTRY_DOMAINS:
                continue
            key = f"entry:{entry.entry_id}"
            found = [dict(f, part="options") for f in scan_obj(plain(entry.options), pat, mapping, key + ":o")]
            found += [dict(f, part="data") for f in scan_obj(plain(entry.data), pat, mapping, key + ":d")]
            if found:
                for f in found:
                    if f["part"] == "data":  # Daten können Zugangsdaten enthalten
                        f["before"] = f["after"] = ""
                sources.append({"key": key, "kind": "entry", "label": f"{entry.title} ({entry.domain})",
                                "domain": entry.domain, "entry_id": entry.entry_id, "writable": True, "note": None,
                                "findings": found})

        total = sum(len(s["findings"]) for s in sources)
        return {"sources": sources, "total": total}

    # ── Schreiben ─────────────────────────────────────────────────────────────
    async def apply(self, mapping: dict[str, str], fids: list[str], progress=None) -> dict:
        mapping = {k: v for k, v in mapping.items() if k and v and k != v}
        pat = pattern(list(mapping))
        plan = await self.scan(mapping)
        want = set(fids)
        backup_id = time.strftime("%Y%m%d-%H%M%S")
        bdir = os.path.join(self.root, BACKUP_DIR, backup_id)
        manifest: dict[str, Any] = {"ts": time.time(), "mapping": mapping, "items": []}
        results = []
        reload: set[str] = set()
        for src in plan["sources"]:
            picks = [f for f in src["findings"] if f["fid"] in want]
            if not picks or not src["writable"]:
                continue
            if progress:
                progress(src["key"], "running")
            try:
                item = await self._apply_source(src, picks, pat, bdir)
                manifest["items"].append(item)
                if src["kind"] == "yaml":
                    reload.add(src["reload"])
                results.append({"key": src["key"], "label": src["label"], "ok": True, "count": len(picks)})
                if progress:
                    progress(src["key"], "done", f"{len(picks)} ersetzt")
            except Exception as err:  # noqa: BLE001 – Ergebnis pro Quelle
                _LOGGER.warning("Verweise in %s: %s", src["label"], err)
                results.append({"key": src["key"], "label": src["label"], "ok": False, "error": str(err)})
                if progress:
                    progress(src["key"], "error", str(err))
        if "stromnetz_generate" in reload:
            # Auch die erzeugten Dateien sichern – Rückgängig stellt sie exakt wieder her
            def keep() -> list[str]:
                out = []
                for rel in STROMNETZ_OUT:
                    src = os.path.join(self.root, rel)
                    if os.path.exists(src):
                        dst = os.path.join(bdir, "files", rel)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        out.append(rel)
                return out

            manifest["items"].append({"kind": "files", "rels": await self.hass.async_add_executor_job(keep)})
        if manifest["items"]:
            await self.hass.async_add_executor_job(self._write_manifest, bdir, manifest)
        await self._reload(reload)
        left = await self.scan(mapping)
        return {"results": results, "backup_id": backup_id if manifest["items"] else None,
                "remaining": left["total"], "reloaded": sorted(reload)}

    async def _apply_source(self, src: dict, picks: list[dict], pat: re.Pattern, bdir: str) -> dict:
        kind = src["kind"]
        if kind == "yaml":
            rel = src["label"]
            path = os.path.join(self.root, rel)

            def write() -> None:
                with open(path, encoding="utf-8", newline="") as fh:
                    text = fh.read()
                new = replace_text(text, picks)
                dst = os.path.join(bdir, "files", rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(path, dst)
                tmp = path + ".shm-tmp"
                with open(tmp, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
                shutil.copymode(path, tmp)
                os.replace(tmp, path)

            await self.hass.async_add_executor_job(write)
            return {"kind": "yaml", "rel": rel, "reload": src["reload"]}
        if kind == "dashboard":
            dash = self._dashboards()[src["url"]]
            cfg = plain(await dash.async_load(False))
            await self._save_json(bdir, f"dashboard-{src['url']}.json", cfg)
            await dash.async_save(replace_obj(cfg, picks, pat))
            return {"kind": "dashboard", "url": src["url"], "file": f"dashboard-{src['url']}.json"}
        if kind == "stromnetz":
            old = plain(self._stromnetz().runtime_data.data)
            await self._save_json(bdir, "stromnetz.json", old)
            await self._stromnetz_save(replace_obj(old, picks, pat))
            return {"kind": "stromnetz", "file": "stromnetz.json"}
        if kind == "energy":
            mgr = await self._energy()
            old = plain(mgr.data)
            new = replace_obj(old, picks, pat)
            await self._save_json(bdir, "energy.json", old)
            await mgr.async_update({k: v for k, v in new.items() if v != old.get(k)})
            return {"kind": "energy", "file": "energy.json"}
        if kind == "entry":
            entry = self.hass.config_entries.async_get_entry(src["entry_id"])
            if entry is None:
                raise ValueError("Eintrag nicht mehr vorhanden")
            options, data = plain(entry.options), plain(entry.data)
            await self._save_json(bdir, f"entry-{entry.entry_id}.json", {"options": options, "data": data})
            new_o = replace_obj(options, [f for f in picks if f["part"] == "options"], pat)
            new_d = replace_obj(data, [f for f in picks if f["part"] == "data"], pat)
            self.hass.config_entries.async_update_entry(entry, options=new_o, data=new_d)
            await self._reload_entry(entry.entry_id)
            return {"kind": "entry", "entry_id": entry.entry_id, "file": f"entry-{entry.entry_id}.json"}
        raise ValueError(f"Quelle {kind} kann nicht geändert werden")

    async def _save_json(self, bdir: str, name: str, obj: Any) -> None:
        def write() -> None:
            os.makedirs(bdir, exist_ok=True)
            with open(os.path.join(bdir, name), "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=1)

        await self.hass.async_add_executor_job(write)

    def _write_manifest(self, bdir: str, manifest: dict) -> None:
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
        base = os.path.join(self.root, BACKUP_DIR)
        for old in sorted(os.listdir(base))[:-BACKUP_KEEP]:
            shutil.rmtree(os.path.join(base, old), ignore_errors=True)

    async def _reload_entry(self, entry_id: str) -> None:
        from homeassistant.config_entries import ConfigEntryState

        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is not None and entry.state is ConfigEntryState.LOADED:
            await self.hass.config_entries.async_reload(entry_id)

    async def _reload(self, what: set[str]) -> None:
        if "stromnetz_generate" in what:
            what = set(what) - {"stromnetz_generate"}
            try:
                msg = await self.hass.async_add_executor_job(self._stromnetz_generate)
                _LOGGER.info("Stromnetz neu erzeugt: %s", msg)
            except Exception as err:  # noqa: BLE001 – Quelle ist geändert, nur die Ausgaben fehlen
                _LOGGER.warning("Stromnetz konnte nicht neu erzeugt werden: %s", err)
        if not what:
            return
        if "all" in what:
            await self.hass.services.async_call("homeassistant", "reload_all", blocking=True)
            return
        for domain in sorted(what):
            if self.hass.services.has_service(domain, "reload"):
                await self.hass.services.async_call(domain, "reload", blocking=True)

    # ── Rückgängig ────────────────────────────────────────────────────────────
    def backups(self) -> list[dict]:
        base = os.path.join(self.root, BACKUP_DIR)
        out = []
        for bid in sorted(os.listdir(base), reverse=True) if os.path.isdir(base) else []:
            try:
                with open(os.path.join(base, bid, "manifest.json"), encoding="utf-8") as fh:
                    m = json.load(fh)
                out.append({"id": bid, "ts": m.get("ts"), "mapping": m.get("mapping"), "items": len(m.get("items", []))})
            except (OSError, ValueError):
                continue
        return out

    async def undo(self, backup_id: str) -> dict:
        if not re.fullmatch(r"\d{8}-\d{6}", backup_id):
            raise ValueError("Ungültige Sicherung")
        bdir = os.path.join(self.root, BACKUP_DIR, backup_id)

        def load(name: str) -> Any:
            with open(os.path.join(bdir, name), encoding="utf-8") as fh:
                return json.load(fh)

        manifest = await self.hass.async_add_executor_job(load, "manifest.json")
        reload: set[str] = set()
        done = 0
        for item in manifest["items"]:
            if item["kind"] == "yaml":
                src = os.path.join(bdir, "files", item["rel"])
                dst = os.path.join(self.root, item["rel"])
                await self.hass.async_add_executor_job(shutil.copy2, src, dst)
                reload.add(item["reload"])
            elif item["kind"] == "files":
                for rel in item["rels"]:
                    await self.hass.async_add_executor_job(shutil.copy2, os.path.join(bdir, "files", rel), os.path.join(self.root, rel))
                reload.discard("stromnetz_generate")
                continue
            elif item["kind"] == "dashboard":
                await self._dashboards()[item["url"]].async_save(await self.hass.async_add_executor_job(load, item["file"]))
            elif item["kind"] == "stromnetz":
                await self._stromnetz_save(await self.hass.async_add_executor_job(load, item["file"]))
            elif item["kind"] == "energy":
                await (await self._energy()).async_update(await self.hass.async_add_executor_job(load, item["file"]))
            elif item["kind"] == "entry":
                saved = await self.hass.async_add_executor_job(load, item["file"])
                entry = self.hass.config_entries.async_get_entry(item["entry_id"])
                if entry is not None:
                    self.hass.config_entries.async_update_entry(entry, options=saved["options"], data=saved["data"])
                    await self._reload_entry(entry.entry_id)
            done += 1
        await self._reload(reload)
        return {"restored": done}
