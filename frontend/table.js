// Gerätetabelle: feste Spalten (Auswahl, Zustand, Name), wählbare Spalten, Kanalzeilen zum Aufklappen
import { COLUMN_BY_KEY, NAME_STATUS, haName, hintText, shellyName } from "./columns.js";
import { MDI, SEVERITY, fmtW, h, icon, num, problemIcon, spinner } from "./util.js";

export class Table {
  constructor(panel) {
    this.p = panel;
    this.el = h("div", { class: "view" });
    this.live = [];      // [{el, row, col}] – bei HA-Updates neu zeichnen
    this.stateCells = new Map(); // mac -> td
    this.rowEls = new Map();     // mac -> tr
    this.lastClicked = null;
  }

  render(rows) {
    const p = this.p;
    const cols = p.view.columns.map((k) => COLUMN_BY_KEY[k]).filter(Boolean);
    const ctx = p.ctx;
    this.live = [];
    this.stateCells.clear();
    this.rowEls.clear();
    this.visible = rows;

    const allSel = rows.length > 0 && rows.every((r) => p.selected.has(r.mac));
    const someSel = rows.some((r) => p.selected.has(r.mac));
    const selAll = h("input", { type: "checkbox", "aria-label": "Alle auswählen", checked: allSel,
      onclick: (e) => { e.stopPropagation(); p.selectRows(rows.map((r) => r.mac), !allSel); } });
    selAll.indeterminate = someSel && !allSel;

    const head = h("tr", {},
      h("th", { class: "fix c-sel" }, selAll),
      h("th", { class: "fix c-state", title: "Abfrage läuft / schlafendes Gerät" }, ""),
      this.th({ key: "name", label: "Name", type: "text" }, "fix c-name"),
      cols.map((c) => this.th(c)),
    );
    const body = h("tbody");
    for (const r of rows) {
      body.append(this.devRow(r, cols, ctx));
      if (p.expanded.has(r.mac)) body.append(this.channelRow(r, cols.length + 3));
    }
    const table = h("table", {}, h("thead", {}, head), body);
    const scroll = this.el.scrollTop, scrollX = this.el.scrollLeft;
    this.el.replaceChildren(rows.length ? table : h("p", { class: "empty" }, p.rows.length ? "Keine Geräte passen zu Suche und Filtern." : "Keine Geräte gefunden."));
    this.el.scrollTop = scroll;
    this.el.scrollLeft = scrollX;
  }

  th(col, extra = "") {
    const p = this.p;
    const sorted = p.view.sort?.key === col.key;
    const filtered = !!p.activeFilter(col.key);
    return h("th", { class: `${extra}${col.cls === "c-ico" ? " c-ico" : ""}${sorted ? " sorted" : ""}${filtered ? " filtered" : ""}`,
      title: col.head ? col.label : null },
      h("div", { class: "th", onclick: (e) => p.columnMenu(e, col.key) },
        col.head ?? col.label,
        sorted ? icon(p.view.sort.dir > 0 ? MDI.sortAsc : MDI.sortDesc, 16) : null));
  }

  devRow(r, cols, ctx) {
    const p = this.p;
    const sel = p.selected.has(r.mac);
    const multi = r.channels.length > 1 || r.names.channels.some((c) => c.kind === "input");
    const tr = h("tr", { class: `dev${sel ? " sel" : ""}${p.detail.mac === r.mac ? " open" : ""}`,
      onclick: (e) => { if (!e.target.closest("a,button,input")) p.openDetail(r.mac); },
      oncontextmenu: (e) => p.rowMenu(e, r.mac) });
    const cb = h("input", { type: "checkbox", checked: sel, "aria-label": "Auswählen",
      onclick: (e) => { e.stopPropagation(); this.toggle(r.mac, e.shiftKey); } });
    const state = h("td", { class: "fix c-state" }, this.stateIcon(r));
    this.stateCells.set(r.mac, state);
    const exp = h("button", { class: `exp${multi ? "" : " none"}`, "aria-label": "Kanäle",
      onclick: (e) => { e.stopPropagation(); p.toggleExpand(r.mac); } },
    icon(p.expanded.has(r.mac) ? MDI.collapse : MDI.expand, 18));
    tr.append(
      h("td", { class: "fix c-sel" }, cb),
      state,
      h("td", { class: "fix c-name" }, h("div", { class: "namecell" }, exp,
        h("div", { class: "txt" }, h("span", { class: "nm", title: r.name }, r.name),
          h("span", { class: "sub" }, [r.in_ha ? null : "nicht in HA", r.gen === 1 ? "Gen1" : null].filter(Boolean).join(" · "))))),
    );
    for (const c of cols) {
      const td = h("td", { class: c.cls ?? "" });
      td.append(this.cell(c, r, ctx));
      if (c.live) this.live.push({ el: td, row: r, col: c });
      tr.append(td);
    }
    this.rowEls.set(r.mac, tr);
    return tr;
  }

