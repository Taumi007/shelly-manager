"""WebSocket-API für das Panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .config_flow import parse_ranges
from .const import DOMAIN, SIGNAL_EVENT
from .model import build_row


def _manager(hass: HomeAssistant, connection, msg):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "Shelly Manager ist nicht eingerichtet")
        return None
    return entries[0].runtime_data


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/get"})
@callback
def ws_get(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    areas = [{"id": a.id, "name": a.name, "floor": a.floor_id} for a in ar.async_get(hass).async_list_areas()]
    connection.send_result(msg["id"], {
        "rows": m.rows(), "areas": sorted(areas, key=lambda a: a["name"].casefold()),
        "busy": m.busy, "scan": m.scan_state,
        "options": {"ranges": m.ranges, "mdns": m.mdns_enabled, "full_interval": m.full_interval,
                    "wifi_ssid": m.provision.ssid, "has_wifi_password": bool(m.provision.password),
                    "auto_provision": m.provision.auto_enabled, "provision_wifi": m.provision.has_wifi},
        "ble": m.provision.candidates(),
        "unifi": m.unifi.status(),
        "new_devices": m.data.get("new_devices", {}),
        "entry_id": m.entry.entry_id,
    })


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/device", vol.Required("mac"): str})
@callback
def ws_device(hass: HomeAssistant, connection, msg) -> None:
    """Ein Gerät mit Rohdaten (Info, Konfiguration, Status)."""
    if (m := _manager(hass, connection, msg)) is None:
        return
    dev = m.device(msg["mac"])
    if dev is None:
        connection.send_error(msg["id"], "not_found", "Gerät nicht gefunden")
        return
    sh = dev.get("shelly") or {}
    connection.send_result(msg["id"], {"row": build_row(dev, m.areas()),
                                       "raw": {k: sh.get(k) for k in ("info", "config", "status")}})


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): "shelly_manager/refresh",
    vol.Required("mode"): vol.In(["status", "full"]),
    vol.Optional("macs"): [str],
})
@callback
def ws_refresh(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"queued": m.refresh(msg["mode"], msg.get("macs"))})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/scan", vol.Optional("ranges"): str})
@callback
def ws_scan(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    try:
        ranges = parse_ranges(msg["ranges"]) if msg.get("ranges") else None
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_range", f"Ungültiger IP-Bereich: {err}")
        return
    if not (ranges or m.ranges):
        connection.send_error(msg["id"], "no_range", "Kein IP-Bereich angegeben")
        return
    connection.send_result(msg["id"], {"hosts": m.scan(ranges)})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg) -> None:
    """Fortschritt (Abfragen, Scan) und Änderungsmeldungen live ans Panel."""

    @callback
    def forward(event: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], event))

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(hass, SIGNAL_EVENT, forward)
    connection.send_result(msg["id"])


NAME_ITEMS = [{
    vol.Required("mac"): str,
    vol.Required("direction"): vol.In(["to_shelly", "to_ha"]),
    vol.Optional("keys"): [str],
    vol.Optional("only"): [str],  # »key|target«, z. B. »switch:0|ha«
}]


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/names/preview", vol.Required("items"): NAME_ITEMS})
@callback
def ws_names_preview(hass: HomeAssistant, connection, msg) -> None:
    """Was »Namen übernehmen« ändern würde, ohne etwas zu schreiben."""
    if (m := _manager(hass, connection, msg)) is None:
        return
    ops, skipped = m.plan_names(msg["items"])
    connection.send_result(msg["id"], {"ops": ops, "skipped": skipped})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/names/apply", vol.Required("items"): NAME_ITEMS,
                                  vol.Optional("op_id"): str})
@websocket_api.async_response
async def ws_names_apply(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    ops, skipped = m.plan_names(msg["items"])
    results = await m.apply_names(ops, msg.get("op_id"))
    connection.send_result(msg["id"], {"results": results, "skipped": skipped})


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): "shelly_manager/input/set",
    vol.Required("mac"): str,
    vol.Required("key"): vol.Match(r"^input:\d+$"),
    vol.Optional("name"): str,
    vol.Optional("enable"): bool,
})
@websocket_api.async_response
async def ws_input_set(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    from .client import ShellyError
    try:
        res = await m.set_input(msg["mac"], msg["key"], msg.get("name"), msg.get("enable"))
    except ShellyError as err:
        connection.send_error(msg["id"], "shelly_error", str(err))
        return
    connection.send_result(msg["id"], res)


CHANGES = [{vol.Required("id"): str, vol.Optional("value"): vol.Any(str, int, float, bool, None),
            vol.Optional("channel"): vol.Any(int, None)}]


def _guard(connection, msg, fn):
    from .settings import SettingError
    try:
        return fn()
    except SettingError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return None


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/settings/catalog"})
@callback
def ws_catalog(hass: HomeAssistant, connection, msg) -> None:
    from .settings import catalog_for_frontend
    connection.send_result(msg["id"], {"settings": catalog_for_frontend()})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/batch/preview",
                                  vol.Required("macs"): [str], vol.Required("changes"): CHANGES})
@callback
def ws_batch_preview(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    res = _guard(connection, msg, lambda: m.jobs.preview(msg["macs"], msg["changes"]))
    if res is not None:
        connection.send_result(msg["id"], {"devices": res})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/batch/apply", vol.Required("macs"): [str],
                                  vol.Required("changes"): CHANGES, vol.Optional("label", default="Einstellungen"): str})
@callback
def ws_batch_apply(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    job_id = _guard(connection, msg, lambda: m.jobs.start({mac: msg["changes"] for mac in msg["macs"]}, msg["label"]))
    if job_id is not None:
        connection.send_result(msg["id"], {"job_id": job_id})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/batch/undo", vol.Required("job_id"): str})
@callback
def ws_batch_undo(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    job_id = _guard(connection, msg, lambda: m.jobs.undo(msg["job_id"]))
    if job_id is not None:
        connection.send_result(msg["id"], {"job_id": job_id})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/jobs"})
@callback
def ws_jobs(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"jobs": m.jobs.jobs()})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/reboot", vol.Required("macs"): [str],
                                  vol.Optional("op_id"): str})
@callback
def ws_reboot(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"queued": m.jobs.reboot(msg["macs"], msg.get("op_id"))})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/helper/info", vol.Required("entity_id"): str})
@callback
def ws_helper_info(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    res = _guard(connection, msg, lambda: m.jobs.helper_info(msg["entity_id"]))
    if res is not None:
        connection.send_result(msg["id"], res)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/helper/convert", vol.Required("entity_id"): str,
                                  vol.Optional("remove_switch", default=False): bool})
@websocket_api.async_response
async def ws_helper_convert(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    from .client import ShellyError
    from .settings import SettingError
    try:
        res = await m.jobs.convert_helper(msg["entity_id"], msg["remove_switch"])
    except (SettingError, ShellyError) as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], res)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/names/check", vol.Required("name"): str,
                                  vol.Optional("area"): vol.Any(str, None), vol.Optional("kind", default="channel"): str})
@callback
def ws_names_check(hass: HomeAssistant, connection, msg) -> None:
    """Namensregeln für einen geplanten Namen (Prüfung ist nur ein Hinweis)."""
    from .naming import check
    connection.send_result(msg["id"], {"issues": check(msg["name"], msg.get("area"), msg["kind"])})


STAGE = vol.In(["stable", "beta"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/firmware/preview",
                                  vol.Optional("macs"): [str], vol.Optional("stage", default="stable"): STAGE})
@callback
def ws_fw_preview(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"devices": m.firmware.preview(msg.get("macs"), msg["stage"])})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/firmware/check", vol.Optional("macs"): [str]})
@callback
def ws_fw_check(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"queued": m.firmware.check(msg.get("macs"))})


@websocket_api.require_admin
@websocket_api.websocket_command({
    vol.Required("type"): "shelly_manager/firmware/start", vol.Required("macs"): [str],
    vol.Optional("stage", default="stable"): STAGE,
    vol.Optional("concurrency", default=1): vol.All(int, vol.Range(min=1, max=4)),
    vol.Optional("gap", default=30): vol.All(vol.Coerce(float), vol.Range(min=0, max=600)),
    vol.Optional("keep_state", default=True): bool,
    vol.Optional("stop_on_error", default=True): bool,
})
@callback
def ws_fw_start(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    job_id = m.firmware.start(msg["macs"], msg["stage"], msg["concurrency"], msg["gap"], msg["keep_state"],
                              msg["stop_on_error"])
    connection.send_result(msg["id"], {"job_id": job_id})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/firmware/cancel", vol.Required("job_id"): str})
@callback
def ws_fw_cancel(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    m.firmware.cancel(msg["job_id"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/names/set", vol.Required("mac"): str,
                                  vol.Required("key"): str, vol.Required("name"): str,
                                  vol.Optional("area_id"): vol.Any(str, None)})
@websocket_api.async_response
async def ws_names_set(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    from .client import ShellyError
    try:
        res = await m.set_name(msg["mac"], msg["key"], msg["name"], msg.get("area_id"))
    except (ShellyError, ValueError) as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], res)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/reload", vol.Required("macs"): [str]})
@callback
def ws_reload(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"queued": m.reload_entries(msg["macs"])})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/history", vol.Required("mac"): str})
@callback
def ws_history(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    from .monitor import series
    connection.send_result(msg["id"], series(m.history, msg["mac"]))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/provision/start", vol.Required("flow_id"): str,
                                  vol.Optional("ssid"): str, vol.Optional("password"): str,
                                  vol.Optional("restore", default=False): bool})
@callback
def ws_provision_start(hass: HomeAssistant, connection, msg) -> None:
    """Bluetooth-Fund ins WLAN bringen; ohne Angaben mit dem Standard-WLAN. restore: danach Sicherung einspielen."""
    if (m := _manager(hass, connection, msg)) is None:
        return
    ssid = msg.get("ssid")
    if not ssid and not m.provision.has_wifi:
        connection.send_error(msg["id"], "no_wifi", "Kein WLAN angegeben (WLAN-Vorgabe im Tab »Vorgaben« oder in den Optionen setzen)")
        return
    if msg["restore"]:
        mac = next((c["mac"] for c in m.provision.candidates() if c["flow_id"] == msg["flow_id"]), None)
        if not mac or not m.reset.backup(mac):
            connection.send_error(msg["id"], "no_backup", "Für dieses Gerät gibt es keine Sicherung")
            return
        m.reset.mark_restore(mac)
    m.provision.start(msg["flow_id"], ssid, msg.get("password"))
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/new/dismiss", vol.Required("mac"): str})
@callback
def ws_new_dismiss(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    m.provision.dismiss(msg["mac"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/profile",
                                  vol.Optional("changes"): CHANGES})
@callback
def ws_profile(hass: HomeAssistant, connection, msg) -> None:
    """Basis-Profil für neue Geräte lesen oder (mit »changes«) speichern."""
    if (m := _manager(hass, connection, msg)) is None:
        return
    if "changes" in msg:
        m.provision.save_profile(msg["changes"])
    connection.send_result(msg["id"], {"changes": m.provision.profile()})


UNIFI_WHAT = [vol.In(["names", "fixed_ip", "cleanup", "release"])]


def _unifi_rows(m, macs):
    rows = m.rows()
    return [r for r in rows if r["in_ha"] and (not macs or r["mac"] in macs)]


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/unifi/preview", vol.Optional("macs"): [str],
                                  vol.Required("what"): UNIFI_WHAT})
@callback
def ws_unifi_preview(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    ops, skipped = m.unifi.plan(_unifi_rows(m, msg.get("macs")), msg["what"], m.fixed_range)
    connection.send_result(msg["id"], {"ops": ops, "skipped": skipped, "status": m.unifi.status()})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/unifi/apply", vol.Required("macs"): [str],
                                  vol.Required("what"): UNIFI_WHAT, vol.Optional("only"): [str], vol.Optional("op_id"): str})
@websocket_api.async_response
async def ws_unifi_apply(hass: HomeAssistant, connection, msg) -> None:
    """Neu planen (aktueller Stand) und nur die bestätigten Änderungen »mac|kind« schreiben."""
    if (m := _manager(hass, connection, msg)) is None:
        return
    ops, _ = m.unifi.plan(_unifi_rows(m, msg["macs"]), msg["what"], m.fixed_range)
    if "only" in msg:
        allowed = set(msg["only"])
        ops = [o for o in ops if f"{o['mac']}|{o['kind']}" in allowed]
    try:
        results = await m.unifi.apply(ops, lambda item, state, err=None: m.emit_op(msg.get("op_id"), item, state, err))
    except RuntimeError as err:
        connection.send_error(msg["id"], "unifi", str(err))
        return
    m.emit({"kind": "changed"})
    connection.send_result(msg["id"], {"results": results})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/entries/retitle", vol.Required("macs"): [str]})
@callback
def ws_retitle(hass: HomeAssistant, connection, msg) -> None:
    """Titel der Shelly-Integrationseinträge auf den vollen Gerätenamen im Shelly setzen."""
    if (m := _manager(hass, connection, msg)) is None:
        return
    # Nur Geräte, deren Name schon festgelegt ist (Stufe 1 und 2 erledigt)
    ready = {r["mac"] for r in m.rows() if r["name_stage"] not in (1, 2, 3)}
    changed = [mac for mac in msg["macs"] if mac in ready and m.retitle(mac)]
    m.emit({"kind": "changed"})
    connection.send_result(msg["id"], {"changed": len(changed), "skipped": len([x for x in msg["macs"] if x not in ready])})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/fallback/get", vol.Required("mac"): str})
@websocket_api.async_response
async def ws_fallback_get(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    st = m.fallback.status(msg["mac"])
    live = await m.fallback.live(msg["mac"]) if st["script_id"] is not None else {}
    connection.send_result(msg["id"], {**st, **live, "hue": m.fallback.hue_bridge() is not None})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/fallback/hue"})
@websocket_api.async_response
async def ws_fallback_hue(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    from .fallback import FallbackError
    try:
        connection.send_result(msg["id"], await m.fallback.hue_targets())
    except FallbackError as err:
        connection.send_error(msg["id"], "hue", str(err))


TARGET = vol.Schema({vol.Required("kind"): vol.In(["hue", "shelly", "local"])}, extra=vol.ALLOW_EXTRA)
BINDING = vol.Schema({
    vol.Required("type"): vol.In(["double", "single", "switch"]),
    vol.Required("inputs"): [vol.Coerce(int)],
    vol.Optional("name"): str, vol.Optional("dim", default=True): bool,
    vol.Required("targets"): [TARGET], vol.Optional("scenes", default=[]): [vol.Any(str, dict)],
}, extra=vol.ALLOW_EXTRA)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/fallback/save", vol.Required("mac"): str,
                                  vol.Required("bindings"): [BINDING], vol.Optional("enabled", default=True): bool})
@websocket_api.async_response
async def ws_fallback_save(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    from .client import ShellyError
    from .fallback import FallbackError
    try:
        res = await m.fallback.save(msg["mac"], msg["bindings"], msg["enabled"])
    except (FallbackError, ShellyError) as err:
        connection.send_error(msg["id"], "fallback", str(err))
        return
    m.emit({"kind": "changed"})
    connection.send_result(msg["id"], res)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/fallback/remove", vol.Required("mac"): str})
@websocket_api.async_response
async def ws_fallback_remove(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    from .client import ShellyError
    from .fallback import FallbackError
    try:
        await m.fallback.remove(msg["mac"])
    except (FallbackError, ShellyError) as err:
        connection.send_error(msg["id"], "fallback", str(err))
        return
    m.emit({"kind": "changed"})
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/fallback/always", vol.Required("mac"): str,
                                  vol.Required("on"): bool})
@websocket_api.async_response
async def ws_fallback_always(hass: HomeAssistant, connection, msg) -> None:
    """Fallback dauerhaft aktiv schalten – nur ein KVS-Wert, das Script bleibt unverändert."""
    if (m := _manager(hass, connection, msg)) is None:
        return
    from .client import ShellyError
    from .fallback import FallbackError
    try:
        await m.fallback.set_always(msg["mac"], msg["on"])
    except (FallbackError, ShellyError) as err:
        connection.send_error(msg["id"], "fallback", str(err))
        return
    m.emit({"kind": "changed"})
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/fallback/test", vol.Required("mac"): str,
                                  vol.Required("input"): int,
                                  vol.Required("event"): vol.In(["single_push", "long_push", "btn_up"])})
@websocket_api.async_response
async def ws_fallback_test(hass: HomeAssistant, connection, msg) -> None:
    """Tastendruck im Script simulieren (ohne HA-Prüfung)."""
    if (m := _manager(hass, connection, msg)) is None:
        return
    from .client import ShellyError
    from .fallback import FallbackError
    try:
        res = await m.fallback.test(msg["mac"], msg["input"], msg["event"])
    except (FallbackError, ShellyError) as err:
        connection.send_error(msg["id"], "fallback", str(err))
        return
    connection.send_result(msg["id"], {"result": res})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/ha/disable", vol.Required("mac"): str,
                                  vol.Required("key"): str})
@callback
def ws_ha_disable(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    from .client import ShellyError
    try:
        what = m.disable_in_ha(msg["mac"], msg["key"])
    except ShellyError as err:
        connection.send_error(msg["id"], "failed", str(err))
        return
    m.emit({"kind": "changed"})
    connection.send_result(msg["id"], {"disabled": what})


PRESET_KIND = vol.In(["wifi", "mqtt", "ws"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/presets/get"})
@websocket_api.async_response
async def ws_presets_get(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"presets": await m.presets.public(),
                                       "eligible": {k: m.presets.eligible(k) for k in ("wifi", "mqtt", "ws")}})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/presets/save", vol.Required("kind"): PRESET_KIND,
                                  vol.Required("values"): dict})
@websocket_api.async_response
async def ws_presets_save(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    m.presets.save(msg["kind"], msg["values"])
    connection.send_result(msg["id"], {"presets": await m.presets.public()})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/presets/apply", vol.Required("kind"): PRESET_KIND,
                                  vol.Required("macs"): [str]})
@websocket_api.async_response
async def ws_presets_apply(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    try:
        job_id = await m.presets.apply(msg["kind"], msg["macs"])
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"job_id": job_id})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/unifi/radio", vol.Optional("ssid"): str})
@websocket_api.async_response
async def ws_unifi_radio(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    try:
        connection.send_result(msg["id"], await m.unifi.radio(msg.get("ssid")))
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "unifi", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/unifi/wlan", vol.Required("name"): str,
                                  vol.Required("data"): dict})
@websocket_api.async_response
async def ws_unifi_wlan(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    try:
        connection.send_result(msg["id"], await m.unifi.set_wlan(msg["name"], msg["data"]))
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "unifi", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/diag/mdns", vol.Optional("seconds", default=30): vol.All(int, vol.Range(min=5, max=120))})
@websocket_api.async_response
async def ws_diag_mdns(hass: HomeAssistant, connection, msg) -> None:
    from .diag import sniff

    try:
        connection.send_result(msg["id"], await hass.async_add_executor_job(sniff, float(msg["seconds"])))
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "diag", str(err))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/reset/preview", vol.Required("mac"): str})
@callback
def ws_reset_preview(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], m.reset.plan(msg["mac"]))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/reset/start", vol.Required("mac"): str,
                                  vol.Required("steps"): [str], vol.Optional("op_id"): str})
@callback
def ws_reset_start(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    if msg["mac"] in m.busy:
        connection.send_error(msg["id"], "busy", "Gerät wird gerade bearbeitet")
        return
    connection.send_result(msg["id"], {"steps": m.reset.start(msg["mac"], msg["steps"], msg.get("op_id"))})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/found/forget", vol.Required("mac"): str,
                                  vol.Optional("permanent", default=False): bool})
@websocket_api.async_response
async def ws_found_forget(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"result": await m.reset.forget_found(msg["mac"], msg["permanent"])})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/reset/backup_delete", vol.Required("mac"): str})
@callback
def ws_reset_backup_delete(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    m.reset.delete_backup(msg["mac"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/reset/backup", vol.Required("mac"): str})
@callback
def ws_reset_backup(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"backup": m.reset.backup(msg["mac"])})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/provision/auto", vol.Required("enabled"): bool})
@callback
def ws_provision_auto(hass: HomeAssistant, connection, msg) -> None:
    """Neue Bluetooth-Funde automatisch ins WLAN bringen (bleibt über Neustarts erhalten)."""
    if (m := _manager(hass, connection, msg)) is None:
        return
    m.provision.set_auto(msg["enabled"])
    connection.send_result(msg["id"], {"enabled": m.provision.auto_enabled, "wifi": m.provision.has_wifi})


MAPPING = vol.Schema({str: str})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/refs/scan", vol.Required("mapping"): MAPPING})
@websocket_api.async_response
async def ws_refs_scan(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], await m.refs.scan(msg["mapping"]))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/refs/count", vol.Required("ids"): [str]})
@websocket_api.async_response
async def ws_refs_count(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"counts": await m.refs.count(msg["ids"])})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/refs/apply", vol.Required("mapping"): MAPPING,
                                  vol.Required("fids"): [str], vol.Optional("op_id"): str})
@websocket_api.async_response
async def ws_refs_apply(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    op = msg.get("op_id")
    try:
        res = await m.refs.apply(msg["mapping"], msg["fids"], lambda item, state, note=None: m.emit_op(op, item, state, note))
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "refs", str(err))
        return
    connection.send_result(msg["id"], res)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/refs/orphans"})
@websocket_api.async_response
async def ws_refs_orphans(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], await m.refs.orphans())


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/refs/backups"})
@websocket_api.async_response
async def ws_refs_backups(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    connection.send_result(msg["id"], {"backups": await hass.async_add_executor_job(m.refs.backups)})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): "shelly_manager/refs/undo", vol.Required("backup_id"): str})
@websocket_api.async_response
async def ws_refs_undo(hass: HomeAssistant, connection, msg) -> None:
    if (m := _manager(hass, connection, msg)) is None:
        return
    try:
        res = await m.refs.undo(msg["backup_id"])
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "refs", str(err))
        return
    connection.send_result(msg["id"], res)


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    for cmd in (ws_get, ws_device, ws_refresh, ws_scan, ws_subscribe, ws_names_preview, ws_names_apply, ws_input_set,
                ws_catalog, ws_batch_preview, ws_batch_apply, ws_batch_undo, ws_jobs, ws_reboot,
                ws_helper_info, ws_helper_convert, ws_names_check,
                ws_fw_preview, ws_fw_check, ws_fw_start, ws_fw_cancel, ws_names_set, ws_reload, ws_history,
                ws_provision_start, ws_new_dismiss, ws_profile, ws_unifi_preview, ws_unifi_apply, ws_retitle,
                ws_fallback_get, ws_fallback_hue, ws_fallback_save, ws_fallback_remove, ws_fallback_test,
                ws_ha_disable, ws_presets_get, ws_presets_save, ws_presets_apply, ws_fallback_always, ws_unifi_radio, ws_unifi_wlan, ws_diag_mdns,
                ws_reset_preview, ws_reset_start, ws_reset_backup, ws_reset_backup_delete, ws_found_forget, ws_provision_auto,
                ws_refs_scan, ws_refs_count, ws_refs_apply, ws_refs_backups, ws_refs_undo, ws_refs_orphans):
        websocket_api.async_register_command(hass, cmd)
