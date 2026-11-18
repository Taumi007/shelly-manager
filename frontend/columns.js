// Spalten der Gerätetabelle: Wert (für Filter/Sortierung/Suche) und Darstellung
import { MDI, SEVERITY, WIFI_CODES, fmtAgo, fmtDuration, fmtW, h, icon, num, problemIcon } from "./util.js";

export const NAME_STATUS = {
  ok: { label: "Gleich", icon: MDI.ok, cls: "ok" },
  diff: { label: "Abweichend", icon: MDI.diff, cls: "sev-warning" },
  warn: { label: "Regel verletzt", icon: MDI.alert, cls: "sev-warning" },
  info: { label: "Hinweis", icon: MDI.info, cls: "sev-info" },
  unknown: { label: "Unbekannt", icon: MDI.help, cls: "muted" },
};

// Hinweise je Seite: welche Namensprobleme zum Shelly- bzw. HA-Namen gehören
const SHELLY_HINTS = ["shelly_empty", "shelly_no_area", "generic", "single_channel_named", "duplicate_channel", "input_unnamed"];
const HA_HINTS = ["ha_area_in_name", "no_area", "ha_no_user_name"];
const INFO_HINTS = ["no_area", "shelly_no_area", "ha_no_user_name", "input_unnamed"];

export const isInfoHint = (hint) => INFO_HINTS.includes(hint);

export function hintText(hint, isDevice) {
  const what = isDevice ? "Gerätename" : "Kanalname";
  return {
    no_area: `${isDevice ? "Gerät" : "Kanal"} hat in HA keinen Bereich`,
    ha_area_in_name: `${what} in HA enthält den Bereich (wird doppelt angezeigt)`,
    ha_no_user_name: `${what} in HA nicht eigens gesetzt – HA übernimmt den Shelly-Namen`,
    shelly_empty: `${what} im Shelly ist leer`,
    shelly_no_area: `${what} im Shelly beginnt mit keinem HA-Bereich`,
    generic: "Kanalname ist nur ein Standardname",
    duplicate_channel: "Kanalname im Shelly mehrfach im selben Gerät vergeben",
    input_unnamed: "Eingang ohne Namen – benennen oder deaktivieren",
    single_channel_named: "Einkanalig: Kanalname im Shelly sollte leer sein",
  }[hint] ?? hint;
}

function warnMark(item, side, isDevice) {
  const hints = (item?.hints ?? []).filter((x) => (side === "shelly" ? SHELLY_HINTS : HA_HINTS).includes(x));
  if (!hints.length) return null;
  // Reine Hinweise blau (i), echte Regelverstöße gelb (Dreieck)
  const warn = hints.some((x) => !INFO_HINTS.includes(x));
  return h("span", { class: `warnmark ${warn ? "sev-warning" : "sev-info"}`, title: hints.map((x) => hintText(x, isDevice)).join("\n") },
    icon(warn ? MDI.alert : MDI.info, 14));
}

// HA-Name mit vorangestelltem Bereich als Badge: der Bereich ist nur ergänzt, nicht Teil des Namens
export function haName(area, name, { item = null, isDevice = true } = {}) {
  if (name === null || name === undefined) return h("span", { class: "muted" }, "–");
  return h("span", { class: "nmwrap" }, area ? h("span", { class: "areatag", title: "Bereich in HA" }, area) : null,
    h("span", {}, name || h("span", { class: "muted" }, "(leer)")), warnMark(item, "ha", isDevice));
}

export function shellyName(name, { item = null, isDevice = true } = {}) {
  if (name === null || name === undefined) return h("span", { class: "muted" }, "nicht abgefragt");
  return h("span", { class: "nmwrap" }, h("span", {}, name || h("span", { class: "muted" }, "(leer)")), warnMark(item, "shelly", isDevice));
}

function worstChannelStatus(r) {
  const ch = r.names?.channels ?? [];
  if (!ch.length) return null;
  if (ch.some((c) => c.status === "diff")) return "diff";
  if (ch.some((c) => c.hints.some((x) => !INFO_HINTS.includes(x)))) return "warn";
  if (ch.some((c) => c.hints.length)) return "info";
  if (ch.some((c) => c.status === "unknown")) return "unknown";
  return "ok";
}

