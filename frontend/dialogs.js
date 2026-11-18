// Dialoge: Namen übernehmen (Vorschau → Anwenden) und Entity-IDs neu erzeugen
import { refsDialog } from "./refs.js";
import { MDI, h, icon, spinner } from "./util.js";

const USAGE_TYPES = { automation: "Automation", script: "Skript", scene: "Szene", group: "Gruppe" };

export function modal(panel, { title, body, actions = [], wide = false }) {
  const api = {};
  const close = () => { backdrop.remove(); if (panel._modalClose === close) panel._modalClose = null; api.onClose?.(); };
  const foot = h("div", { class: "mfoot" });
  const setActions = (list) => foot.replaceChildren(...list.map((a) => h("button", {
    class: `btn${a.primary ? " primary" : ""}${a.danger ? " danger" : ""}`, disabled: !!a.disabled, onclick: () => (a.onclick ? a.onclick() : close()),
  }, a.label)));
  const bodyEl = h("div", { class: "mbody" }, body);
  // Nur schließen, wenn der Klick auf dem Hintergrund begann und endete (Markieren im Textfeld und
  // Loslassen außerhalb des Dialogs schließt ihn nicht)
  let downOnBg = false;
  const backdrop = h("div", { class: "modal-bg", onmousedown: (e) => { downOnBg = e.target === backdrop; },
    onclick: (e) => { if (e.target === backdrop && downOnBg) close(); downOnBg = false; } },
    h("div", { class: `modal${wide ? " wide" : ""}`, role: "dialog", "aria-label": title },
      h("div", { class: "mhead" }, h("span", { class: "grow" }, title),
        h("button", { class: "icon-btn", "aria-label": "Schließen", onclick: close }, icon(MDI.close))),
      bodyEl, foot));
  setActions(actions);
  panel._modalClose?.();
  panel._modalClose = close;
  panel.shadowRoot.append(backdrop);
  return Object.assign(api, { close, setBody: (n) => bodyEl.replaceChildren(n), appendBody: (n) => bodyEl.append(n), setActions });
}

// Fortschritt zum Abhaken: Balken + Liste; Zustände kommen per set() oder als »op«-Events vom Backend
export function progressList(panel, items, opId = null) {
  const rows = new Map();
  const bar = h("i", { style: { width: "0%" } });
  const count = h("span", { class: "muted small" });
  const list = h("div", { class: "oplist plist2" });
  const ICON = { queued: [MDI.clock, "muted"], running: [MDI.refresh, "spin"], done: [MDI.ok, "ok"], error: [MDI.error, "sev-error"] };
  for (const it of items) {
    const ic = h("span", {});
    const note = h("span", { class: "small sev-error" });
    const el = h("div", { class: "jobdev" }, ic, h("span", { class: "grow" }, it.label), note);
    rows.set(it.id, { ic, note, state: "queued" });
    list.append(el);
  }
  const render = () => {
    let done = 0;
    for (const r of rows.values()) {
      const [i, c] = ICON[r.state] ?? ICON.queued;
      r.ic.className = c === "spin" ? "" : c;
      r.ic.replaceChildren(c === "spin" ? spinner(14) : icon(i, 16));
      if (r.state === "done" || r.state === "error") done++;
    }
    bar.style.width = `${rows.size ? Math.round((done / rows.size) * 100) : 100}%`;
    count.textContent = `${done} / ${rows.size}`;
  };
  const set = (id, state, err) => {
    const r = rows.get(id);
    if (!r) return;
    r.state = state;
    r.note.textContent = err ?? "";
    r.note.className = state === "error" ? "small sev-error" : "small muted";
    render();
  };
  const off = opId ? panel.onOp(opId, (ev) => set(ev.item, ev.state, ev.error)) : () => {};
  render();
  const el = h("div", {}, h("div", { class: "row" }, h("span", { class: "progress" }, bar), count), list);
  return { el, set, close: off, finish: () => { for (const [id, r] of rows) if (r.state !== "error") set(id, "done"); off(); } };
}

