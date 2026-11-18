// Seitenpanel: ein Gerät mit Links, Problemen, Namensabgleich, Kanälen und Rohdaten
import { NAME_STATUS, haName, hintText, isInfoHint, livePower, shellyName } from "./columns.js";
import { channelSettingsDialog } from "./batch.js";
import { fixFor } from "./fixes.js";
import { entityDialog, helperDialog, inputDisableDialog, inputNameDialog, namesDialog, renameItemDialog } from "./dialogs.js";
import { MDI, SEVERITY, fireMoreInfo, fmtAgo, fmtDuration, fmtW, h, icon, navigate, num, spinner, svg } from "./util.js";


export class Detail {
  constructor(panel) {
    this.p = panel;
    this.el = h("aside", { class: "drawer" });
    this.mac = null;
    this.raw = null;
  }

  open(mac) {
    this.mac = mac;
    this.raw = null;
    this.el.classList.add("open");
    this.render();
  }

  close() {
    this.mac = null;
    this.el.classList.remove("open");
    this.el.replaceChildren();
    this.p.table?.markOpen(null);
  }

  get row() {
    return this.p.byMac.get(this.mac);
  }

  render() {
    const r = this.row;
    if (!r) return this.close();
    const hass = this.p.hass;
    const busy = this.p.busy[r.mac];
    this.el.replaceChildren(
      h("div", { class: "dtools" },
        h("button", { class: "icon-btn", "aria-label": "Aktionen", title: "Aktionen", onclick: (e) => this.p.openMenu(e, this.p.deviceActions(r)) }, icon(MDI.dots)),
        h("button", { class: "icon-btn", "aria-label": "Schließen", onclick: () => this.close() }, icon(MDI.close))),
      h("div", { class: "dbody" },
        h("div", {},
          h("div", { class: "dtitle" }, r.name),
          h("div", { class: "muted small" }, [r.model, r.host, r.mac].filter(Boolean).join(" · ")),
          busy ? h("div", { class: "row small", style: { marginTop: "6px" } }, spinner(14), "wird abgefragt …") : null),
        this.links(r),
        r.problems.length ? this.section("Probleme", h("div", { class: "plist" }, r.problems.map((p) => {
          const s = SEVERITY[p.severity] ?? SEVERITY.info;
          // Gleiche Reparatur wie im Probleme-Tab, nur für dieses Gerät; spätere Namens-Stufen warten
          const fix = p.blocked ? null : (p.code === "switch_as_light"
            ? { label: "Umstellen …", run: () => helperDialog(this.p, p.entity_id) } : fixFor(p.code));
          return h("div", { class: "pitem" }, h("span", { class: s.cls }, icon(s.icon, 16)),
            h("span", { class: `grow${p.blocked ? " muted" : ""}` }, p.text),
            p.blocked ? h("span", { class: "muted small", title: "Erst die frühere Namens-Stufe erledigen" }, `wartet auf Stufe ${r.name_stage}`)
              : fix ? h("button", { class: "mini", onclick: (e) => fix.run(this.p, [{ r, p }], e) }, fix.label) : null);
        }))) : null,
        this.section("Namen", this.names(r), () => this.namesMenu(r)),
        r.channels.length ? this.section("Kanäle", this.channels(r, hass)) : null,
        this.section("Gerät", this.kv([
          ["Modell", `${r.model ?? "–"}${r.model_id && r.model_id !== r.model ? ` (${r.model_id})` : ""}`],
          ["Firmware", r.fw ?? "–"],
          ["Update", r.update ?? (r.beta ? `nur Beta ${r.beta}` : "aktuell")],
          ["Laufzeit", fmtDuration(r.uptime)],
          ["Leistung", fmtW(livePower(r, hass))],
          ["Eco-Modus", fmt(r.eco)],
          ["Passwort", fmt(r.auth)],
          ["Zeitzone", r.tz ?? "–"],
          ["Akku/schlafend", r.sleeping ? "ja" : "nein"],
        ])),
        this.section("Netzwerk & Dienste", this.kv([
          ["IP", r.host ?? "–"],
          ["WLAN", r.rssi !== null && r.rssi !== undefined ? `${r.ssid ?? ""} · ${r.rssi} dBm` : "–"],
          ["Geräte-AP", fmt(r.ap)],
          ["Cloud", conn(r.cloud, r.cloud_connected)],
          ["MQTT", conn(r.mqtt, r.mqtt_connected) + (r.mqtt_server ? ` · ${r.mqtt_server}` : "")],
          ["Ausgehender WS", conn(r.ws, r.ws_connected) + (r.ws_server ? ` · ${r.ws_server}` : "")],
          ["Bluetooth", `${fmt(r.ble)} · RPC ${fmt(r.ble_rpc)} · Gateway ${fmt(r.ble_observer)}`],
          ["Gefunden über", r.sources.join(", ") || "–"],
          ["Zuletzt abgefragt", r.fetched_at ? fmtAgo(r.fetched_at) : (r.live ? "live aus HA" : "–")],
          ...(r.fetch_error ? [["Abfragefehler", r.fetch_error]] : []),
        ])),
        this.historySection(r),
        this.rawSection(r),
      ),
    );
  }