function itemStatus(it) {
  if (!it) return null;
  if (it.status === "diff") return "diff";
  if (it.hints.some((x) => !INFO_HINTS.includes(x))) return "warn";
  return it.hints.length ? "info" : it.status;
}

function statusCell(st, title) {
  if (!st) return h("span", { class: "muted" }, "–");
  const s = NAME_STATUS[st] ?? NAME_STATUS.unknown;
  return h("span", { class: `row ${s.cls}`, title: title || s.label }, icon(s.icon, 16));
}

const yesNo = (v) => (v === true ? "ja" : v === false ? "nein" : null);

function boolTag(v, { good = true } = {}) {
  if (v === null || v === undefined) return h("span", { class: "muted" }, "–");
  const cls = v ? (good ? "on" : "bad") : "off";
  return h("span", { class: `tag ${cls}` }, v ? "an" : "aus");
}

function connTag(enabled, connected) {
  if (!enabled) return h("span", { class: "tag off" }, "aus");
  if (connected === false) return h("span", { class: "tag bad" }, "getrennt");
  if (connected === true) return h("span", { class: "tag on" }, "verbunden");
  return h("span", { class: "tag" }, "an");
}

// Live aus HA: Schaltzustände der Kanäle
export function channelDots(row, hass) {
  const outs = row.channels.filter((c) => ["switch", "light", "rgb", "rgbw", "cct", "cover"].includes(c.component));
  if (!outs.length) return h("span", { class: "muted" }, "–");
  return h("span", { class: "dots" }, outs.map((c) => {
    const st = c.entity ? hass?.states[c.entity] : null;
    const on = st ? st.state === "on" || st.state === "open" : c.output === true;
    const na = st ? ["unavailable", "unknown"].includes(st.state) : c.output === undefined || c.output === null;
    const name = c.names?.ha_full || c.name || c.key;
    return h("span", { class: `dot${on ? " on" : ""}${na ? " na" : ""}`, title: `${name}: ${st ? st.state : (on ? "an" : "aus")}` });
  }));
}

export function livePower(row, hass) {
  let sum = null;
  for (const c of row.channels) {
    const v = num(hass, c.power_entity) ?? (typeof c.apower === "number" ? c.apower : null);
    if (v !== null) sum = (sum ?? 0) + v;
  }
  return sum;
}

function rssiCell(v) {
  if (typeof v !== "number") return h("span", { class: "muted" }, "–");
  const cls = v > -67 ? "good" : v > -75 ? "mid" : "bad";
  return h("span", { class: `rssi ${cls}` }, icon(MDI.wifi, 14), `${v} dBm`);
}

// ── Symbolspalten: Probleme getrennt nach WLAN, Firmware und Sonstigem ─────────────────────────
const FW_CODES = new Set(["update"]);
const SEV_RANK = { error: 3, warning: 2, info: 1 };
const SEV_LABEL = { 3: "Fehler", 2: "Warnung", 1: "Hinweis", 0: "–" };
function group(row, kind) {
  return row.problems.filter((p) => (kind === "wifi" ? WIFI_CODES.has(p.code) : kind === "fw" ? FW_CODES.has(p.code)
    : !WIFI_CODES.has(p.code) && !FW_CODES.has(p.code)));
}
function worst(list) {
  return list.reduce((m, p) => Math.max(m, SEV_RANK[p.severity] ?? 1), 0);
}
function iconCell(row, kind) {
  const list = group(row, kind);
  if (!list.length) return "";
  const rank = worst(list);
  const sev = rank === 3 ? SEVERITY.error : rank === 2 ? SEVERITY.warning : SEVERITY.info;
  const ic = kind === "wifi" ? MDI.wifi : kind === "fw" ? MDI.update : sev.icon;
  return h("span", { class: `${sev.cls}${kind === "wifi" ? " wifiprob" : ""}`, title: list.map((p) => `• ${p.text}`).join("\n") }, icon(ic, 16));
}
// rank: höher = braucht mehr Aufmerksamkeit (Sortierung absteigend bringt es nach oben)
const iconCol = (key, kind, title, ic) => ({ key, label: title, head: icon(ic, 16), group: "Zustand", type: "enum", def: true, cls: "c-ico",
  get: (r) => SEV_LABEL[worst(group(r, kind))], render: (r) => iconCell(r, kind),
  rank: (r) => worst(group(r, kind)) * 1000 + (kind === "wifi" && typeof r.rssi === "number" ? -r.rssi : 0) });