// Abschluss-Zeile für Fortschritts-Dialoge
export function doneBar(bad, total, verb) {
  return h("div", { class: `donebar ${bad ? "bad" : "ok"}` }, icon(bad ? MDI.alert : MDI.ok, 18),
    bad ? `Fertig – ${bad} von ${total} mit Fehlern` : `Fertig – ${total === 1 ? verb : `alle ${total} ${verb}`}`);
}

export const newOpId = () => Math.random().toString(36).slice(2, 10);

const loading = (text) => h("p", { class: "row muted" }, spinner(16), text);

function opText(op) {
  if (op.target === "shelly") return [op.old || "(leer)", op.new || "(leer)"];
  const f = (x) => `${x.area ? `${x.area} · ` : ""}${x.name ?? "–"}`;
  return [f(op.old), f(op.new)];
}

// ── Namen übernehmen ────────────────────────────────────────────────────────
export async function namesDialog(panel, items, title) {
  const dlg = modal(panel, { title, body: loading("Berechne Änderungen …"), wide: true, actions: [{ label: "Abbrechen" }] });
  let res;
  try {
    res = await panel.hass.callWS({ type: "shelly_manager/names/preview", items });
  } catch (err) {
    dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    return;
  }
  const { ops, skipped } = res;
  const checked = new Set(ops.map((_, i) => i));
  const count = h("span");
  const ruleCount = ops.filter((o) => o.rules?.length).length;
  const update = () => {
    count.textContent = `${checked.size} Änderung${checked.size === 1 ? "" : "en"} ${ruleCount ? "trotzdem " : ""}anwenden`;
    dlg.setActions([{ label: "Abbrechen" }, { label: count.textContent, primary: true, disabled: !checked.size, onclick: apply }]);
  };
  const groups = new Map();
  ops.forEach((op, i) => {
    if (!groups.has(op.mac)) groups.set(op.mac, []);
    groups.get(op.mac).push([op, i]);
  });
  const list = h("div", { class: "oplist" });
  for (const [mac, entries] of groups) {
    list.append(h("div", { class: "opdev" }, entries[0][0].device ?? mac));
    for (const [op, i] of entries) {
      const cb = h("input", { type: "checkbox", checked: true });
      cb.addEventListener("change", () => { cb.checked ? checked.add(i) : checked.delete(i); update(); });
      const [a, b] = opText(op);
      const rules = op.rules ?? [];
      list.append(h("label", { class: "op" }, cb,
        h("span", { class: "tag" }, op.target === "shelly" ? "Shelly" : "HA"),
        h("span", { class: "muted small oplbl" }, op.label),
        h("span", { class: "opold" }, a), icon(MDI.expand, 16),
        h("span", { class: "opnew" }, b, rules.length ? h("span", { class: "warnmark sev-warning", style: { marginLeft: "6px" },
          title: ["Namensregeln", ...rules.map((x) => x.text)].join("\n") }, icon(MDI.alert, 14)) : null)));
    }
  }
  const body = h("div", {},
    ruleCount ? h("p", { class: "small sev-warning row" }, icon(MDI.alert, 16),
      `${ruleCount} neue Name${ruleCount === 1 ? "" : "n"} verletzen die Namensregeln (Details per Hover). Du kannst trotzdem anwenden.`) : null,
    ops.length ? list : h("p", { class: "muted" }, "Nichts zu ändern – Namen stimmen bereits überein."),
    skipped.length ? h("details", { class: "skipped" }, h("summary", {}, `${skipped.length} übersprungen`),
      skipped.map((s) => h("div", { class: "small" }, h("b", {}, s.device ?? s.mac), ` ${s.label ?? ""}: ${s.reason}`))) : null,
    ops.some((o) => o.target === "shelly")
      ? h("p", { class: "muted small" }, "Nach Änderungen im Shelly lädt die Shelly-Integration das Gerät kurz neu.") : null);
  dlg.setBody(body);
  update();

  async function apply() {
    // Zurück in Aufträge pro Gerät und Richtung, nur mit den angehakten Kanälen
    const byKey = new Map();
    for (const i of checked) {
      const op = ops[i];
      const k = `${op.mac}|${op.direction}`;
      if (!byKey.has(k)) byKey.set(k, { mac: op.mac, direction: op.direction, keys: [], only: [] });
      const it = byKey.get(k);
      if (!it.keys.includes(op.key)) it.keys.push(op.key);
      it.only.push(`${op.key}|${op.target}`);
    }
    const macs = [...new Set([...checked].map((i) => ops[i].mac))];
    const opId = newOpId();
    const prog = progressList(panel, macs.map((m) => ({ id: m, label: panel.byMac.get(m)?.name ?? m })), opId);
    dlg.setBody(h("div", {}, h("p", { class: "small muted" }, "Schreibe Namen …"), prog.el));
    dlg.setActions([]);
    let out;
    try {
      out = await panel.hass.callWS({ type: "shelly_manager/names/apply", items: [...byKey.values()], op_id: opId });
      prog.close();
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
      dlg.setActions([{ label: "Schließen" }]);
      return;
    }
    const ok = out.results.filter((r) => r.ok), bad = out.results.filter((r) => !r.ok);
    const doneMacs = [...new Set(out.results.map((r) => r.mac))];
    dlg.setBody(h("div", {},
      h("p", { class: "row" }, icon(MDI.ok, 18, "ok"), `${ok.length} Änderung${ok.length === 1 ? "" : "en"} übernommen.`),
      bad.length ? h("div", { class: "plist" }, bad.map((r) => h("div", { class: "pitem sev-error" }, icon(MDI.error, 16),
        `${r.device} – ${r.label}: ${r.error}`))) : null,
      h("p", { class: "muted small" }, "Entity-IDs entstehen in HA aus Bereich und Gerätename. Nach einer Umbenennung lohnt sich ein Blick, ob sie noch passen.")));
    dlg.setActions([{ label: "Schließen" }, { label: "Entity-IDs prüfen", primary: true, onclick: () => setTimeout(() => entityDialog(panel, doneMacs), 0) }]);
  }
}