  section(title, body, menu = null) {
    return h("div", { class: "sec" }, h("h4", { class: "sechead" }, h("span", { class: "grow" }, title),
      menu ? h("button", { class: "icon-btn sm", "aria-label": `${title}: Aktionen`, onclick: (e) => this.p.openMenu(e, menu()) }, icon(MDI.dots, 16)) : null), body);
  }

  kv(pairs) {
    return h("div", { class: "kv" }, pairs.flatMap(([k, v]) => [h("span", {}, k), h("span", {}, v ?? "–")]));
  }

  links(r) {
    return h("div", { class: "links" },
      r.url ? h("a", { class: "btn", href: r.url, target: "_blank", rel: "noreferrer" }, icon(MDI.open, 16), "Webinterface") : null,
      r.ha_device_id ? h("button", { class: "btn", onclick: () => navigate(`/config/devices/device/${r.ha_device_id}`) }, icon(MDI.ha, 16), "Gerät in HA") : null,
      !r.in_ha && r.sources.some((s) => s.startsWith("ha_"))
        ? h("button", { class: "btn", onclick: () => navigate("/config/integrations/dashboard") }, icon(MDI.ha, 16), "In HA einrichten")
        : null,
    );
  }

  // Aktionen für einen Namen (Gerät, Kanal oder Eingang)
  nameActions(r, it) {
    const p = this.p;
    const one = (direction, title) => namesDialog(p, [{ mac: r.mac, direction, keys: [it.key] }], title);
    const items = [h("div", { class: "menu-title" }, it.key === "device" ? "Gerätename" : `${it.kind === "input" ? "Eingang" : "Kanal"} ${it.key}`)];
    if (it.shelly !== null && !it.hints.includes("single_channel_named")) items.push(p.item("Umbenennen …", MDI.pencil ?? MDI.tag, () => renameItemDialog(p, r, it)));
    if (it.unnamed) {
      items.push(p.item("Namen setzen …", MDI.tag, () => inputNameDialog(p, r, it)), p.item("Eingang deaktivieren …", MDI.close, () => inputDisableDialog(p, r, it)));
      return items;
    }
    if (it.coupled && !(it.shelly ?? "").trim()) {
      // Gekoppelter Taster: benennen möglich, deaktivieren nicht (würde den Ausgang vom Taster trennen)
      items.push(p.item("Namen setzen …", MDI.tag, () => inputNameDialog(p, r, it), { sub: `schaltet ${it.coupled} direkt` }));
      return items;
    }
    if (it.shelly !== null && (it.ha_full !== null || it.hints.includes("single_channel_named")))
      items.push(p.item("HA-Name → Shelly", MDI.chip, () => one("to_shelly", "HA-Name → Shelly"),
        { sub: it.hints.includes("single_channel_named") ? "Kanalname leeren" : `»${it.to_shelly}«` }));
    if (it.shelly !== null && (it.ha_device_id || it.ha_entity_id) && it.to_ha?.name)
      items.push(p.item("Shelly-Name → HA", MDI.ha, () => one("to_ha", "Shelly-Name → HA"), { sub: `»${it.to_ha.name}« in ${it.to_ha.area ?? "–"}` }));
    return items;
  }