  cell(c, r, ctx) {
    if (c.render) {
      const v = c.render(r, ctx);
      return v instanceof Node ? v : document.createTextNode(v ?? "–");
    }
    const v = c.get(r, ctx);
    if (Array.isArray(v)) return document.createTextNode(v.length ? v.join(", ") : "–");
    return document.createTextNode(v === null || v === undefined || v === "" ? "–" : String(v));
  }

  channelRow(r, span) {
    const hass = this.p.hass;
    const grid = h("div", { class: "chgrid" },
      ["Kanal", "Name im Shelly", "Name in HA", "", "Zustand", "Leistung", "Nach Stromausfall", "Eingang", "Typ"].map((t) => h("span", { class: "hd" }, t)));
    for (const c of r.channels) {
      const n = c.names;
      const s = n ? NAME_STATUS[n.status] ?? NAME_STATUS.unknown : null;
      const st = c.entity ? hass.states[c.entity] : null;
      const powerCell = h("span", { class: "num" }, fmtW(num(hass, c.power_entity) ?? c.apower ?? null));
      const stateCell = h("span", {}, st ? st.state : c.output === true ? "on" : c.output === false ? "off" : "–");
      this.live.push({ el: powerCell, row: r, col: { render: () => fmtW(num(this.p.hass, c.power_entity) ?? c.apower ?? null) } });
      this.live.push({ el: stateCell, row: r, col: { render: () => { const s2 = c.entity ? this.p.hass.states[c.entity] : null; return s2 ? s2.state : "–"; } } });
      grid.append(
        h("span", { class: "muted" }, c.key),
        n ? shellyName(n.shelly, { item: n, isDevice: false }) : h("span", {}, c.name ?? "–"),
        n ? haName(n.ha_area, n.ha_name, { item: n, isDevice: false }) : h("span", { class: "muted" }, "–"),
        s ? h("span", { class: s.cls, title: [s.label, ...(n.hints ?? []).map((x) => hintText(x, false))].join("\n") }, icon(s.icon, 15)) : h("span"),
        stateCell, powerCell,
        h("span", {}, c.initial_state ?? "–"),
        h("span", {}, c.in_mode ?? "–"),
        h("span", {}, c.consumption_type ?? "–"),
      );
    }
    // Eingänge: eigenständig (i4 & Co.) oder an einen Ausgang gekoppelt – Namen und letzter Zustand
    for (const n of r.names.channels.filter((x) => x.kind === "input")) {
      const s = NAME_STATUS[n.status] ?? NAME_STATUS.unknown;
      const st = hass.states[n.ha_entity_id];
      grid.append(
        h("span", { class: "muted" }, n.key),
        shellyName(n.shelly, { item: n, isDevice: false }),
        haName(n.ha_area, n.ha_name_by_user || n.ha_name, { item: n, isDevice: false }),
        h("span", { class: s.cls, title: [s.label, ...n.hints.map((x) => hintText(x, false))].join("\n") }, icon(s.icon, 15)),
        h("span", { class: "muted" }, st ? (st.attributes?.event_type ?? st.state) : "–"),
        h("span"), h("span", { class: "muted" }, n.coupled ? `Eingang → ${n.coupled}` : "Eingang"),
        h("span", { class: "muted" }, n.coupled ? (n.in_mode ?? "") : "eigenständig"), h("span"),
      );
    }
    return h("tr", { class: "chrow" }, h("td", { colspan: span }, grid));
  }

  stateIcon(r) {
    const b = this.p.busy[r.mac];
    if (b === "running") return h("span", { title: "Wird abgefragt" }, spinner(16));
    if (b === "queued") return h("span", { class: "muted", title: "Wartet auf Abfrage" }, icon(MDI.clock, 16));
    if (r.sleeping) return h("span", { class: "muted", title: "Schlafendes Akku-Gerät" }, icon(MDI.sleep, 15));
    return "";  // Probleme stehen in den Symbolspalten WLAN / Firmware / Sonstige
  }

  toggle(mac, range) {
    const p = this.p;
    if (range && this.lastClicked && this.visible) {
      const macs = this.visible.map((r) => r.mac);
      const a = macs.indexOf(this.lastClicked), b = macs.indexOf(mac);
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a < b ? [a, b] : [b, a];
        p.selectRows(macs.slice(lo, hi + 1), !p.selected.has(mac));
        this.lastClicked = mac;
        return;
      }
    }
    this.lastClicked = mac;
    p.selectRows([mac], !p.selected.has(mac));
  }

  updateBusy() {
    for (const [mac, td] of this.stateCells) {
      const r = this.p.byMac.get(mac);
      if (r) td.replaceChildren(this.stateIcon(r));
    }
  }

  updateLive() {
    const ctx = this.p.ctx;
    for (const { el, row, col } of this.live) {
      const v = col.render(row, ctx);
      el.replaceChildren(v instanceof Node ? v : document.createTextNode(v ?? "–"));
    }
  }

  markOpen(mac) {
    for (const [m, tr] of this.rowEls) tr.classList.toggle("open", m === mac);
  }
}