// ── Entity-IDs neu erzeugen ──────────────────────────────────────────────────
export async function entityDialog(panel, macs) {
  const dlg = modal(panel, { title: "Entity-IDs neu erzeugen", body: loading("Frage Home Assistant nach den automatischen IDs …"), wide: true,
    actions: [{ label: "Abbrechen" }] });
  const rows = macs.map((m) => panel.byMac.get(m)).filter(Boolean);
  const owner = new Map();
  for (const r of rows) for (const e of r.entity_ids) owner.set(e, r.name);
  let auto;
  try {
    // Dieselbe Berechnung wie »Entitäts-IDs neu erstellen« im Gerätedialog von HA
    auto = await panel.hass.callWS({ type: "config/entity_registry/get_automatic_entity_ids", entity_ids: [...owner.keys()] });
  } catch (err) {
    dlg.setBody(h("p", { class: "sev-error" }, `Home Assistant kann keine IDs vorschlagen: ${err.message ?? err}`));
    return;
  }
  const changes = Object.entries(auto).filter(([oldId, newId]) => newId && newId !== oldId).map(([oldId, newId]) => ({ oldId, newId, uses: null }));
  if (!changes.length) {
    dlg.setBody(h("p", { class: "row" }, icon(MDI.ok, 18, "ok"), `Alle ${owner.size} Entity-IDs passen bereits zu Bereich und Namen.`));
    dlg.setActions([{ label: "Schließen" }]);
    return;
  }
  dlg.setBody(loading(`${changes.length} abweichende IDs – suche Verwendungen in YAML, Dashboards, Energie und Helfern …`));
  try {
    const { counts } = await panel.hass.callWS({ type: "shelly_manager/refs/count", ids: changes.map((c) => c.oldId) });
    for (const c of changes) c.uses = counts[c.oldId] ?? { count: 0, sources: [] };
  } catch {
    for (const c of changes) c.uses = null;
  }

  // Verweise schreibt der Manager anschließend mit um – deshalb alles vorausgewählt
  const checked = new Set(changes.map((c) => c.oldId));
  const update = () => dlg.setActions([{ label: "Abbrechen" },
    { label: `${checked.size} umbenennen`, primary: true, disabled: !checked.size, onclick: apply }]);
  const list = h("div", { class: "oplist" });
  let lastOwner = null;
  for (const c of changes) {
    const name = owner.get(c.oldId);
    if (name !== lastOwner) { list.append(h("div", { class: "opdev" }, name)); lastOwner = name; }
    const cb = h("input", { type: "checkbox", checked: checked.has(c.oldId) });
    cb.addEventListener("change", () => { cb.checked ? checked.add(c.oldId) : checked.delete(c.oldId); update(); });
    const uses = c.uses === null ? h("span", { class: "muted small" }, "?")
      : c.uses.count ? h("span", { class: "tag", title: c.uses.sources.join("\n") }, `${c.uses.count}× verwendet`)
        : h("span", { class: "muted small" }, "unbenutzt");
    list.append(h("label", { class: "op" }, cb, h("span", { class: "opold mono" }, c.oldId), icon(MDI.expand, 16),
      h("span", { class: "opnew mono" }, c.newId), uses));
  }
  dlg.setBody(h("div", {},
    h("p", { class: "small muted" }, "Der Verlauf bleibt erhalten. Verweise in Automationen, Skripten, Szenen, YAML, Dashboards, " +
      "Energie und Helfern bietet der Manager danach zum Umschreiben an – mit Vorschau, Sicherung und Rückgängig."),
    list));
  update();

  async function apply() {
    const todo = changes.filter((c) => checked.has(c.oldId));
    const prog = progressList(panel, todo.map((c) => ({ id: c.oldId, label: `${c.oldId} → ${c.newId}` })));
    dlg.setBody(h("div", {}, h("p", { class: "small muted" }, `Benenne ${todo.length} Entitäten um …`), prog.el));
    dlg.setActions([]);
    const errors = [];
    for (const c of todo) {
      prog.set(c.oldId, "running");
      try {
        await panel.hass.callWS({ type: "config/entity_registry/update", entity_id: c.oldId, new_entity_id: c.newId });
        prog.set(c.oldId, "done");
      } catch (err) {
        errors.push(`${c.oldId}: ${err.message ?? err}`);
        prog.set(c.oldId, "error", String(err.message ?? err));
      }
    }
    const done = todo.filter((c) => !errors.some((e) => e.startsWith(`${c.oldId}:`)));
    const used = done.filter((c) => c.uses?.count);
    dlg.setBody(h("div", {},
      h("p", { class: "row" }, icon(MDI.ok, 18, "ok"), `${todo.length - errors.length} von ${todo.length} Entity-IDs umbenannt.`),
      errors.length ? h("div", { class: "plist" }, errors.map((e) => h("div", { class: "pitem sev-error" }, icon(MDI.error, 16), e))) : null,
      used.length ? h("p", { class: "small" }, `${used.length} davon werden noch verwendet – als Nächstes die Verweise umschreiben.`) : null));
    dlg.setActions(used.length
      ? [{ label: "Später" }, { label: "Verweise umschreiben …", primary: true, onclick: () => {
        dlg.close();
        refsDialog(panel, Object.fromEntries(used.map((c) => [c.oldId, c.newId])), { title: "Verweise auf umbenannte Entitäten" });
      } }]
      : [{ label: "Schließen" }]);
  }
}