  names(r) {
    // Pro Gerät/Kanal ein Block: Shelly, Home Assistant, (Gerät:) Integration und UniFi untereinander
    const n = r.names;
    const items = [n.device, ...n.channels];
    const line = (label, value, cls = "") => h("div", { class: `nline ${cls}` }, h("span", { class: "nlab" }, label), h("span", { class: "nval" }, value));
    return h("div", { class: "nblocks" }, items.map((it) => {
      const s = NAME_STATUS[it.status] ?? NAME_STATUS.unknown;
      const isDev = it.key === "device";
      const u = isDev ? r.unifi : null;
      const want = (it.shelly ?? "").trim();
      const head = h("div", { class: "nhead" },
        h("span", { class: s.cls, title: s.label }, icon(s.icon, 16)),
        h("span", { class: "grow nkey" }, isDev ? "Gerät" : `${it.kind === "input" ? "Eingang" : "Kanal"} ${it.key}`,
          it.coupled ? h("span", { class: "muted small" }, ` · gekoppelt an ${it.coupled}${it.in_mode ? ` (${it.in_mode})` : ""}`) : null),
        h("button", { class: "icon-btn sm", "aria-label": "Aktionen", onclick: (e) => this.p.openMenu(e, this.nameActions(r, it)) }, icon(MDI.dots, 16)));
      return h("div", { class: "nblock" }, head,
        line("Shelly", shellyName(it.shelly, { item: it, isDevice: isDev })),
        line("HA", haName(it.ha_area, it.ha_name, { item: it, isDevice: isDev })),
        isDev && r.entry_title ? line("Integration", h("span", { class: "nmwrap" }, r.entry_title,
          want && r.entry_title !== want ? h("span", { class: "warnmark sev-info", title: "Titel weicht vom Gerätenamen ab" }, icon(MDI.info, 14)) : null)) : null,
        isDev && u ? line("UniFi", !u.found ? h("span", { class: "muted" }, "nicht bekannt") : h("span", { class: "nmwrap" },
          u.name || h("span", { class: "muted" }, `(kein Alias – ${u.hostname ?? "?"})`),
          want && u.name !== want ? h("span", { class: "warnmark sev-info", title: "Alias weicht vom Gerätenamen ab" }, icon(MDI.info, 14)) : null)) : null,
        it.hints.map((hint) => h("div", { class: `nhint ${isInfoHint(hint) ? "sev-info" : "sev-warning"}` }, hintText(hint, isDev))));
    }));
  }

  namesMenu(r) {
    const p = this.p;
    const all = (direction, title) => namesDialog(p, [{ mac: r.mac, direction }], title);
    return [
      h("div", { class: "menu-title" }, "Alle Namen dieses Geräts"),
      p.item("Alle HA-Namen → Shelly", MDI.chip, () => all("to_shelly", "HA-Namen → Shelly")),
      p.item("Alle Shelly-Namen → HA", MDI.ha, () => all("to_ha", "Shelly-Namen → HA")),
      r.entity_ids.length ? p.item("Entity-IDs prüfen", MDI.tag, () => entityDialog(p, [r.mac])) : null,
    ];
  }

  channels(r, hass) {
    const multi = r.channels.filter((c) => ["switch", "light", "rgb", "rgbw", "cct", "cover"].includes(c.component)).length > 1;
    return h("div", { class: "chlist" }, r.channels.map((c) => {
      const st = c.entity ? hass.states[c.entity] : null;
      const p = num(hass, c.power_entity) ?? c.apower;
      const name = c.names?.ha_full ?? c.names?.shelly ?? c.name ?? (r.channels.length === 1 ? r.name : c.key);
      const menu = () => [
        h("div", { class: "menu-title" }, `${c.key} – ${name}`),
        ["switch", "light", "rgb", "rgbw", "cct"].includes(c.component) ? this.p.item("Einstellungen …", MDI.cog, () => channelSettingsDialog(this.p, r, c),
          { sub: "Stromausfall, Eingang, Gerätetyp, Auto-Aus" }) : null,
        multi && c.names ? this.p.item("Umbenennen …", MDI.tag, () => renameItemDialog(this.p, r, c.names)) : null,
        c.entity ? this.p.item("In HA öffnen", MDI.ha, () => fireMoreInfo(this.el, c.entity)) : null,
      ];
      return h("div", { class: "chitem" },
        h("span", { class: `dot${st?.state === "on" || (!st && c.output) ? " on" : ""}` }),
        h("span", { class: "grow", style: { cursor: c.entity ? "pointer" : "default" }, onclick: () => c.entity && fireMoreInfo(this.el, c.entity) },
          h("div", {}, name),
          h("div", { class: "muted small" }, [c.key, c.consumption_type && `Typ ${c.consumption_type}`,
            c.initial_state && `nach Stromausfall: ${c.initial_state}`, c.in_mode && `Eingang: ${c.in_mode}`,
            c.auto_off && `Auto-Aus ${c.auto_off_delay}s`].filter(Boolean).join(" · "))),
        h("span", { class: "muted" }, typeof p === "number" ? fmtW(p) : ""),
        h("button", { class: "icon-btn sm", "aria-label": "Kanal-Aktionen", onclick: (e) => this.p.openMenu(e, menu()) }, icon(MDI.dots, 16)));
    }));
  }

