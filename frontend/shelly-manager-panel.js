// Shelly-Manager-Panel: Geräte (Tabelle) und Probleme
import { COLUMNS, COLUMN_BY_KEY, DEFAULT_COLUMNS, GROUPS, PRESETS, compare, filterLabel, isActive, matchFilter, values } from "./columns.js";
import { Detail } from "./detail.js";
import { batchDialog, jobsDialog } from "./batch.js";
import { firmwareDialog } from "./firmware.js";
import { fixFor } from "./fixes.js";
import { wizardDialog } from "./wizard.js";
import { unifiDialog } from "./unifi.js";
import { fallbackDialog } from "./fallback.js";
import { presetsView } from "./presets.js";
import { resetDialog } from "./reset.js";
import { orphansDialog, refsManualDialog } from "./refs.js";
import { modal, rebootDialog, renameItemDialog } from "./dialogs.js";
import { entityDialog, namesDialog } from "./dialogs.js";
import { STYLE } from "./style.js";
import { Table } from "./table.js";
import { MDI, SEVERITY, debounce, h, icon, navigate, spinner, storage, store } from "./util.js";

const VIEW_KEY = "shelly_manager.view";

// Gespeicherte Ansichten von vor den Symbolspalten: passende Symbolspalten des Presets einmalig voranstellen
function migrateIconCols(saved) {
  if (!saved.columns) return DEFAULT_COLUMNS;
  // Neue Übersicht einmalig übernehmen, solange die Ansicht noch auf »Übersicht« steht
  if ((saved.preset ?? "overview") === "overview" && !saved.ov2) return DEFAULT_COLUMNS;
  if (saved.iconCols) return saved.columns;
  const preset = PRESETS.find((p) => p.key === (saved.preset ?? "overview"));
  const icons = (preset?.columns ?? DEFAULT_COLUMNS).filter((k) => k.startsWith("st_"));
  return [...icons, ...saved.columns.filter((k) => !k.startsWith("st_"))];
}
const TABS = [["devices", "Geräte"], ["problems", "Probleme"], ["presets", "Vorgaben"]];
const SEARCH_FIELDS = ["name", "shelly_name", "ha_name", "area", "model", "model_id", "host", "mac", "fw", "ssid"];

class ShellyManagerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.rows = [];
    this.byMac = new Map();
    this.busy = {};
    this.scan = { running: false };
    this.selected = new Set();
    this.expanded = new Set();
    this.query = "";
    this.tab = "devices";
    const saved = storage(VIEW_KEY, {});
    this.view = {
      columns: migrateIconCols(saved).filter((k) => COLUMN_BY_KEY[k]),
      sort: saved.sort ?? null,
      filters: saved.filters ?? {},
      refreshMode: saved.refreshMode ?? "status",
      preset: saved.preset ?? "overview",
    };
    this.liveTick = debounce(() => { this.table?.updateLive(); if (this.detail?.mac) this.detail.render(); }, 1000);
    this.reload = debounce(() => this.load(), 500);
    this.renderSoon = debounce(() => this.renderView(), 150);
    this.jobListeners = new Set();
    this.idleListeners = new Set();
    this.opListeners = new Map();
  }

  connectedCallback() {
    this._fitHeight = () => {
      const vh = window.visualViewport?.height ?? window.innerHeight;
      const top = Math.max(0, this.getBoundingClientRect().top);
      this.style.height = `${Math.max(240, Math.floor(vh - top))}px`;
    };
    this._fitHeight();
    requestAnimationFrame(this._fitHeight);
    window.addEventListener("resize", this._fitHeight);
    window.visualViewport?.addEventListener("resize", this._fitHeight);
    if (this._hass && !this._unsub) this.subscribe();
  }

  disconnectedCallback() {
    window.removeEventListener("resize", this._fitHeight);
    window.visualViewport?.removeEventListener("resize", this._fitHeight);
    this._unsub?.then((u) => u()).catch(() => {});
    this._unsub = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this.renderShell();
      this.load();
      this.subscribe();
    } else this.liveTick();
  }

  get hass() {
    return this._hass;
  }

  set narrow(v) {
    this.toggleAttribute("narrow", !!v);
  }

  set panel(_v) {}

  get ctx() {
    return { hass: this._hass, panel: this };
  }

  // ── Daten ─────────────────────────────────────────────────────────────────
  async load() {
    try {
      const res = await this.hass.callWS({ type: "shelly_manager/get" });
      this.setRows(res.rows);
      this.areas = res.areas;
      this.ble = res.ble ?? [];
      this.newDevices = res.new_devices ?? {};
      this.options = res.options;
      this.busy = res.busy ?? {};
      this.scan = res.scan ?? { running: false };
      this.renderTabs();
      this.renderView();
      this.renderBanner();
      // Seitenpanel mit dem neuen Stand neu zeichnen (z. B. nach UniFi-Abgleich oder Umbenennen)
      if (this.detail?.mac) this.detail.render();
    } catch (err) {
      this.main.replaceChildren(h("p", { class: "empty" }, `Shelly Manager konnte nicht geladen werden: ${err.message ?? err}`));
    }
  }

  setRows(rows) {
    this.rows = rows;
    this.byMac = new Map(rows.map((r) => [r.mac, r]));
    for (const mac of [...this.selected]) if (!this.byMac.has(mac)) this.selected.delete(mac);
  }

  subscribe() {
    if (this._unsub || !this._hass) return;
    this._unsub = this.hass.connection.subscribeMessage((ev) => this.onEvent(ev), { type: "shelly_manager/subscribe" });
    this._unsub.catch(() => { this._unsub = null; });
  }

  onOp(opId, fn) {
    this.opListeners.set(opId, fn);
    return () => this.opListeners.delete(opId);
  }

  onBusyIdle(fn) {
    this.idleListeners.add(fn);
    return () => this.idleListeners.delete(fn);
  }

  onEvent(ev) {
    if (ev.busy) setTimeout(() => {
      if (!Object.keys(this.busy).length) for (const fn of [...this.idleListeners]) fn();
    }, 0);
    if (ev.kind === "progress") {
      this.busy = ev.busy;
      this.table?.updateBusy();
      this.renderBanner();
    } else if (ev.kind === "row") {
      this.busy = ev.busy;
      if (ev.row) {
        const i = this.rows.findIndex((r) => r.mac === ev.mac);
        // Geräteübergreifende Probleme (z. B. doppelte Namen) bleiben bis zum nächsten Gesamtabruf erhalten
        if (i >= 0) {
          const fleet = this.rows[i].problems.filter((p) => p.code === "duplicate_name");
          ev.row.problems.push(...fleet);
          ev.row.severity = ev.row.problems[0]?.severity ?? fleet[0]?.severity ?? null;
          this.rows[i] = ev.row;
        } else this.rows.push(ev.row);
        this.byMac.set(ev.mac, ev.row);
      }
      this.renderSoon();
      this.renderBanner();
      if (this.detail.mac === ev.mac) this.detail.render();
    } else if (ev.kind === "changed") {
      this.reload();
    } else if (ev.kind === "op") {
      this.opListeners.get(ev.op_id)?.(ev);
    } else if (ev.kind === "provision") {
      const c = (this.ble ?? []).find((x) => x.flow_id === ev.flow_id);
      if (c) Object.assign(c, { state: ev.state, error: ev.error });
      if (ev.state === "done" || ev.state === "failed") this.reload();
      this.renderNewCard();
    } else if (ev.kind === "job") {
      for (const fn of this.jobListeners) fn(ev.job);
    } else if (ev.kind === "scan") {
      this.scan = ev;
      this.renderBanner();
    }
  }

  onJob(fn) {
    this.jobListeners.add(fn);
    return () => this.jobListeners.delete(fn);
  }

  saveView() {
    store(VIEW_KEY, { columns: this.view.columns, sort: this.view.sort, filters: this.view.filters,
      refreshMode: this.view.refreshMode, preset: this.view.preset, iconCols: 1, ov2: 1 });
  }

  // ── Aktionen ──────────────────────────────────────────────────────────────
  async refresh(mode, macs) {
    this.view.refreshMode = mode;
    this.saveView();
    this.renderRefreshLabel();
    try {
      const res = await this.hass.callWS({ type: "shelly_manager/refresh", mode, ...(macs?.length ? { macs } : {}) });
      if (!res.queued) this.toast("Nichts abzufragen – Geräte laufen bereits oder sind nicht erreichbar.");
      else if (mode === "full") this.toast(`${res.queued} Geräte werden abgefragt.`);
    } catch (err) {
      this.toast(`Abfrage fehlgeschlagen: ${err.message ?? err}`);
    }
  }

  async startScan(ranges) {
    try {
      const res = await this.hass.callWS({ type: "shelly_manager/scan", ...(ranges ? { ranges } : {}) });
      if (!res.hosts) this.toast("Scan läuft bereits.");
    } catch (err) {
      this.toast(err.message ?? String(err), "error");
    }
  }

  openWebUis() {
    const rows = [...this.selected].map((m) => this.byMac.get(m)).filter((r) => r?.url);
    let blocked = 0;
    for (const r of rows) {
      const w = window.open(r.url, "_blank");
      if (!w) blocked++;
      else w.opener = null;
    }
    if (blocked) this.toast(`${blocked} von ${rows.length} Tabs blockiert – bitte Pop-ups für Home Assistant erlauben.`);
  }

  selectRows(macs, on) {
    for (const m of macs) on ? this.selected.add(m) : this.selected.delete(m);
    this.renderView();
  }

  toggleExpand(mac) {
    this.expanded.has(mac) ? this.expanded.delete(mac) : this.expanded.add(mac);
    this.renderView();
  }

  openDetail(mac) {
    this.detail.open(mac);
    this.table.markOpen(mac);
  }

  activeFilter(key) {
    const col = COLUMN_BY_KEY[key];
    const f = this.view.filters[key];
    return col && isActive(col, f) ? f : null;
  }

  setFilter(key, f) {
    const col = COLUMN_BY_KEY[key];
    if (!f || !isActive(col, f)) delete this.view.filters[key];
    else this.view.filters[key] = f;
    this.saveView();
    this.renderView();
  }

  // ── Ansichten (Spaltenpresets) ────────────────────────────────────────────
  applyPreset(key) {
    const p = PRESETS.find((x) => x.key === key);
    if (!p) return;
    this.view.preset = key;
    this.view.columns = [...p.columns];
    this.view.sort = p.sort ? { ...p.sort } : null;
    this.view.filters = JSON.parse(JSON.stringify(p.filters ?? {}));
    this.expanded.clear();
    if (p.expandAll) {
      for (const r of this.rows) if (r.channels.length > 1) this.expanded.add(r.mac);
    }
    this.saveView();
    this.renderView();
  }

  presetState() {
    const p = PRESETS.find((x) => x.key === this.view.preset) ?? PRESETS[0];
    const same = JSON.stringify(p.columns) === JSON.stringify(this.view.columns)
      && JSON.stringify(p.sort ?? null) === JSON.stringify(this.view.sort ?? null)
      && JSON.stringify(p.filters ?? {}) === JSON.stringify(this.view.filters);
    return { preset: p, modified: !same };
  }

  presetMenu(e) {
    const { preset, modified } = this.presetState();
    this.openMenu(e, [
      h("div", { class: "menu-title" }, "Ansicht"),
      ...PRESETS.map((p) => this.item(p.label, p.key === preset.key ? MDI.ok : null, () => this.applyPreset(p.key),
        { sub: p.key === preset.key && modified ? "angepasst – klicken zum Zurücksetzen" : null })),
    ]);
  }

  // ── Filtern, Suchen, Sortieren ────────────────────────────────────────────
  visibleRows() {
    const ctx = this.ctx;
    const q = this.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let rows = this.rows.filter((r) => {
      for (const [key, f] of Object.entries(this.view.filters)) {
        const col = COLUMN_BY_KEY[key];
        if (col && isActive(col, f) && !matchFilter(col, f, r, ctx)) return false;
      }
      if (!q.length) return true;
      const hay = [...SEARCH_FIELDS.map((k) => r[k]), ...r.channels.flatMap((c) => [c.name, c.names?.ha_full, c.entity]),
        ...r.entity_ids, ...r.problems.map((p) => p.text)].filter(Boolean).join(" ").toLowerCase();
      return q.every((t) => hay.includes(t));
    });
    const s = this.view.sort;
    if (s) {
      const col = s.key === "name" ? { get: (r) => r.name, type: "text" } : COLUMN_BY_KEY[s.key];
      if (col) {
        const byName = (a, b) => a.name.localeCompare(b.name, "de", { numeric: true, sensitivity: "base" });
        rows = [...rows].sort((a, b) => {
          // Spalten mit Dringlichkeit (Symbolspalten, Probleme): nach Rang, Gleichstand nach Name
          if (col.rank) return s.dir * (col.rank(a) - col.rank(b)) || byName(a, b);
          const va = values(col, a, ctx)[0], vb = values(col, b, ctx)[0];
          if (va === undefined || vb === undefined) return (va === undefined) - (vb === undefined) || byName(a, b);
          return s.dir * compare(col, a, b, ctx) || byName(a, b);
        });
      }
    }
    if (q.length) {
      // Treffer ordnen: ganzer Suchtext im Namen → alle Wörter im Namen → Treffer nur in anderen Feldern
      const phrase = q.join(" ");
      const names = (r) => [r.name, r.shelly_name, r.ha_name].filter(Boolean).join(" | ").toLowerCase();
      const score = (r) => (names(r).includes(phrase) ? 0 : q.every((t) => names(r).includes(t)) ? 1 : 2);
      rows = rows.map((r, i) => [score(r), i, r]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((x) => x[2]);
    }
    return rows;
  }

  // ── Grundgerüst ───────────────────────────────────────────────────────────
  renderShell() {
    this.shadowRoot.replaceChildren();
    this.search = h("input", { type: "search", placeholder: "Name, IP, MAC, Modell, Entity …", "aria-label": "Suchen" });
    this.search.addEventListener("input", debounce(() => { this.query = this.search.value; this.renderView(); }, 150));
    this.refreshLabel = h("span", { class: "bl" });
    this.refreshMain = h("button", { class: "btn", onclick: () => this.refresh(this.view.refreshMode, [...this.selected]) },
      icon(MDI.refresh, 18), this.refreshLabel);
    const refreshMore = h("button", { class: "btn", "aria-label": "Abfrageart", onclick: (e) => this.refreshMenu(e) }, icon(MDI.down, 18));
    this.tabsEl = h("nav", { class: "tabs" });
    this.toolbar = h("div", { class: "toolbar" });
    this.newCard = h("div", { class: "newcard", hidden: true });
    this.banner = h("div", { class: "banner", hidden: true });
    this.table = new Table(this);
    this.detail = new Detail(this);
    this.main = h("main", {}, h("p", { class: "empty" }, "Lade …"));
    this.menuEl = h("div", { class: "menu", role: "menu" });
    this.toastEl = h("div", { class: "toast" });
    const header = h("header", {},
      h("button", { class: "icon-btn menu-btn", "aria-label": "Seitenleiste",
        onclick: () => this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })) }, icon(MDI.menu)),
      h("div", { class: "brand" }, icon(MDI.chip, 22), h("span", {}, "Shellys")),
      this.tabsEl,
      h("div", { class: "grow" }),
      h("label", { class: "search" }, icon(MDI.search, 16), this.search),
      h("div", { class: "split" }, this.refreshMain, refreshMore),
      h("button", { class: "icon-btn", "aria-label": "Spalten", title: "Spalten", onclick: (e) => this.columnsMenu(e) }, icon(MDI.columns)),
      h("button", { class: "icon-btn", "aria-label": "Mehr", title: "Mehr", onclick: (e) => this.moreMenu(e) }, icon(MDI.dots)),
    );
    this.tipEl = h("div", { class: "tip", role: "tooltip" });
    this.shadowRoot.append(h("style", {}, STYLE), header, this.banner, this.newCard, this.toolbar, this.main, this.menuEl, this.toastEl, this.tipEl);
    this.setupTooltips();
    this.shadowRoot.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        // Schrittweise schließen: Dialog → Menü → Seitenpanel
        if (this._modalClose) this._modalClose();
        else if (this.menuEl.classList.contains("open")) this.closeMenu();
        else this.detail.close();
      }
    });
    this.shadowRoot.addEventListener("click", (e) => {
      if (!e.composedPath().includes(this.menuEl)) this.closeMenu();
    });
    this.renderRefreshLabel();
  }

  // Eigene Tooltips: erscheinen nach 150 ms statt nach der langen Browser-Verzögerung
  setupTooltips() {
    let timer = null, current = null;
    const hide = () => { clearTimeout(timer); current = null; this.tipEl.classList.remove("show"); };
    this.shadowRoot.addEventListener("mouseover", (e) => {
      const el = e.composedPath().find((n) => n instanceof Element && (n.hasAttribute?.("title") || n.dataset?.tip));
      if (!el || el === current) return;
      if (el.hasAttribute("title")) {
        el.dataset.tip = el.getAttribute("title");
        el.removeAttribute("title");  // native Anzeige unterdrücken
      }
      if (!el.dataset.tip) return;
      hide();
      current = el;
      timer = setTimeout(() => {
        if (current !== el || !el.isConnected) return;
        // Mehrere Zeilen: erste als Überschrift (sofern keine Aufzählung), Rest als Liste
        const lines = el.dataset.tip.split("\n").map((l) => l.trim()).filter(Boolean);
        const head = lines.length > 1 && !lines[0].startsWith("•") ? lines.shift() : null;
        this.tipEl.replaceChildren(...(lines.length > 1 || head
          ? [head ? h("div", { class: "th" }, head) : null, h("ul", {}, lines.map((l) => h("li", {}, l.replace(/^•\s*/, ""))))].filter(Boolean)
          : [lines[0] ?? ""]));
        this.tipEl.classList.add("show");
        const r = el.getBoundingClientRect(), t = this.tipEl.getBoundingClientRect();
        let top = r.bottom + 6;
        if (top + t.height > window.innerHeight - 8) top = r.top - t.height - 6;
        this.tipEl.style.top = `${Math.max(8, top)}px`;
        this.tipEl.style.left = `${Math.max(8, Math.min(r.left + r.width / 2 - t.width / 2, window.innerWidth - t.width - 8))}px`;
      }, 150);
    });
    this.shadowRoot.addEventListener("mouseout", (e) => {
      if (current && !current.contains(e.relatedTarget)) hide();
    });
    this.shadowRoot.addEventListener("mousedown", hide);
    this.shadowRoot.addEventListener("scroll", hide, true);
  }

  renderRefreshLabel() {
    const n = this.selected.size;
    const what = this.view.refreshMode === "full" ? "Alle Infos" : "Status";
    this.refreshLabel.textContent = n ? `${what} (${n})` : what;
    this.refreshMain.title = `${this.view.refreshMode === "full" ? "Alle Geräteinfos" : "Kanal-Status"} ${n ? `der ${n} markierten` : "aller"} Geräte aktualisieren`;
  }

  renderTabs() {
    const nProblems = this.rows.filter((r) => r.problems.some((p) => p.severity !== "info")).length;
    this.tabsEl.replaceChildren(...TABS.map(([k, l]) => h("button", {
      class: `tab${this.tab === k ? " active" : ""}`, onclick: () => { this.tab = k; this.renderTabs(); this.renderView(); },
    }, l, k === "problems" && nProblems ? h("span", { class: "badge warn" }, nProblems) : null)));
  }

  renderView() {
    if (!this.main) return;
    this.renderRefreshLabel();
    if (this.tab === "presets") {
      this.toolbar.hidden = true;
      if (this.newCard) this.newCard.hidden = true;
      if (!this._presetsEl || this.main.firstChild !== this._presetsEl) {
        this._presetsEl = presetsView(this);
        this.main.replaceChildren(this._presetsEl);
      }
      return;
    }
    this._presetsEl = null;
    if (this.tab === "problems") {
      this.toolbar.hidden = true;
      if (this.newCard) this.newCard.hidden = true;
      this.main.replaceChildren(this.problemsView(), this.detail.el);
      return;
    }
    this.toolbar.hidden = false;
    this.renderNewCard();
    const rows = this.visibleRows();
    this.renderToolbar(rows);
    this.table.render(rows);
    if (this.main.firstChild !== this.table.el) this.main.replaceChildren(this.table.el, this.detail.el);
  }

  renderToolbar(rows) {
    const chips = Object.entries(this.view.filters).map(([key, f]) => {
      const col = COLUMN_BY_KEY[key];
      if (!col || !isActive(col, f)) return null;
      return h("span", { class: "chip active" },
        h("span", { style: { cursor: "pointer" }, onclick: (e) => this.filterMenu(e, key) }, filterLabel(col, f)),
        h("button", { "aria-label": "Filter entfernen", onclick: () => this.setFilter(key, null) }, icon(MDI.close, 14)));
    });
    const n = this.selected.size;
    this.toolbar.className = `toolbar${n ? " selbar" : ""}`;
    const { preset, modified } = this.presetState();
    this.toolbar.replaceChildren(...[
      h("button", { class: "btn", style: { height: "30px" }, title: "Spaltenpreset wählen", onclick: (e) => this.presetMenu(e) },
        icon(MDI.columns, 16), `${preset.label}${modified ? " *" : ""}`, icon(MDI.down, 16)),
      h("span", { class: "count" }, rows.length === this.rows.length ? `${rows.length} Geräte` : `${rows.length} von ${this.rows.length}`),
      ...chips,
      h("span", { class: "chip dashed", onclick: (e) => this.addFilterMenu(e) }, icon(MDI.filter, 14), "Filter"),
      chips.some(Boolean) ? h("button", { class: "btn", style: { height: "30px" }, onclick: () => { this.view.filters = {}; this.saveView(); this.renderView(); } }, "Filter löschen") : null,
      h("div", { class: "grow" }),
      n ? h("span", { class: "count" }, `${n} markiert`) : null,
      n ? h("button", { class: "btn", style: { height: "30px" }, onclick: (e) => this.namesMenu(e) }, icon(MDI.tag, 16), "Namen", icon(MDI.down, 16)) : null,
      n ? h("button", { class: "btn", style: { height: "30px" }, onclick: () => batchDialog(this, [...this.selected]) }, icon(MDI.cog, 16), "Einstellungen") : null,
      n ? h("button", { class: "btn", style: { height: "30px" }, onclick: () => firmwareDialog(this, [...this.selected]) }, icon(MDI.update, 16), "Firmware") : null,
      n ? h("button", { class: "btn", style: { height: "30px" }, onclick: () => this.openWebUis() }, icon(MDI.open, 16), "Webinterfaces öffnen") : null,
      n ? h("button", { class: "btn", style: { height: "30px" }, onclick: () => { this.selected.clear(); this.renderView(); } }, "Auswahl aufheben") : null,
    ].filter(Boolean));
  }

  // »Neu«: Bluetooth-Funde (noch nicht im WLAN) und frisch eingerichtete Geräte
  renderNewCard() {
    if (!this.newCard || this.tab !== "devices") return;
    const STATE = { restoring: "spiele Sicherung ein …", connecting: "verbinde …", scanning: "suche WLANs …", sending: "übertrage WLAN …", joining: "wartet auf Gerät im Netz …",
      done: "fertig", failed: "fehlgeschlagen" };
    const ble = this.ble ?? [];
    const fresh = Object.entries(this.newDevices ?? {}).map(([mac, info]) => ({ mac, info, r: this.byMac.get(mac) })).filter((x) => x.r);
    this.newCard.hidden = !ble.length && !fresh.length;
    if (this.newCard.hidden) return;
    this.newCard.replaceChildren(...[
      ble.length ? h("div", { class: "newsec" }, h("div", { class: "newhead" }, icon(MDI.radar, 16), `Über Bluetooth gefunden (${ble.length})`),
        ble.map((c) => h("div", { class: "newitem" },
          h("span", { class: "grow" }, c.name, h("span", { class: "muted small" }, ` · ${c.mac}`),
            c.held ? h("span", { class: "small muted", title: "Wurde im Manager zurückgesetzt – wird nicht automatisch eingerichtet" }, " · zurückgesetzt, manuell einrichten")
              : this.options?.auto_provision && !c.state ? h("span", { class: "small muted" }, " · wird automatisch eingerichtet …") : null),
          c.state ? h("span", { class: `small ${c.state === "failed" ? "sev-error" : c.state === "done" ? "ok" : "muted"}`, title: c.error ?? "" },
            ["connecting", "scanning", "sending", "joining", "restoring"].includes(c.state) ? spinner(14) : null, ` ${STATE[c.state] ?? c.state}`) : null,
          ["connecting", "scanning", "sending", "joining", "restoring"].includes(c.state) ? null
            : h("button", { class: "btn", style: { height: "28px" }, onclick: () => this.provisionDialog(c) }, icon(MDI.wifi, 14), c.backup ? "Neu einrichten …" : "Ins WLAN bringen …"),
          ["connecting", "scanning", "sending", "joining", "restoring"].includes(c.state) ? null
            : h("button", { class: "icon-btn", title: "Aus der Liste entfernen", "aria-label": "Entfernen", onclick: () => this.forgetFound(c.mac, c.name) }, icon(MDI.close, 16)),
          c.backup && !["connecting", "scanning", "sending", "joining", "restoring"].includes(c.state) ? h("div", { class: "newbackup small" },
            icon(MDI.restore, 14), h("span", { class: "grow" }, `Sicherung vom ${new Date(c.backup.ts * 1000).toLocaleString()} vorhanden (»${c.backup.name ?? "ohne Namen"}«)`),
            h("button", { class: "btn primary", style: { height: "26px" }, onclick: () => this.restoreProvision(c) }, "Einrichten + Sicherung einspielen"),
            h("button", { class: "btn", style: { height: "26px" }, onclick: () => this.deleteBackup(c) }, "Sicherung löschen")) : null))) : null,
      fresh.length ? h("div", { class: "newsec" }, h("div", { class: "newhead" }, icon(MDI.tag, 16), `Neu eingerichtet – noch nicht fertig konfiguriert (${fresh.length})`),
        fresh.map(({ mac, r }) => h("div", { class: "newitem" },
          h("span", { class: "grow" }, r.name, h("span", { class: "muted small" }, ` · ${r.model ?? ""}${r.host ? ` · ${r.host}` : ""}`)),
          h("button", { class: "btn primary", style: { height: "28px" }, onclick: () => wizardDialog(this, mac) }, "Einrichten …"),
          h("button", { class: "btn", style: { height: "28px" }, onclick: async () => { await this.hass.callWS({ type: "shelly_manager/new/dismiss", mac }); } }, "Ausblenden")))) : null,
    ].filter(Boolean));
  }

  async restoreProvision(c) {
    try {
      await this.hass.callWS({ type: "shelly_manager/provision/start", flow_id: c.flow_id, restore: true });
      c.state = "connecting";
      this.renderNewCard();
    } catch (err) {
      this.toast(err.message ?? String(err), "error");
    }
  }

  // Fund ohne HA-Eintrag aus der Liste nehmen (Bluetooth, Netzwerk-Scan, UniFi)
  forgetFound(mac, name) {
    const run = async (permanent) => {
      try {
        const r = await this.hass.callWS({ type: "shelly_manager/found/forget", mac, permanent });
        dlg.close();
        this.toast(`${name}: ${r.result}`, "ok");
        this.detail?.mac === mac && this.detail.close();
        this.load();
      } catch (err) { this.toast(err.message ?? String(err), "error"); }
    };
    const dlg = modal(this, { title: `${name} entfernen`, body: h("div", { class: "form" },
      h("p", {}, "Das Gerät ist nicht in Home Assistant eingerichtet und wird nur als Fund angezeigt."),
      h("ul", { class: "small steps" },
        h("li", {}, h("b", {}, "Aus der Liste nehmen: "), "verwirft den Fund. Sendet das Gerät weiter im Einrichtungsmodus, taucht es später wieder auf."),
        h("li", {}, h("b", {}, "Dauerhaft ignorieren: "), "HA merkt sich das Gerät als ignoriert (rückgängig unter Einstellungen → Geräte & Dienste → Shelly → ignorierte Einträge)."))),
      actions: [{ label: "Abbrechen" }, { label: "Dauerhaft ignorieren", onclick: () => run(true) },
        { label: "Aus der Liste nehmen", primary: true, onclick: () => run(false) }] });
  }

  deleteBackup(c) {
    const dlg = modal(this, { title: "Sicherung löschen", body: h("p", {}, `Sicherung für »${c.backup.name ?? c.mac}« endgültig löschen? ` +
      "Das Gerät bleibt im Einrichtungsmodus und kann danach als neues Gerät eingerichtet werden."),
      actions: [{ label: "Abbrechen" }, { label: "Löschen", danger: true, onclick: async () => {
        await this.hass.callWS({ type: "shelly_manager/reset/backup_delete", mac: c.mac });
        c.backup = null;
        dlg.close();
        this.renderNewCard();
        this.toast("Sicherung gelöscht", "ok");
      } }] });
  }

  provisionDialog(c) {
    const o = this.options ?? {};
    const ssid = h("input", { type: "text", value: o.wifi_ssid ?? "", placeholder: "leer lassen = WLAN-Vorgabe verwenden" });
    const pw = h("input", { type: "password", placeholder: "leer lassen = gespeichertes Passwort verwenden" });
    const dlg = modal(this, { title: `${c.name} ins WLAN bringen`, body: h("div", { class: "form" },
      h("p", { class: "small" }, "Die Shelly-Integration verbindet sich per Bluetooth (auch über Bluetooth-Proxys), überträgt das WLAN, wartet auf das Gerät im Netz und schaltet danach Geräte-AP und Bluetooth-RPC ab."),
      h("label", { class: "field" }, h("span", { class: "flabel" }, "WLAN (SSID)"), ssid),
      h("label", { class: "field" }, h("span", { class: "flabel" }, "Passwort"), pw)),
    actions: [{ label: "Abbrechen" }, { label: "Starten", primary: true, onclick: async () => {
      try {
        await this.hass.callWS({ type: "shelly_manager/provision/start", flow_id: c.flow_id,
          ...(ssid.value.trim() ? { ssid: ssid.value.trim() } : {}), ...(pw.value ? { password: pw.value } : {}) });
        c.state = "connecting";
        this.renderNewCard();
        dlg.close();
      } catch (err) {
        this.toast(err.message ?? String(err), "error");
      }
    } }] });
  }

  renderBanner() {
    if (!this.banner) return;
    const busy = Object.values(this.busy);
    const parts = [];
    if (busy.length) {
      const running = busy.filter((b) => b === "running").length;
      parts.push(h("span", { class: "row" }, spinner(16), `Abfrage: ${running} aktiv, ${busy.length - running} wartend`));
    }
    if (this.scan?.running) {
      const pct = this.scan.total ? Math.round((this.scan.done / this.scan.total) * 100) : 0;
      parts.push(h("span", { class: "row" }, spinner(14), `IP-Scan ${this.scan.done}/${this.scan.total}, ${this.scan.found} Shellys`),
        h("span", { class: "progress" }, h("i", { style: { width: `${pct}%` } })));
    }
    this.banner.hidden = !parts.length;
    this.banner.replaceChildren(...parts);
  }

  // ── Probleme ──────────────────────────────────────────────────────────────
  problemsView() {
    const STAGES = { 1: "Gerätename im Shelly festlegen", 2: "Kanalnamen im Shelly festlegen", 3: "Home Assistant angleichen",
      4: "Abgeleitete Namen übernehmen (Integration, UniFi)" };
    const order = { error: 0, warning: 1, info: 2 };
    const groups = new Map();
    for (const r of this.rows) {
      for (const p of r.problems) {
        const key = `${p.severity}|${p.code}`;
        if (!groups.has(key)) groups.set(key, { key, p, entries: [], blocked: [] });
        const g = groups.get(key);
        (p.blocked ? g.blocked : g.entries).push({ r, p });
      }
    }
    const all = [...groups.values()].sort((a, b) => (order[a.p.severity] - order[b.p.severity]) || (b.entries.length - a.entries.length));
    if (!all.length) return h("div", { class: "view" }, h("p", { class: "empty" }, "Keine Probleme gefunden."));
    this.openGroups ??= new Set(all.filter((g) => g.p.severity === "error").map((g) => g.key));
    const titleOf = (text) => text.replace(/\s*\(.*\)$|:.*$/, "");

    const groupEl = ({ key, p, entries, blocked }) => {
      const s = SEVERITY[p.severity] ?? SEVERITY.info;
      const title = titleOf(p.text);
      const limit = p.severity === "error" ? 5 : 8;
      const fix = entries.length ? fixFor(p.code) : null;
      const devs = new Set(entries.map((e) => e.r.mac)).size;
      const waiting = new Set(blocked.map((e) => e.r.mac)).size;
      const det = h("details", { class: `pgroup ${s.cls}-group${!devs ? " pblocked" : ""}`, open: this.openGroups.has(key) },
        h("summary", {},
          h("span", { class: s.cls }, icon(s.icon, 18)),
          h("span", { class: "grow ptitle" }, title),
          fix ? h("button", { class: "btn fixbtn", onclick: (e) => { e.preventDefault(); e.stopPropagation(); fix.run(this, entries, e); } },
            icon(MDI.ok, 14), fix.label) : null,
          h("span", { class: "pcount" }, devs ? `${devs} Gerät${devs === 1 ? "" : "e"}` : "", waiting
            ? h("span", { class: "muted", title: "Diese Geräte haben noch offene Punkte einer früheren Stufe" }, `${devs ? " · " : ""}${waiting} warten`) : null)),
        h("div", { class: "pbody" },
          [...entries.slice(0, limit), ...(entries.length < limit ? blocked.slice(0, limit - entries.length) : [])].map(({ r, p: pp }) => {
            const detail = pp.text.length > title.length ? pp.text.slice(title.length).replace(/^[:\s]+/, "") : "";
            return h("button", { class: `pdev${pp.blocked ? " waiting" : ""}`, onclick: () => this.openDetail(r.mac) },
              h("span", {}, r.name),
              h("span", { class: "muted small" }, pp.blocked ? `wartet auf Stufe ${r.name_stage}` : detail || r.model || ""));
          }),
          h("div", { class: "row pfoot" },
            entries.length + blocked.length > limit ? h("span", { class: "muted small grow" }, `… und ${entries.length + blocked.length - limit} weitere`) : h("span", { class: "grow" }),
            h("button", { class: "btn", style: { height: "28px" },
              onclick: () => { this.tab = "devices"; this.view.filters = { problem_codes: { values: [title] } }; this.saveView(); this.renderTabs(); this.renderView(); } },
            icon(MDI.filter, 14), "In der Tabelle zeigen"))));
      det.addEventListener("toggle", () => { det.open ? this.openGroups.add(key) : this.openGroups.delete(key); });
      return det;
    };

    const section = (title, sub, list) => (list.length ? h("div", { class: "psection" },
      h("div", { class: "pshead" }, h("span", {}, title), sub ? h("span", { class: "muted small" }, sub) : null), list.map(groupEl)) : null);
    const critical = all.filter((g) => !g.p.stage && g.p.severity === "error");
    const others = all.filter((g) => !g.p.stage && g.p.severity !== "error");
    const done = this.rows.filter((r) => r.in_ha && !r.name_stage).length;
    const inHa = this.rows.filter((r) => r.in_ha).length;
    const perStage = (n) => this.rows.filter((r) => r.name_stage === n).length;
    return h("div", { class: "view" }, h("div", { class: "plist-v" },
      h("div", { class: "psum muted small" }, `Namensgebung: ${done} von ${inHa} Geräten fertig · offen in Stufe 1: ${perStage(1)} · 2: ${perStage(2)} · 3: ${perStage(3)} · 4: ${perStage(4)}`),
      section("Kritisch", null, critical),
      [1, 2, 3, 4].map((n) => section(`Stufe ${n}: ${STAGES[n]}`,
        n === 1 ? "zuerst – legt den Namen fest, aus dem alles andere abgeleitet wird" : `erst wenn Stufe ${n - 1} erledigt ist`,
        all.filter((g) => g.p.stage === n))),
      section("Weitere Probleme", null, others)));
  }

  // ── Menüs ─────────────────────────────────────────────────────────────────
  openMenu(e, children) {
    e.stopPropagation();
    this.menuEl.replaceChildren(...children.filter(Boolean));
    this.menuEl.classList.add("open");
    const rect = e.currentTarget?.getBoundingClientRect?.() ?? { left: e.clientX, bottom: e.clientY };
    this.menuEl.style.left = "0px";
    const w = this.menuEl.offsetWidth, hgt = this.menuEl.offsetHeight;
    this.menuEl.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - w - 8))}px`;
    this.menuEl.style.top = `${Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - hgt - 8))}px`;
  }

  closeMenu() {
    this.menuEl?.classList.remove("open");
  }

  item(label, ic, action, { disabled = false, sub = null } = {}) {
    return h("button", { disabled, onclick: () => { this.closeMenu(); action(); } },
      ic ? icon(ic, 18) : h("span", { style: { width: "18px" } }),
      h("span", { class: "grow" }, label, sub ? h("div", { class: "muted small" }, sub) : null));
  }

  // Aktionen für ein Gerät – im Seitenpanel (⋮) und im Kontextmenü der Tabelle
  deviceActions(r) {
    const ids = r.entity_ids ?? [];
    return [
      h("div", { class: "menu-title" }, r.name),
      r.url ? this.item("Webinterface öffnen", MDI.open, () => { const w = window.open(r.url, "_blank"); if (w) w.opener = null; }) : null,
      r.ha_device_id ? this.item("Gerät in HA", MDI.ha, () => navigate(`/config/devices/device/${r.ha_device_id}`)) : null,
      ids.length ? this.item("Verlauf", MDI.history, () => navigate(`/history?entity_id=${encodeURIComponent(ids.join(","))}`)) : null,
      ids.length ? this.item("Logbuch", MDI.logbook, () => navigate(`/logbook?entity_id=${encodeURIComponent(ids.join(","))}`)) : null,
      h("div", { class: "sep" }),
      this.item("Infos vom Gerät abfragen", MDI.refresh, () => this.refresh("full", [r.mac]), { disabled: !!this.busy[r.mac] }),
      r.names?.device?.shelly != null ? this.item("Umbenennen …", MDI.tag, () => renameItemDialog(this, r, r.names.device)) : null,
      r.in_ha && r.has_config ? this.item("Einrichtungs-Assistent", MDI.cog, () => wizardDialog(this, r.mac)) : null,
      r.has_config ? this.item("Einstellungen …", MDI.cog, () => batchDialog(this, [r.mac])) : null,
      r.update ? this.item("Firmware aktualisieren …", MDI.update, () => firmwareDialog(this, [r.mac]), { sub: `${r.fw} → ${r.update}` }) : null,
      ids.length ? this.item("Entity-IDs prüfen", MDI.tag, () => entityDialog(this, [r.mac])) : null,
      r.has_config && r.gen !== 1 ? this.item("Fallback-Modus …", MDI.radar, () => fallbackDialog(this, r.mac),
        { sub: r.fallback?.enabled ? `aktiv · ${r.fallback.bindings} Belegung(en)` : "Tasten steuern ohne HA" }) : null,
      h("div", { class: "sep" }),
      r.host ? this.item("Neu starten …", MDI.refresh, () => this.confirmReboot([r.mac])) : null,
      !r.in_ha ? this.item("Aus der Liste entfernen …", MDI.close, () => this.forgetFound(r.mac, r.name), { sub: "Fund verwerfen oder dauerhaft ignorieren" }) : null,
      this.item("Zurücksetzen und entfernen …", MDI.restore, () => resetDialog(this, r.mac),
        { sub: "Werksreset, aus HA und UniFi entfernen" }),
    ];
  }

  confirmReboot(macs) {
    rebootDialog(this, macs);
  }

  rowMenu(e, mac) {
    e.preventDefault();
    const pos = { stopPropagation: () => e.stopPropagation(), currentTarget: null, clientX: e.clientX, clientY: e.clientY };
    const sel = [...this.selected];
    if (sel.length > 1 && sel.includes(mac)) {
      this.openMenu(pos, [
        h("div", { class: "menu-title" }, `${sel.length} markierte Geräte`),
        this.item("Einstellungen …", MDI.cog, () => batchDialog(this, sel)),
        this.item("Firmware …", MDI.update, () => firmwareDialog(this, sel)),
        this.item("HA-Namen in die Shellys schreiben", MDI.chip, () => namesDialog(this, sel.map((m) => ({ mac: m, direction: "to_shelly" })), "HA-Namen → Shellys")),
        this.item("Shelly-Namen in HA übernehmen", MDI.ha, () => namesDialog(this, sel.map((m) => ({ mac: m, direction: "to_ha" })), "Shelly-Namen → Home Assistant")),
        this.item("Webinterfaces öffnen", MDI.open, () => this.openWebUis()),
        this.item("Infos von den Geräten abfragen", MDI.refresh, () => this.refresh("full", sel)),
        h("div", { class: "sep" }),
        this.item("Neu starten …", MDI.refresh, () => this.confirmReboot(sel)),
      ]);
      return;
    }
    const r = this.byMac.get(mac);
    if (r) this.openMenu(pos, [this.item("Details", MDI.info, () => this.openDetail(mac)), ...this.deviceActions(r).slice(1)]);
  }

  namesMenu(e) {
    const macs = [...this.selected];
    const items = (direction) => macs.map((mac) => ({ mac, direction }));
    this.openMenu(e, [
      h("div", { class: "menu-title" }, `${macs.length} markierte Geräte`),
      this.item("HA-Namen in die Shellys schreiben", MDI.chip, () => namesDialog(this, items("to_shelly"), "HA-Namen → Shellys"),
        { sub: "Shelly-Name = Bereich + Name, Einkanaler ohne Kanalnamen" }),
      this.item("Shelly-Namen in HA übernehmen", MDI.ha, () => namesDialog(this, items("to_ha"), "Shelly-Namen → Home Assistant"),
        { sub: "Bereich vom Namen trennen und zuweisen" }),
      h("div", { class: "sep" }),
      this.item("Namen nach UniFi übertragen", MDI.wifi, () => unifiDialog(this, macs, ["names"]), { sub: "Client-Alias = voller Shelly-Name" }),
      this.item("Feste IPs in UniFi vergeben", MDI.wifi, () => unifiDialog(this, macs, ["fixed_ip"]), { sub: "Nur für Geräte, die per IP angesprochen werden" }),
      this.item("Reservierung in UniFi entfernen", MDI.wifi, () => unifiDialog(this, macs, ["release"]), { sub: "DHCP vergibt dann wieder dynamisch" }),
      h("div", { class: "sep" }),
      this.item("Entity-IDs prüfen", MDI.tag, () => entityDialog(this, macs), { sub: "Mit den automatischen IDs von HA vergleichen" }),
    ]);
  }

  refreshMenu(e) {
    const n = this.selected.size;
    const scope = n ? `die ${n} markierten Geräte` : "alle Geräte";
    this.openMenu(e, [
      h("div", { class: "menu-title" }, `Aktualisieren: ${scope}`),
      this.item("Nur Kanal-Status", this.view.refreshMode === "status" ? MDI.ok : null, () => this.refresh("status", [...this.selected]),
        { sub: "Schaltzustände und Leistung aus Home Assistant" }),
      this.item("Alle Geräteinfos", this.view.refreshMode === "full" ? MDI.ok : null, () => this.refresh("full", [...this.selected]),
        { sub: "Konfiguration und Status direkt vom Gerät, gestaffelt" }),
    ]);
  }

  moreMenu(e) {
    const ranges = this.options?.ranges ?? [];
    const input = h("input", { type: "text", value: ranges.join(", "), placeholder: "z. B. 192.168.1.0/24" });
    this.openMenu(e, [
      h("div", { class: "grp" }, "Netzwerk scannen"),
      h("div", { class: "pad" }, input,
        h("button", { class: "btn primary", disabled: this.scan?.running, onclick: () => { this.closeMenu(); this.startScan(input.value.trim()); } },
          icon(MDI.radar, 16), this.scan?.running ? "Scan läuft …" : "Scan starten")),
      h("div", { class: "sep" }),
      this.res_unifi === false ? null : this.item("UniFi abgleichen …", MDI.wifi, () => unifiDialog(this, null, ["names"]), { sub: "Aliasse aller Shellys in UniFi" }),
      this.item("Firmware-Updates …", MDI.update, () => firmwareDialog(this, null), { sub: "Alle Geräte mit verfügbarem Update" }),
      this.item("Verlauf der Änderungen", MDI.history, () => jobsDialog(this), { sub: "Batch-Aufträge mit Rückgängig" }),
      this.item("Verwaiste Verweise suchen …", MDI.alert, () => orphansDialog(this), { sub: "Verweise auf nicht mehr vorhandene Entitäten" }),
      this.item("Verweise umschreiben …", MDI.tag, () => refsManualDialog(this), { sub: "Alte Entity-IDs in YAML, Dashboards, Helfern ersetzen" }),
      this.item("Ansicht zurücksetzen (Übersicht)", MDI.columns, () => this.applyPreset("overview")),
      this.item("Optionen der Integration", MDI.cog, () => navigate("/config/integrations/integration/shelly_manager")),
    ]);
  }

  columnsMenu(e) {
    const shown = new Set(this.view.columns);
    const kids = [h("div", { class: "menu-title" }, "Spalten einblenden")];
    for (const g of GROUPS) {
      kids.push(h("div", { class: "grp" }, g));
      for (const c of COLUMNS.filter((x) => x.group === g)) {
        const cb = h("input", { type: "checkbox", checked: shown.has(c.key) });
        cb.addEventListener("change", () => {
          if (cb.checked) {
            // In Katalogreihenfolge einsortieren
            const order = COLUMNS.map((x) => x.key);
            this.view.columns = order.filter((k) => k === c.key || this.view.columns.includes(k));
          } else this.view.columns = this.view.columns.filter((k) => k !== c.key);
          this.saveView();
          this.renderView();
        });
        kids.push(h("label", { class: "mi" }, cb, h("span", { class: "lbl" }, c.label)));
      }
    }
    this.openMenu(e, kids);
  }

  addFilterMenu(e) {
    const anchor = e.currentTarget;
    const kids = [h("div", { class: "menu-title" }, "Filtern nach")];
    for (const g of GROUPS) {
      kids.push(h("div", { class: "grp" }, g));
      for (const c of COLUMNS.filter((x) => x.group === g)) {
        kids.push(h("button", { onclick: (ev) => this.filterMenu(ev, c.key, anchor) }, h("span", { class: "lbl" }, c.label)));
      }
    }
    this.openMenu(e, kids);
  }

  columnMenu(e, key) {
    const col = key === "name" ? { key: "name", label: "Name", type: "text" } : COLUMN_BY_KEY[key];
    const s = this.view.sort;
    const kids = [
      h("div", { class: "menu-title" }, col.label),
      this.item("Aufsteigend sortieren", MDI.sortAsc, () => { this.view.sort = { key, dir: 1 }; this.saveView(); this.renderView(); }),
      this.item("Absteigend sortieren", MDI.sortDesc, () => { this.view.sort = { key, dir: -1 }; this.saveView(); this.renderView(); }),
      s?.key === key ? this.item("Sortierung aufheben", null, () => { this.view.sort = null; this.saveView(); this.renderView(); }) : null,
    ];
    if (key !== "name") {
      kids.push(this.item("Spalte ausblenden", MDI.eyeOff, () => { this.view.columns = this.view.columns.filter((k) => k !== key); this.saveView(); this.renderView(); }));
      kids.push(h("div", { class: "sep" }), ...this.filterControls(col));
    }
    this.openMenu(e, kids);
  }

  filterMenu(e, key, anchor) {
    const col = COLUMN_BY_KEY[key];
    const ev = anchor ? { stopPropagation: () => e.stopPropagation(), currentTarget: anchor, clientX: e.clientX, clientY: e.clientY } : e;
    this.openMenu(ev, [h("div", { class: "menu-title" }, `Filter: ${col.label}`), ...this.filterControls(col)]);
  }

  filterControls(col) {
    const f = { ...(this.view.filters[col.key] ?? {}) };
    if (col.type === "num") {
      const min = h("input", { type: "number", placeholder: "min", value: f.min ?? "" });
      const max = h("input", { type: "number", placeholder: "max", value: f.max ?? "" });
      const apply = debounce(() => this.setFilter(col.key, { min: min.value, max: max.value }), 300);
      min.addEventListener("input", apply);
      max.addEventListener("input", apply);
      return [h("div", { class: "pad" }, h("div", { class: "row" }, min, max))];
    }
    if (col.type === "text") {
      const inp = h("input", { type: "text", placeholder: "enthält …", value: f.text ?? "" });
      inp.addEventListener("input", debounce(() => this.setFilter(col.key, { text: inp.value }), 300));
      setTimeout(() => inp.focus(), 0);
      return [h("div", { class: "pad" }, inp)];
    }
    // Aufzählung: vorhandene Werte mit Anzahl (über alle Geräte, unabhängig von anderen Filtern)
    const counts = new Map();
    for (const r of this.rows) {
      const vals = values(col, r, this.ctx);
      if (!vals.length) counts.set("(leer)", (counts.get("(leer)") ?? 0) + 1);
      for (const v of new Set(vals.map(String))) counts.set(v, (counts.get(v) ?? 0) + 1);
    }
    const sel = new Set(f.values ?? []);
    const opts = [...counts.entries()].sort((a, b) => (a[0] === "(leer)") - (b[0] === "(leer)") || a[0].localeCompare(b[0], "de", { numeric: true }));
    return opts.map(([v, n]) => {
      const cb = h("input", { type: "checkbox", checked: sel.has(v) });
      cb.addEventListener("change", () => {
        cb.checked ? sel.add(v) : sel.delete(v);
        this.setFilter(col.key, { values: [...sel] });
      });
      return h("label", { class: "mi" }, cb, h("span", { class: "lbl" }, v), h("span", { class: "cnt" }, n));
    });
  }

  // kind: "ok" (Häkchen), "error" (rot) oder leer
  toast(text, kind = "") {
    this.toastEl.replaceChildren(...[kind === "ok" ? icon(MDI.ok, 18) : kind === "error" ? icon(MDI.error, 18) : null, h("span", {}, text)].filter(Boolean));
    this.toastEl.className = `toast show ${kind}`;
    clearTimeout(this._toastT);
    this._toastT = setTimeout(() => this.toastEl.classList.remove("show"), kind === "error" ? 8000 : 4000);
  }
}

if (!customElements.get("shelly-manager-panel")) customElements.define("shelly-manager-panel", ShellyManagerPanel);