async function pool(items, n, fn) {
  let i = 0;
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => {
    while (i < items.length) await fn(items[i++]);
  }));
}

// ── Eingang ohne Namen: benennen oder deaktivieren ──────────────────────────
export function inputNameDialog(panel, row, item) {
  const area = item.ha_area ?? row.area ?? "";
  const field = h("input", { type: "text", value: area ? `${area} ` : "", style: { width: "100%" } });
  const hint = h("p", { class: "muted small" });
  const rulesEl = h("div", { class: "small sev-warning" });
  let issues = [];
  let checkT;
  const checkRules = () => {
    clearTimeout(checkT);
    checkT = setTimeout(async () => {
      const v = field.value.trim();
      issues = v ? (await panel.hass.callWS({ type: "shelly_manager/names/check", name: field.value, area, kind: "input" })).issues : [];
      rulesEl.replaceChildren(...(issues.length ? [h("div", { class: "row" }, icon(MDI.alert, 14), "Namensregeln:"),
        h("ul", { class: "steps" }, issues.map((x) => h("li", {}, x.text)))] : []));
      dlg.setActions([{ label: "Abbrechen" }, { label: issues.length ? "Trotzdem speichern" : "Speichern", primary: true, onclick: save }]);
    }, 250);
  };
  field.addEventListener("input", checkRules);
  const upd = () => {
    const v = field.value.trim();
    const pure = area && v.toLowerCase().startsWith(area.toLowerCase() + " ") ? v.slice(area.length).trim() : v;
    hint.textContent = v ? `Shelly: »${v}« · HA-Entity: »${pure}«${area ? ` im Bereich ${area}` : ""}` : "";
  };
  field.addEventListener("input", upd);
  upd();
  const dlg = modal(panel, {
    title: `Eingang ${item.key} benennen`,
    body: h("div", { class: "form" }, h("p", { class: "small" }, `${row.name}: Voller Name inklusive Bereich, wie er im Shelly stehen soll.`), field, hint, rulesEl),
    actions: [{ label: "Abbrechen" }, { label: "Speichern", primary: true, onclick: save }],
  });
  setTimeout(() => { field.focus(); field.setSelectionRange(field.value.length, field.value.length); }, 0);
  checkRules();
  field.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });

  async function save() {
    const name = field.value.trim();
    if (!name) return;
    dlg.setBody(loading("Schreibe Namen …"));
    dlg.setActions([]);
    try {
      await panel.hass.callWS({ type: "shelly_manager/input/set", mac: row.mac, key: item.key, name });
      dlg.close();
      panel.toast(`Eingang ${item.key} heißt jetzt »${name}«.`, "ok");
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
      dlg.setActions([{ label: "Schließen" }]);
    }
  }
}