  historySection(r) {
    const box = h("div", { class: "hist" }, h("p", { class: "muted small" }, "Lade Verlauf …"));
    // Detail wird bei jedem HA-Update neu gezeichnet – Verlauf nur einmal pro Minute holen
    const c = this._hist;
    const fresh = c && c.mac === r.mac && Date.now() - c.at < 60000;
    const load = fresh ? Promise.resolve(c.data) : this.p.hass.callWS({ type: "shelly_manager/history", mac: r.mac })
      .then((data) => { this._hist = { mac: r.mac, at: Date.now(), data }; return data; });
    load.then((hs) => {
      if (!hs.fine) {
        box.replaceChildren(h("p", { class: "muted small" }, "Noch keine Messwerte – der Manager zeichnet alle 5 Minuten auf."));
        return;
      }
      const reb = (hs.reboots ?? []).slice(-5).reverse();
      const out = (hs.outages ?? []).slice(-5).reverse();
      box.replaceChildren(
        h("div", { class: "small muted" }, "WLAN-Signal 24 h"), sparkFine(hs.fine),
        h("div", { class: "small muted" }, "WLAN-Signal 14 Tage (min / Ø / max je Stunde)"), sparkCoarse(hs.coarse),
        h("div", { class: "kv", style: { marginTop: "8px" } },
          h("span", {}, "Neustarts"), h("span", {}, reb.length ? h("div", {}, reb.map((x) => h("div", { class: x.crash ? "sev-warning" : "" },
            `${new Date(x.ts * 1000).toLocaleString("de-AT")} – ${x.reason}`))) : "keine erfasst"),
          h("span", {}, "Ausfälle"), h("span", {}, out.length ? h("div", {}, out.map((o) => h("div", {},
            `${new Date(o.start * 1000).toLocaleString("de-AT")} – ${o.end ? `${Math.round((o.end - o.start) / 60)} min` : "dauert an"}`))) : "keine erfasst")));
    }).catch(() => box.replaceChildren(h("p", { class: "muted small" }, "Verlauf nicht verfügbar")));
    return this.section("Verlauf", box);
  }

  rawSection(r) {
    const det = h("details", {}, h("summary", {}, "Rohdaten (Info, Konfiguration, Status)"));
    det.addEventListener("toggle", async () => {
      if (!det.open || det.dataset.loaded) return;
      det.dataset.loaded = "1";
      try {
        const res = await this.p.hass.callWS({ type: "shelly_manager/device", mac: r.mac });
        for (const k of ["info", "config", "status"]) {
          det.append(h("div", { class: "muted small" }, k), h("pre", { class: "raw" }, JSON.stringify(res.raw[k], null, 2)));
        }
      } catch (err) {
        det.append(h("p", { class: "sev-error" }, String(err.message ?? err)));
      }
    });
    return det;
  }
}

function fmt(v) {
  return v === true ? "an" : v === false ? "aus" : "–";
}

function conn(enabled, connected) {
  if (!enabled) return enabled === false ? "aus" : "–";
  return connected === false ? "an, getrennt" : "an, verbunden";
}


// ── Sparklines (SVG, dBm: −30 oben … −95 unten) ─────────────────────────────
const W = 400, H = 70, TOP = -30, BOTTOM = -95;
const y = (v) => Math.round(((TOP - v) / (TOP - BOTTOM)) * H * 10) / 10;

function frame(children, labels = true) {
  const lines = [-67, -75, -82].map((v) => svg("line", { x1: 0, x2: W, y1: y(v), y2: y(v), class: `gl gl${-v}` }));
  const text = labels ? [-67, -82].map((v) => svg("text", { x: W - 2, y: y(v) - 2, "text-anchor": "end", class: "glt" }, `${v}`)) : [];
  return svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "spark", preserveAspectRatio: "none" }, ...lines, ...children, ...text);
}

function sparkFine(s) {
  const n = s.v.length;
  if (!n) return h("p", { class: "muted small" }, "–");
  let d = "", pen = false;
  s.v.forEach((v, i) => {
    if (v === null) { pen = false; return; }
    const x = (i / Math.max(1, n - 1)) * W;
    d += `${pen ? "L" : "M"}${x.toFixed(1)},${y(v)} `;
    pen = true;
  });
  return frame([svg("path", { d, class: "sl" })]);
}

function sparkCoarse(s) {
  const n = s.v.length;
  if (!n) return h("p", { class: "muted small" }, "–");
  const bars = [], avg = [];
  s.v.forEach((v, i) => {
    if (!v) return;
    const x = (i / Math.max(1, n - 1)) * W;
    bars.push(svg("line", { x1: x, x2: x, y1: y(v[2]), y2: y(v[0]), class: "sb" }));
    avg.push(`${avg.length ? "L" : "M"}${x.toFixed(1)},${y(v[1])}`);
  });
  return frame([...bars, svg("path", { d: avg.join(" "), class: "sl" })]);
}
