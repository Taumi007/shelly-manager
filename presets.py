"""Vorgaben für WLAN, MQTT und ausgehenden WebSocket – einmal hinterlegen, gezielt auf Geräte anwenden.

Jedes Feld ist entweder fester Text oder ein Verweis auf einen Eintrag in secrets.yaml ({"secret": "schlüssel"}).
secrets.yaml wird erst beim Anwenden gelesen; Passwörter werden nie ans Panel geschickt.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .inventory import Manager

SECRET = {"wifi": ("pass",), "mqtt": ("pass",)}
FIELDS = {"wifi": ("ssid", "pass"), "mqtt": ("server", "user", "pass")}


class SecretMissing(ValueError):
    pass


def _ref(value: Any) -> str | None:
    return value.get("secret") if isinstance(value, dict) else None


class Presets:
    def __init__(self, manager: Manager) -> None:
        self.m = manager
        self.data: dict[str, dict] = manager.data.setdefault("presets", {})

    # ── secrets.yaml ──────────────────────────────────────────────────────────
    async def secrets(self) -> dict[str, Any]:
        path = self.m.hass.config.path("secrets.yaml")

        def load() -> dict[str, Any]:
            import yaml

            if not os.path.exists(path):
                return {}

            class Loader(yaml.SafeLoader):
                pass

            # HA-eigene Tags (z. B. !include) nicht auswerten, nur einfache Schlüssel lesen
            Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
            with open(path, encoding="utf-8") as fh:
                data = yaml.load(fh, Loader=Loader)  # noqa: S506 – SafeLoader-Ableitung
            return data if isinstance(data, dict) else {}

        return await self.m.hass.async_add_executor_job(load)

    async def resolved(self, kind: str) -> dict[str, Any]:
        """Vorgabe mit aufgelösten secrets.yaml-Verweisen (nur im Backend verwenden)."""
        values = dict(self.data.get(kind) or {})
        refs = {k: _ref(v) for k, v in values.items() if _ref(v)}
        if refs:
            sec = await self.secrets()
            for k, key in refs.items():
                if key not in sec or sec[key] in (None, ""):
                    raise SecretMissing(f"secrets.yaml: Eintrag »{key}« fehlt oder ist leer")
                values[k] = str(sec[key])
        return values

    async def public(self) -> dict[str, Any]:
        sec = await self.secrets()
        out: dict[str, Any] = {}
        for kind, values in self.data.items():
            if kind == "ws":
                continue
            pub: dict[str, Any] = {}
            for k, v in values.items():
                if (key := _ref(v)) is not None:
                    pub[k] = {"secret": key, "exists": key in sec and sec[key] not in (None, "")}
                elif k in SECRET.get(kind, ()):
                    pub[f"has_{k}"] = bool(v)
                else:
                    pub[k] = v
            out[kind] = pub
        out["ws"] = {"server": self.m.fallback.ws_url()}
        out["secret_keys"] = sorted(str(k) for k in sec)  # nur Namen, nie Werte
        return out

    def save(self, kind: str, values: dict) -> None:
        cur = self.data.setdefault(kind, {})
        for k, v in values.items():
            if k.startswith("has_") or k == "secret_keys":
                continue  # Anzeige-Felder aus public(), nicht speichern
            if isinstance(v, dict):
                key = str(v.get("secret") or "").strip()
                if key:
                    cur[k] = {"secret": key}
                else:
                    cur.pop(k, None)
                continue
            if k in SECRET.get(kind, ()) and not v:
                if _ref(cur.get(k)):
                    cur.pop(k, None)  # Wechsel von secrets.yaml zurück auf festen Wert ohne Eingabe
                continue  # leeres Passwortfeld = gespeichertes behalten
            cur[k] = v
        self.m._save()

    async def wifi(self) -> tuple[str, str] | None:
        """SSID/Passwort aus der WLAN-Vorgabe – für die Bluetooth-Einrichtung neuer Geräte."""
        if not (self.data.get("wifi") or {}).get("ssid"):
            return None
        v = await self.resolved("wifi")
        return v["ssid"], v.get("pass") or ""

    def eligible(self, kind: str) -> list[str]:
        """Geräte, für die eine Vorgabe sinnvoll ist (WebSocket nur für Akku- und Fallback-Geräte)."""
        out = []
        fb = self.m.data.get("fallback") or {}
        for dev in self.m.collect():
            if not dev.get("ha") or dev.get("gen") == 1:
                continue
            if kind == "ws" and not (dev.get("sleeping") or (fb.get(dev["mac"]) or {}).get("enabled")):
                continue
            out.append(dev["mac"])
        return out

    async def changes(self, kind: str) -> list[dict]:
        v = await self.resolved(kind) if kind != "ws" else {}
        if kind == "ws":
            return [{"id": "ws_enable", "value": True}, {"id": "ws_server", "value": self.m.fallback.ws_url()}]
        if kind == "mqtt":
            out = [{"id": "mqtt_enable", "value": bool(v.get("enable", True))}]
            for key, sid in (("server", "mqtt_server"), ("user", "mqtt_user"), ("pass", "mqtt_pass")):
                if v.get(key):
                    out.append({"id": sid, "value": v[key]})
            return out
        if kind == "wifi":
            if not v.get("ssid"):
                raise ValueError("Keine SSID in der Vorgabe")
            slot = "sta1" if v.get("slot", "sta1") == "sta1" else "sta"
            out = [{"id": f"{slot}_ssid", "value": v["ssid"]}]
            if v.get("pass"):
                out.append({"id": f"{slot}_pass", "value": v["pass"]})
            if slot == "sta1":
                out.append({"id": "sta1_enable", "value": True})
            return out
        raise ValueError(f"Unbekannte Vorgabe {kind}")

    async def apply(self, kind: str, macs: list[str]) -> str:
        ok = set(self.eligible(kind))
        targets = [m for m in macs if m in ok]
        if not targets:
            raise ValueError("Keine passenden Geräte ausgewählt")
        labels = {"ws": "Vorgabe WebSocket zu HA", "mqtt": "Vorgabe MQTT", "wifi": "Vorgabe WLAN"}
        changes = await self.changes(kind)
        return self.m.jobs.start({m: changes for m in targets}, labels[kind])