export function inputDisableDialog(panel, row, item) {
  const dlg = modal(panel, {
    title: `Eingang ${item.key} deaktivieren`,
    body: h("div", {},
      h("p", {}, `${row.name}: Eingang ${item.key} wird im Shelly abgeschaltet.`),
      h("p", { class: "muted small" }, "Tastendrücke an diesem Eingang lösen danach nichts mehr aus. In HA wird die zugehörige Entität nicht mehr aktualisiert. Rückgängig machen geht im Webinterface des Shelly (Eingang wieder aktivieren).")),
    actions: [{ label: "Abbrechen" }, { label: "Deaktivieren", primary: true, onclick: run }],
  });

  async function run() {
    dlg.setBody(loading("Deaktiviere Eingang …"));
    dlg.setActions([]);
    try {
      await panel.hass.callWS({ type: "shelly_manager/input/set", mac: row.mac, key: item.key, enable: false });
      dlg.close();
      panel.toast(`Eingang ${item.key} deaktiviert.`, "ok");
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
      dlg.setActions([{ label: "Schließen" }]);
    }
  }
}

// ── »Schalter als Licht«-Helfer durch Gerätetyp »light« ersetzen ─────────────
export async function helperDialog(panel, helperEntityId) {
  const dlg = modal(panel, { title: "Helfer durch Gerätetyp ersetzen", body: loading("Prüfe Helfer …"), wide: true, actions: [{ label: "Abbrechen" }] });
  let info, uses = [];
  try {
    info = await panel.hass.callWS({ type: "shelly_manager/helper/info", entity_id: helperEntityId });
    const rel = await panel.hass.callWS({ type: "search/related", item_type: "entity", item_id: info.source });
    uses = Object.entries(USAGE_TYPES).flatMap(([k, label]) => (rel[k] ?? []).map((id) => `${label}: ${id}`));
  } catch (err) {
    dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    return;
  }
  const rm = h("input", { type: "checkbox", checked: !uses.length });
  dlg.setBody(h("div", { class: "form" },
    h("p", {}, `${helperEntityId} schaltet ${info.source} als Licht. Stattdessen setzt der Manager im Shelly den Gerätetyp von ${info.key} auf »light« – HA legt dann selbst eine Licht-Entity an.`),
    h("ol", { class: "small steps" },
      h("li", {}, `Gerätetyp ${info.key} im Shelly → »light« (Konfiguration wird vorher gesichert)`),
      h("li", {}, `Helfer ${helperEntityId} entfernen`),
      h("li", {}, "Shelly-Gerät in HA neu laden"),
      h("li", {}, `Neue Licht-Entity übernimmt ID ${helperEntityId}, Name, Bereich, Symbol, Labels und Aliasse des Helfers – Automationen und Dashboards funktionieren weiter`)),
    h("label", { class: "row small" }, rm, `Verwaiste Schalter-Entity ${info.source} danach entfernen`),
    uses.length ? h("div", { class: "small sev-warning" }, `${info.source} wird noch verwendet – diese Verweise müssen auf ${helperEntityId} umgestellt werden:`,
      h("ul", {}, uses.map((u) => h("li", {}, u)))) : h("p", { class: "muted small" }, `${info.source} wird nirgends direkt verwendet.`)));
  dlg.setActions([{ label: "Abbrechen" }, { label: "Umstellen", primary: true, onclick: run }]);

  async function run() {
    dlg.setBody(loading("Stelle um … (das Neuladen dauert ein paar Sekunden)"));
    dlg.setActions([]);
    try {
      const res = await panel.hass.callWS({ type: "shelly_manager/helper/convert", entity_id: helperEntityId, remove_switch: rm.checked });
      dlg.setBody(h("div", {}, h("p", { class: `row ${res.ok ? "ok" : "sev-warning"}` }, icon(res.ok ? MDI.ok : MDI.alert, 18), res.ok ? "Umgestellt." : "Teilweise umgestellt."),
        h("ul", { class: "small" }, res.steps.map((s) => h("li", {}, s)))));
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    }
    dlg.setActions([{ label: "Schließen" }]);
  }
}