export const ICON_COLUMNS = [
  iconCol("st_other", "other", "Probleme (ohne WLAN/Firmware)", MDI.alert),
  iconCol("st_fw", "fw", "Firmware", MDI.update),
  iconCol("st_wifi", "wifi", "WLAN", MDI.wifi),
];

// Nur die Anzahl (Farbe = schwerstes Problem); die Art zeigen die Symbolspalten
export function problemCell(row) {
  if (!row.problems.length) return h("span", { class: "muted" }, "–");
  const sev = SEVERITY[row.severity] ?? SEVERITY.info;
  return h("span", { class: `${sev.cls} probcount`, title: row.problems.map((p) => `• ${p.text}`).join("\n") }, String(row.problems.length));
}

export function nameStatusCell(row) {
  const s = NAME_STATUS[row.name_status] ?? NAME_STATUS.unknown;
  const n = row.names;
  const lines = n ? [n.device, ...n.channels].flatMap((it) => it.hints.map((x) =>
    `${it.key === "device" ? "" : `${it.key}: `}${hintText(x, it.key === "device")}`)) : [];
  return h("span", { class: `row ${s.cls}`, title: [s.label, ...lines].join("\n") }, icon(s.icon, 16));
}

const GROUPS = ["Name", "Gerät", "Netzwerk", "UniFi", "Dienste", "Kanäle", "Home Assistant", "Zustand"];
export { GROUPS };

