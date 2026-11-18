"""Schlanker HTTP-Client für Shelly Gen2+ (JSON-RPC über /rpc, Digest-Auth SHA-256).

Bewusst kurze HTTP-Anfragen statt WebSocket: Gen2-Geräte haben nur wenige Verbindungs-Slots,
einen davon belegt Home Assistant.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import secrets
from typing import Any

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=8)
PROBE_TIMEOUT = aiohttp.ClientTimeout(total=2)
AUTH_RE = re.compile(r'(\w+)="?([^",]*)"?')


class ShellyError(Exception):
    """Gerät nicht erreichbar, Fehlerantwort oder fehlende Zugangsdaten."""


class AuthRequired(ShellyError):
    pass


def _base(host: str, port: int | None) -> str:
    return f"http://{host}" + ("" if port in (None, 80) else f":{port}")


def _digest(www: str, password: str, method: str, uri: str) -> str:
    params = dict(AUTH_RE.findall(www))
    algo = params.get("algorithm", "SHA-256").upper()
    hfn = hashlib.sha256 if "256" in algo else hashlib.md5

    def H(text: str) -> str:
        return hfn(text.encode()).hexdigest()

    realm, nonce = params.get("realm", ""), params.get("nonce", "")
    cnonce, nc = secrets.token_hex(8), "00000001"
    ha1 = H(f"admin:{realm}:{password}")
    ha2 = H(f"{method}:{uri}")
    response = H(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
    return (f'Digest username="admin", realm="{realm}", nonce="{nonce}", uri="{uri}", '
            f'algorithm={"SHA-256" if "256" in algo else "MD5"}, response="{response}", qop=auth, nc={nc}, cnonce="{cnonce}"')


async def probe(session: aiohttp.ClientSession, host: str, port: int | None = None,
                timeout: aiohttp.ClientTimeout = PROBE_TIMEOUT) -> dict[str, Any] | None:
    """GET /shelly (ohne Auth): Identität, Modell, Generation. None, wenn kein Shelly."""
    try:
        async with session.get(f"{_base(host, port)}/shelly", timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError):
        return None
    if not isinstance(data, dict) or not (data.get("mac") or data.get("id")):
        return None
    if "gen" not in data:
        data["gen"] = 1  # Gen1 liefert type statt gen
    return data


async def rpc(session: aiohttp.ClientSession, host: str, method: str, params: dict | None = None, *,
              port: int | None = None, password: str | None = None) -> Any:
    uri = "/rpc"
    body = {"id": 1, "method": method}
    if params:
        body["params"] = params
    url = f"{_base(host, port)}{uri}"
    try:
        async with session.post(url, json=body, timeout=TIMEOUT) as resp:
            if resp.status == 401:
                if not password:
                    raise AuthRequired("Passwort erforderlich")
                header = _digest(resp.headers.get("WWW-Authenticate", ""), password, "POST", uri)
                async with session.post(url, json=body, timeout=TIMEOUT, headers={"Authorization": header}) as r2:
                    if r2.status == 401:
                        raise AuthRequired("Passwort falsch")
                    data = await r2.json(content_type=None)
            else:
                data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
        raise ShellyError(f"nicht erreichbar ({type(err).__name__})") from err
    except ValueError as err:
        raise ShellyError("ungültige Antwort") from err
    if isinstance(data, dict) and data.get("error"):
        e = data["error"]
        raise ShellyError(f"{e.get('code')}: {e.get('message')}")
    return data.get("result") if isinstance(data, dict) else data


async def fetch_all(session: aiohttp.ClientSession, host: str, *, port: int | None = None,
                    password: str | None = None) -> dict[str, Any]:
    """Geräteinfo, Konfiguration und Status nacheinander (nie parallel auf dasselbe Gerät)."""
    info = await rpc(session, host, "Shelly.GetDeviceInfo", port=port, password=password)
    config = await rpc(session, host, "Shelly.GetConfig", port=port, password=password)
    status = await rpc(session, host, "Shelly.GetStatus", port=port, password=password)
    return {"info": info, "config": config, "status": status}


def expand_ranges(ranges: list[str], limit: int) -> list[str]:
    hosts: list[str] = []
    for r in ranges:
        net = ipaddress.ip_network(r, strict=False)
        if net.num_addresses == 1:
            hosts.append(str(net.network_address))
            continue
        for ip in net.hosts():
            hosts.append(str(ip))
            if len(hosts) >= limit:
                return hosts
    return hosts


def mac_of(data: dict) -> str | None:
    mac = (data.get("mac") or "").replace(":", "").upper()
    return mac or None