// ── Manuell umbenennen: Bereich + Name (Shelly bekommt beides, HA den reinen Namen + Bereich) ──
export function renameItemDialog(panel, row, item) {
  const areas = panel.areas ?? [];
  const isDevice = item.key === "device";
  const curArea = areas.find((a) => a.name === (item.ha_area ?? row.area));
  const strip = (n, a) => (a && (n ?? "").toLowerCase().startsWith(a.toLowerCase() + " ") ? n.slice(a.length).trim() : (n ?? "").trim());
  let areaId = curArea?.id ?? "";
  let name = strip(item.shelly ?? item.ha_name ?? "", curArea?.name);
  const areaName = () => areas.find((a) => a.id === areaId)?.name ?? "";
  const full = () => [areaName(), name.trim()].filter(Boolean).join(" ");
  const preview = h("div", { class: "small muted" });
  const rules = h("div", { class: "small" });
  let t;
  const upd = () => {
    preview.textContent = `Shelly: »${full()}« · HA: »${name.trim()}«${areaName() ? ` im Bereich ${areaName()}` : ""}`;
    clearTimeout(t);
    t = setTimeout(async () => {
      if (!name.trim()) { rules.replaceChildren(); return; }
      const kind = isDevice ? row.kind : item.kind === "input" ? "input" : "channel";
      const res = await panel.hass.callWS({ type: "shelly_manager/names/check", name: full(), area: areaName(), kind });
      rules.replaceChildren(res.issues.length
        ? h("span", { class: "sev-warning" }, icon(MDI.alert, 14), " ", res.issues.map((x) => x.text).join(" · "))
        : h("span", { class: "ok" }, icon(MDI.ok, 14), " entspricht den Namensregeln"));
    }, 250);
  };
  const sel = h("select", { onchange: (e) => { areaId = e.target.value; upd(); } },
    h("option", { value: "", selected: !areaId }, "(kein Bereich)"),
    areas.map((a) => h("option", { value: a.id, selected: a.id === areaId }, a.name)));
  const input = h("input", { type: "text", value: name, oninput: (e) => { name = e.target.value; upd(); } });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
  const dlg = modal(panel, {
    title: isDevice ? `${row.name} umbenennen` : `${item.kind === "input" ? "Eingang" : "Kanal"} ${item.key} umbenennen`,
    body: h("div", { class: "form" },
      h("label", { class: "field" }, h("span", { class: "flabel" }, "Bereich"), sel),
      h("label", { class: "field" }, h("span", { class: "flabel" }, "Name (ohne Bereich)"), input),
      preview, rules),
    actions: [{ label: "Abbrechen" }, { label: "Umbenennen", primary: true, onclick: save }],
  });
  setTimeout(() => { input.focus(); input.select(); }, 0);
  upd();

  async function save() {
    if (!name.trim()) return;
    dlg.setBody(loading("Schreibe Namen …"));
    dlg.setActions([]);
    try {
      await panel.hass.callWS({ type: "shelly_manager/names/set", mac: row.mac, key: item.key, name: full(), area_id: areaId || null });
      dlg.close();
      panel.toast(`Umbenannt in »${full()}«`, "ok");
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
      dlg.setActions([{ label: "Schließen" }]);
    }
  }
}