export const COLUMNS = [
  ...ICON_COLUMNS,
  // Name
  { key: "area", label: "Bereich", group: "Name", type: "enum", def: true, get: (r) => r.area },
  { key: "shelly_name", label: "Name im Shelly", group: "Name", type: "text", def: true, get: (r) => r.shelly_name,
    render: (r) => shellyName(r.names.device.shelly, { item: r.names.device }) },
  { key: "ha_name", label: "Name in HA", group: "Name", type: "text", def: true,
    get: (r) => (r.ha_name === null ? null : [r.area, r.ha_name].filter(Boolean).join(" ")),
    render: (r) => haName(r.area, r.ha_name, { item: r.names.device }) },
  { key: "name_status", label: "Abgleich", group: "Name", type: "enum", def: true,
    get: (r) => NAME_STATUS[r.name_status]?.label, render: nameStatusCell },
  { key: "device_name_status", label: "Gerätename", group: "Name", type: "enum",
    get: (r) => NAME_STATUS[itemStatus(r.names.device)]?.label,
    render: (r) => statusCell(itemStatus(r.names.device), r.names.device.hints.map((x) => hintText(x, true)).join("\n")) },
  { key: "channel_name_status", label: "Kanalnamen", group: "Name", type: "enum",
    get: (r) => NAME_STATUS[worstChannelStatus(r)]?.label,
    render: (r) => statusCell(worstChannelStatus(r), (r.names.channels ?? []).flatMap((c) => c.hints.map((x) => `${c.key}: ${hintText(x, false)}`)).join("\n")) },
  { key: "rule_issues", label: "Namensregeln", group: "Name", type: "num", cls: "num",
    get: (r) => (r.rule_issues ?? []).length,
    render: (r) => {
      const list = r.rule_issues ?? [];
      if (!list.length) return h("span", { class: "ok", title: "Entspricht den Namensregeln" }, icon(MDI.ok, 15));
      return h("span", { class: "row sev-warning", title: ["Namensregeln", ...list.map((x) => `${x.key === "device" ? "" : `${x.key}: `}${x.text}`)].join("\n") },
        icon(MDI.alert, 15), String(list.length));
    } },
  { key: "channel_names", label: "Kanalnamen im Shelly", group: "Name", type: "multi",
    get: (r) => (r.names.channels ?? []).map((c) => c.shelly).filter(Boolean),
    render: (r) => ((r.names.channels ?? []).length ? (r.names.channels.map((c) => c.shelly || "(leer)").join(" · ")) : "–") },
  // Gerät
  { key: "model", label: "Modell", group: "Gerät", type: "enum", def: true, get: (r) => r.model },
  { key: "model_id", label: "Modell-ID", group: "Gerät", type: "enum", get: (r) => r.model_id },
  { key: "app", label: "App", group: "Gerät", type: "enum", get: (r) => r.app },
  { key: "gen", label: "Gen", group: "Gerät", type: "enum", get: (r) => (r.gen ? `Gen${r.gen}` : null) },
  { key: "fw", label: "Firmware", group: "Gerät", type: "enum", def: true, get: (r) => r.fw },
  { key: "update", label: "Update", group: "Gerät", type: "enum", def: true, get: (r) => r.update,
    render: (r) => (r.update ? h("span", { class: "tag upd" }, r.update) : h("span", { class: "muted" }, "–")) },
  { key: "beta", label: "Beta", group: "Gerät", type: "enum", get: (r) => r.beta },
  { key: "mac", label: "MAC", group: "Gerät", type: "text", get: (r) => r.mac },
  { key: "uptime", label: "Laufzeit", group: "Gerät", type: "num", cls: "num", get: (r) => r.uptime, render: (r) => fmtDuration(r.uptime) },
  { key: "temperature", label: "Temperatur", group: "Gerät", type: "num", cls: "num", get: (r) => r.temperature,
    render: (r) => (typeof r.temperature === "number" ? `${r.temperature.toFixed(0)} °C` : "–") },
  { key: "auth", label: "Passwort", group: "Gerät", type: "bool", get: (r) => yesNo(r.auth), render: (r) => boolTag(r.auth) },
  { key: "eco", label: "Eco-Modus", group: "Gerät", type: "bool", get: (r) => yesNo(r.eco), render: (r) => boolTag(r.eco) },
  { key: "discoverable", label: "Auffindbar", group: "Gerät", type: "bool", get: (r) => yesNo(r.discoverable), render: (r) => boolTag(r.discoverable) },
  { key: "tz", label: "Zeitzone", group: "Gerät", type: "enum", get: (r) => r.tz },
  { key: "sntp", label: "SNTP", group: "Gerät", type: "enum", get: (r) => r.sntp },
  { key: "debug_ws", label: "Debug-Log WS", group: "Gerät", type: "bool", get: (r) => yesNo(r.debug_ws), render: (r) => boolTag(r.debug_ws, { good: false }) },
  { key: "restart_required", label: "Neustart nötig", group: "Gerät", type: "bool", get: (r) => yesNo(r.restart_required),
    render: (r) => (r.restart_required ? h("span", { class: "tag bad" }, "ja") : h("span", { class: "muted" }, "–")) },
  // Netzwerk
  { key: "host", label: "IP", group: "Netzwerk", type: "text", def: true, get: (r) => r.host,
    render: (r) => (r.url ? h("a", { href: r.url, target: "_blank", rel: "noreferrer", onclick: (e) => e.stopPropagation() }, r.host) : "–") },
  { key: "rssi", label: "WLAN", group: "Netzwerk", type: "num", cls: "num", def: true, get: (r) => r.rssi, render: (r) => rssiCell(r.rssi) },
  { key: "rssi_avg", label: "WLAN Ø 24 h", group: "Netzwerk", type: "num", cls: "num", get: (r) => r.rssi_avg, render: (r) => rssiCell(r.rssi_avg) },
  { key: "rssi_min", label: "WLAN min 24 h", group: "Netzwerk", type: "num", cls: "num", get: (r) => r.rssi_min, render: (r) => rssiCell(r.rssi_min) },
  { key: "ssid", label: "SSID", group: "Netzwerk", type: "enum", get: (r) => r.ssid },
  { key: "bssid", label: "Access Point (BSSID)", group: "Netzwerk", type: "enum", get: (r) => r.bssid },
  { key: "ap", label: "Geräte-AP", group: "Netzwerk", type: "bool", get: (r) => yesNo(r.ap), render: (r) => boolTag(r.ap, { good: false }) },
  { key: "sta1", label: "WLAN 2", group: "Netzwerk", type: "bool", get: (r) => yesNo(r.sta1), render: (r) => boolTag(r.sta1) },
  { key: "sources", label: "Gefunden über", group: "Netzwerk", type: "multi", get: (r) => r.sources },
  // UniFi
  { key: "unifi_name", label: "Name in UniFi", group: "UniFi", type: "text", get: (r) => r.unifi?.name ?? null,
    render: (r) => {
      const u = r.unifi;
      if (!u) return h("span", { class: "muted" }, "–");
      if (!u.found) return h("span", { class: "muted" }, "nicht bekannt");
      const same = u.name && r.shelly_name && u.name === r.shelly_name;
      return h("span", { class: "nmwrap" }, h("span", {}, u.name || h("span", { class: "muted" }, `(${u.hostname ?? "kein Alias"})`)),
        !same && r.shelly_name ? h("span", { class: "warnmark sev-info", title: `Shelly: ${r.shelly_name}` }, icon(MDI.info, 14)) : null);
    } },
  { key: "unifi_sync", label: "UniFi-Abgleich", group: "UniFi", type: "enum",
    get: (r) => (!r.unifi ? null : !r.unifi.found ? "nicht bekannt" : r.unifi.name === r.shelly_name ? "gleich" : "abweichend"),
    rank: (r) => (!r.unifi ? 0 : !r.unifi.found ? 1 : r.unifi.name === r.shelly_name ? 0 : 2) },
  { key: "unifi_fixed", label: "Feste IP", group: "UniFi", type: "bool", get: (r) => (r.unifi?.found ? yesNo(r.unifi.use_fixedip) : null),
    render: (r) => (r.unifi?.found ? (r.unifi.use_fixedip ? h("span", { class: "tag on" }, r.unifi.fixed_ip ?? "ja") : h("span", { class: "tag off" }, "dynamisch")) : h("span", { class: "muted" }, "–")) },
  { key: "unifi_ap", label: "Access Point", group: "UniFi", type: "enum", get: (r) => r.unifi?.ap ?? null },
  { key: "unifi_signal", label: "Signal (UniFi)", group: "UniFi", type: "num", cls: "num", get: (r) => r.unifi?.signal ?? null,
    render: (r) => (typeof r.unifi?.signal === "number" ? `${r.unifi.signal} dBm` : "–") },
  // Dienste
  { key: "cloud", label: "Cloud", group: "Dienste", type: "enum", def: true,
    get: (r) => (!r.cloud ? "aus" : r.cloud_connected === false ? "getrennt" : "verbunden"), render: (r) => connTag(r.cloud, r.cloud_connected) },
  { key: "mqtt", label: "MQTT", group: "Dienste", type: "enum",
    get: (r) => (!r.mqtt ? "aus" : r.mqtt_connected === false ? "getrennt" : "verbunden"), render: (r) => connTag(r.mqtt, r.mqtt_connected) },
  { key: "mqtt_server", label: "MQTT-Server", group: "Dienste", type: "enum", get: (r) => r.mqtt_server },
  { key: "ws", label: "Ausgeh. WS", group: "Dienste", type: "enum",
    get: (r) => (!r.ws ? "aus" : r.ws_connected === false ? "getrennt" : "verbunden"), render: (r) => connTag(r.ws, r.ws_connected) },
  { key: "ws_server", label: "WS-Server", group: "Dienste", type: "enum", get: (r) => r.ws_server },
  { key: "ble", label: "Bluetooth", group: "Dienste", type: "bool", def: true, get: (r) => yesNo(r.ble), render: (r) => boolTag(r.ble) },
  { key: "ble_rpc", label: "BLE-RPC", group: "Dienste", type: "bool", get: (r) => yesNo(r.ble_rpc), render: (r) => boolTag(r.ble_rpc) },
  { key: "ble_observer", label: "BLE-Gateway", group: "Dienste", type: "bool", get: (r) => yesNo(r.ble_observer), render: (r) => boolTag(r.ble_observer) },
  // Kanäle
  { key: "channels", label: "Kanäle", group: "Kanäle", type: "num", def: true, live: true,
    get: (r) => r.channel_count, render: (r, ctx) => channelDots(r, ctx.hass) },
  { key: "power", label: "Leistung", group: "Kanäle", type: "num", cls: "num", def: true, live: true,
    get: (r, ctx) => livePower(r, ctx.hass), render: (r, ctx) => fmtW(livePower(r, ctx.hass)) },
  { key: "consumption_types", label: "Gerätetyp (Drittsysteme)", group: "Kanäle", type: "multi", get: (r) => r.consumption_types },
  { key: "initial_states", label: "Nach Stromausfall", group: "Kanäle", type: "multi", get: (r) => r.initial_states },
  { key: "in_modes", label: "Eingangsmodus", group: "Kanäle", type: "multi", get: (r) => r.in_modes },
  // Home Assistant
  { key: "in_ha", label: "In HA", group: "Home Assistant", type: "bool", get: (r) => yesNo(r.in_ha), render: (r) => boolTag(r.in_ha) },
  { key: "ha_state", label: "Integration", group: "Home Assistant", type: "enum", get: (r) => r.ha_state },
  { key: "available", label: "Verfügbar", group: "Home Assistant", type: "bool", get: (r) => yesNo(r.available), render: (r) => boolTag(r.available) },
  { key: "helpers", label: "Helfer", group: "Home Assistant", type: "multi", get: (r) => r.helpers.map((x) => `als ${x.target_domain}`) },
  { key: "fallback", label: "Fallback", group: "Home Assistant", type: "bool", get: (r) => yesNo(r.fallback?.enabled ?? false),
    render: (r) => (r.fallback?.enabled ? h("span", { class: `tag ${r.fallback.error ? "bad" : "on"}`, title: r.fallback.error ?? "" },
      `${r.fallback.bindings} Belegung(en)${r.fallback.always ? " · dauerhaft" : ""}`) : "–") },
  { key: "sleeping", label: "Akku/schlafend", group: "Home Assistant", type: "bool", get: (r) => yesNo(r.sleeping), render: (r) => (r.sleeping ? icon(MDI.sleep, 16) : "") },
  // Zustand
  { key: "problems", label: "Probleme", group: "Zustand", type: "num", def: true, get: (r) => r.problems.length, render: problemCell,
    rank: (r) => worst(r.problems) * 100 + r.problems.length },
  { key: "problem_codes", label: "Problemart", group: "Zustand", type: "multi", get: (r) => r.problems.map((p) => p.text.replace(/\s*\(.*\)$|:.*$/, "")) },
  { key: "reboots_24h", label: "Neustarts 24 h", group: "Zustand", type: "num", cls: "num", get: (r) => r.reboots_24h ?? null,
    render: (r) => (r.reboots_24h ? h("span", { class: r.reboots_24h >= 3 ? "sev-warning" : "", title: r.last_reboot ? `Zuletzt: ${r.last_reboot.reason}` : "" }, String(r.reboots_24h)) : "–") },
  { key: "last_reboot", label: "Letzter Neustart", group: "Zustand", type: "num", get: (r) => r.last_reboot?.ts ?? null,
    render: (r) => (r.last_reboot ? h("span", { class: r.last_reboot.crash ? "sev-warning" : "", title: r.last_reboot.reason }, fmtAgo(r.last_reboot.ts)) : "–") },
  { key: "outages_24h", label: "Ausfälle 24 h", group: "Zustand", type: "num", cls: "num", get: (r) => r.outages_24h ?? null,
    render: (r) => (r.outages_24h ? String(r.outages_24h) : "–") },
  { key: "fetched_at", label: "Zuletzt abgefragt", group: "Zustand", type: "num", get: (r) => r.fetched_at, render: (r) => fmtAgo(r.fetched_at) },
];

export const COLUMN_BY_KEY = Object.fromEntries(COLUMNS.map((c) => [c.key, c]));
// Übersicht: ohne Shelly-/UniFi-Namen, dafür Netzwerk-Infos aus UniFi
export const DEFAULT_COLUMNS = ["st_other", "st_fw", "st_wifi", "area", "ha_name", "name_status", "model", "fw", "update",
  "host", "unifi_fixed", "unifi_ap", "unifi_signal", "cloud", "ble", "channels", "power", "problems"];

// Werte für Suche und Filter (Listen bei »multi«)
export function values(col, row, ctx) {
  const v = col.get(row, ctx);
  if (col.type === "multi") return Array.isArray(v) ? v : [];
  return v === null || v === undefined || v === "" ? [] : [v];
}

export function matchFilter(col, f, row, ctx) {
  const vals = values(col, row, ctx);
  if (col.type === "num") {
    const v = vals[0];
    if (typeof v !== "number") return f.min === undefined && f.max === undefined ? true : false;
    if (f.min !== undefined && f.min !== "" && v < Number(f.min)) return false;
    if (f.max !== undefined && f.max !== "" && v > Number(f.max)) return false;
    return true;
  }
  if (col.type === "text") {
    const q = (f.text ?? "").toLowerCase();
    return !q || vals.some((v) => String(v).toLowerCase().includes(q));
  }
  // enum, bool, multi: Auswahl; »(leer)« steht für fehlende Werte
  const sel = f.values ?? [];
  if (!sel.length) return true;
  if (!vals.length) return sel.includes("(leer)");
  return vals.some((v) => sel.includes(String(v)));
}