// Neustart mit Bestätigung, Fortschritt zum Abhaken und Kontrolle, ob »Neustart nötig« danach weg ist
export function rebootDialog(panel, macs, reason = null) {
  const names = macs.map((m) => panel.byMac.get(m)?.name ?? m);
  const dlg = modal(panel, {
    title: macs.length === 1 ? `${names[0]} neu starten` : `${macs.length} Geräte neu starten`,
    body: h("div", { class: "form" },
      reason ? h("p", { class: "small" }, reason) : null,
      h("p", { class: "small muted" }, "Geräte werden nacheinander neu gestartet; der Manager wartet jeweils, bis das Gerät zurück ist, " +
        "und prüft, ob die Änderungen übernommen wurden. Ausgänge verhalten sich dabei wie nach einem Stromausfall."),
      macs.length > 1 ? h("ul", { class: "small steps" }, names.map((n) => h("li", {}, n))) : null),
    actions: [{ label: "Abbrechen" }, { label: "Neu starten", primary: true, onclick: run }],
  });

  async function run() {
    const opId = newOpId();
    const prog = progressList(panel, macs.map((m, i) => ({ id: m, label: names[i] })));
    dlg.setBody(h("div", {}, h("p", { class: "small muted" }, "Starte neu und warte auf die Geräte …"), prog.el));
    dlg.setActions([{ label: "Im Hintergrund weiterlaufen lassen" }]);
    let done = 0;
    let bad = 0;
    const off = panel.onOp(opId, (ev) => {
      prog.set(ev.item, ev.state, ev.error);
      if (ev.state === "done" || ev.state === "error") done++;
      if (ev.state === "error") bad++;
      if (done >= macs.length) {
        off();
        dlg.appendBody(doneBar(bad, macs.length, "neu gestartet"));
        dlg.setActions([{ label: "Schließen", primary: true }]);
      }
    });
    try {
      await panel.hass.callWS({ type: "shelly_manager/reboot", macs, op_id: opId });
    } catch (err) {
      off();
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
      dlg.setActions([{ label: "Schließen" }]);
    }
  }
}