export function filterLabel(col, f) {
  if (col.type === "num") {
    const a = f.min !== undefined && f.min !== "" ? `≥ ${f.min}` : "";
    const b = f.max !== undefined && f.max !== "" ? `≤ ${f.max}` : "";
    return `${col.label} ${[a, b].filter(Boolean).join(" ")}`;
  }
  if (col.type === "text") return `${col.label} enthält »${f.text}«`;
  const v = f.values ?? [];
  return `${col.label}: ${v.length > 2 ? `${v.slice(0, 2).join(", ")} +${v.length - 2}` : v.join(", ")}`;
}

export function isActive(col, f) {
  if (!f) return false;
  if (col.type === "num") return (f.min ?? "") !== "" || (f.max ?? "") !== "";
  if (col.type === "text") return !!f.text;
  return (f.values ?? []).length > 0;
}

export function compare(col, a, b, ctx) {
  const va = values(col, a, ctx)[0], vb = values(col, b, ctx)[0];
  if (va === undefined && vb === undefined) return 0;
  if (va === undefined) return 1;
  if (vb === undefined) return -1;
  if (typeof va === "number" && typeof vb === "number") return va - vb;
  return String(va).localeCompare(String(vb), "de", { numeric: true, sensitivity: "base" });
}

// Spaltenpresets für typische Aufgaben
export const PRESETS = [
  { key: "overview", label: "Übersicht", columns: DEFAULT_COLUMNS, sort: { key: "problems", dir: -1 }, filters: {} },
  { key: "names", label: "Gerätenamen abgleichen", sort: { key: "name", dir: 1 }, filters: {},
    columns: ["st_other", "area", "shelly_name", "ha_name", "device_name_status", "channel_name_status", "rule_issues", "model", "problems"] },
  { key: "rules", label: "Namensregeln prüfen", sort: { key: "name", dir: 1 }, filters: { rule_issues: { min: "1" } },
    columns: ["st_other", "area", "shelly_name", "rule_issues", "model", "channel_names"] },
  { key: "channels", label: "Kanalnamen prüfen", sort: { key: "name", dir: 1 }, expandAll: true,
    filters: { channel_name_status: { values: ["Abweichend", "Regel verletzt", "Hinweis", "Gleich", "Unbekannt"] } },
    columns: ["st_other", "area", "shelly_name", "ha_name", "channels", "channel_name_status", "channel_names", "model"] },
  { key: "wifi", label: "WLAN & Signalstärke", sort: { key: "st_wifi", dir: -1 }, filters: {},
    columns: ["st_wifi", "area", "model", "host", "rssi", "rssi_avg", "rssi_min", "ssid", "bssid", "uptime", "outages_24h"] },
  { key: "services", label: "Dienste & Verbindungen", sort: { key: "st_other", dir: -1 }, filters: {},
    columns: ["st_other", "area", "model", "cloud", "mqtt", "mqtt_server", "ws", "ws_server", "ble", "ble_rpc", "ble_observer", "eco"] },
  { key: "firmware", label: "Firmware & Updates", sort: { key: "st_fw", dir: -1 }, filters: {},
    columns: ["st_fw", "area", "model", "model_id", "gen", "fw", "update", "beta", "uptime", "restart_required"] },
  { key: "unifi", label: "UniFi", sort: { key: "unifi_sync", dir: -1 }, filters: {},
    columns: ["st_other", "st_wifi", "area", "shelly_name", "unifi_name", "unifi_sync", "unifi_fixed", "host", "unifi_ap", "rssi_avg"] },
  { key: "behavior", label: "Schaltverhalten", sort: { key: "name", dir: 1 }, filters: {},
    columns: ["st_other", "area", "channels", "power", "initial_states", "in_modes", "consumption_types", "helpers"] },
  { key: "health", label: "Zustand & Probleme", sort: { key: "problems", dir: -1 }, filters: {},
    columns: ["st_other", "st_fw", "st_wifi", "area", "model", "problems", "problem_codes", "available", "reboots_24h", "last_reboot", "outages_24h", "rssi_avg"] },
];
