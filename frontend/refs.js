// Verweise auf Entitäts-IDs umschreiben: Vorschau je Fundstelle, Auswahl, Sicherung, Rückgängig
import { modal, newOpId, progressList } from "./dialogs.js";
import { MDI, h, icon, spinner } from "./util.js";

const KIND = { yaml: "YAML-Datei", dashboard: "Dashboard", energy: "Energie", stromnetz: "Stromnetz", entry: "Helfer / Integration", storage: "Andere Integration" };
const loading = (t) => h("p", { class: "row muted" }, spinner(16), t);

// Dialog neu zeichnen, ohne dass die Ansicht nach oben springt
function keepScroll(panel, render) {
  const root = panel.shadowRoot;
  const top = root.querySelector(".modal .mbody")?.scrollTop;
  const lists = [...root.querySelectorAll(".modal .reflist")].map((l) => l.scrollTop);
  render();
  const body = root.querySelector(".modal .mbody");
  if (body && top != null) body.scrollTop = top;
  root.querySelectorAll(".modal .reflist").forEach((l, i) => { if (lists[i] != null) l.scrollTop = lists[i]; });
}

export async function refsDialog(panel, mapping, { title = "Verweise umschreiben", intro = null } = {}) {
  const dlg = modal(panel, { title, wide: true, body: loading("Suche Verweise in YAML, Dashboards, Energie und Helfern …"), actions: [{ label: "Abbrechen" }] });
  let plan;
  try {
    plan = await panel.hass.callWS({ type: "shelly_manager/refs/scan", mapping });
  } catch (err) {
    dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    return;
  }
  if (!plan.total) {
    dlg.setBody(h("div", {}, intro, h("p", { class: "row" }, icon(MDI.ok, 18, "ok"), "Keine Verweise gefunden – nichts zu tun.")));
    dlg.setActions([{ label: "Schließen" }]);
    return;
  }
  const sel = new Set();
  for (const s of plan.sources) if (s.writable) for (const f of s.findings) sel.add(f.fid);
  const open = new Set(plan.sources.length <= 3 ? plan.sources.map((s) => s.key) : []);

  const draw = () => keepScroll(panel, render);
  const render = () => {
    const writable = plan.sources.filter((s) => s.writable);
    const ro = plan.sources.filter((s) => !s.writable);
    dlg.setBody(h("div", {},
      intro,
      h("p", { class: "small muted" }, `${plan.total} Fundstellen in ${plan.sources.length} Quellen. Vor dem Schreiben wird alles gesichert; ` +
        "danach werden Automationen/Skripte/Szenen bzw. YAML neu geladen und Helfer neu gestartet."),
      writable.map(source),
      ro.length ? h("div", {}, h("p", { class: "small sev-warning", style: { marginTop: "12px" } }, "Nur zur Info – hier ändert der Manager nichts:"), ro.map(source)) : null));
    const n = sel.size;
    dlg.setActions([{ label: "Abbrechen" }, { label: `${n} Verweis${n === 1 ? "" : "e"} umschreiben`, primary: true, disabled: !n, onclick: apply }]);
  };

  function source(s) {
    const picked = s.findings.filter((f) => sel.has(f.fid)).length;
    const all = s.writable && picked === s.findings.length;
    const cb = h("input", { type: "checkbox", checked: all, disabled: !s.writable, onchange: (e) => {
      for (const f of s.findings) e.target.checked ? sel.add(f.fid) : sel.delete(f.fid);
      draw();
    } });
    if (s.writable && picked && !all) cb.indeterminate = true;
    const isOpen = open.has(s.key);
    return h("div", { class: "refsrc" },
      h("div", { class: "row" }, cb,
        h("button", { class: "linkish grow", onclick: () => { isOpen ? open.delete(s.key) : open.add(s.key); draw(); } },
          icon(isOpen ? MDI.collapse : MDI.expand, 16), h("b", {}, s.label),
          h("span", { class: "muted small" }, ` · ${KIND[s.kind] ?? s.kind} · ${s.writable ? `${picked}/${s.findings.length}` : s.findings.length}`),
          s.note ? h("span", { class: "muted small" }, ` · ${s.note}`) : null)),
      isOpen ? h("div", { class: "reflist" }, s.findings.map((f) => h("label", { class: "refrow small" },
        h("input", { type: "checkbox", checked: sel.has(f.fid), disabled: !s.writable, onchange: (e) => { e.target.checked ? sel.add(f.fid) : sel.delete(f.fid); draw(); } }),
        h("span", { class: "muted mono refwhere" }, f.line ? `Z. ${f.line}` : (f.where ?? "")),
        h("span", { class: "mono grow refctx" },
          h("span", { class: "muted" }, f.before ?? ""), h("del", {}, f.old), " ", h("ins", {}, f.new), h("span", { class: "muted" }, f.after ?? ""))))) : null);
  }

  async function apply() {
    const writable = plan.sources.filter((s) => s.writable && s.findings.some((f) => sel.has(f.fid)));
    const opId = newOpId();
    const prog = progressList(panel, writable.map((s) => ({ id: s.key, label: s.label })));
    const off = panel.onOp(opId, (ev) => prog.set(ev.item, ev.state, ev.error));
    dlg.setBody(h("div", {}, h("p", { class: "small muted" }, "Sichere und schreibe …"), prog.el));
    dlg.setActions([]);
    let res;
    try {
      res = await panel.hass.callWS({ type: "shelly_manager/refs/apply", mapping, fids: [...sel], op_id: opId });
    } catch (err) {
      off();
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
      dlg.setActions([{ label: "Schließen" }]);
      return;
    }
    off();
    const bad = res.results.filter((r) => !r.ok);
    dlg.setBody(h("div", {}, prog.el,
      h("p", { class: `row ${bad.length ? "sev-warning" : "ok"}` }, icon(bad.length ? MDI.alert : MDI.ok, 18),
        `${res.results.length - bad.length} von ${res.results.length} Quellen geändert` +
        (res.reloaded.length ? ` · neu geladen: ${res.reloaded.map((r) => (r === "all" ? "alle YAML-Bereiche" : r === "stromnetz_generate" ? "Stromnetz neu erzeugt" : r)).join(", ")}` : "")),
      h("p", { class: "small muted" }, res.remaining ? `Noch ${res.remaining} Verweise auf die alten IDs (nicht ausgewählt oder nur zur Info).`
        : "Keine Verweise auf die alten IDs mehr vorhanden.")));
    dlg.setActions([
      res.backup_id ? { label: "Rückgängig", onclick: () => undo(res.backup_id) } : null,
      { label: "Schließen", primary: true },
    ].filter(Boolean));
  }

  async function undo(id) {
    dlg.setBody(loading("Stelle die Sicherung wieder her …"));
    dlg.setActions([]);
    try {
      const r = await panel.hass.callWS({ type: "shelly_manager/refs/undo", backup_id: id });
      dlg.setBody(h("p", { class: "row ok" }, icon(MDI.ok, 18), `${r.restored} Quellen wiederhergestellt.`));
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    }
    dlg.setActions([{ label: "Schließen" }]);
  }

  draw();
}

// Ohne Gerätebezug: alte → neue IDs von Hand eintragen, plus Liste der Sicherungen
export async function refsManualDialog(panel) {
  const ta = h("textarea", { rows: 6, style: { width: "100%" }, placeholder: "switch.alt_name → light.neuer_name\nsensor.a  sensor.b" });
  const list = h("div", { class: "small" }, loading("Lade Sicherungen …"));
  const dlg = modal(panel, { title: "Verweise umschreiben", wide: true,
    body: h("div", { class: "form" },
      h("p", { class: "small muted" }, "Eine Zuordnung pro Zeile: alte ID, dann neue ID (getrennt durch →, Leerzeichen, Komma oder =)."),
      ta, h("h4", {}, "Letzte Änderungen"), list),
    actions: [{ label: "Abbrechen" }, { label: "Verweise suchen …", primary: true, onclick: () => {
      const mapping = {};
      for (const line of ta.value.split("\n")) {
        const m = line.trim().match(/^([a-z_]+\.[a-z0-9_]+)\s*(?:→|->|=|,|\s)\s*([a-z_]+\.[a-z0-9_]+)$/);
        if (m) mapping[m[1]] = m[2];
      }
      if (!Object.keys(mapping).length) { panel.toast("Keine gültige Zuordnung erkannt"); return; }
      dlg.close();
      refsDialog(panel, mapping);
    } }] });
  try {
    const { backups } = await panel.hass.callWS({ type: "shelly_manager/refs/backups" });
    list.replaceChildren(...(backups.length ? backups.map((b) => h("div", { class: "row" },
      h("span", { class: "grow" }, `${new Date(b.ts * 1000).toLocaleString()} · ${Object.keys(b.mapping ?? {}).length} IDs · ${b.items} Quellen`,
        h("div", { class: "muted mono" }, Object.entries(b.mapping ?? {}).slice(0, 3).map(([a, c]) => `${a} → ${c}`).join(", "))),
      h("button", { class: "btn", onclick: async () => {
        if (!confirm("Diesen Stand wiederherstellen?")) return;
        const r = await panel.hass.callWS({ type: "shelly_manager/refs/undo", backup_id: b.id });
        panel.toast(`${r.restored} Quellen wiederhergestellt`, "ok");
      } }, "Rückgängig"))) : [h("span", { class: "muted" }, "Noch keine.")]));
  } catch (err) {
    list.replaceChildren(h("span", { class: "sev-error" }, String(err.message ?? err)));
  }
}

// Verweise auf Entitäten, die es nicht (mehr) gibt – mit Vorschlag, dann weiter zur Umschreib-Vorschau
export async function orphansDialog(panel) {
  const dlg = modal(panel, { title: "Verwaiste Verweise", wide: true, body: loading("Suche Verweise auf nicht vorhandene Entitäten …"), actions: [{ label: "Abbrechen" }] });
  let res;
  try {
    res = await panel.hass.callWS({ type: "shelly_manager/refs/orphans" });
  } catch (err) {
    dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    return;
  }
  if (!res.orphans.length) {
    dlg.setBody(h("p", { class: "row" }, icon(MDI.ok, 18, "ok"), "Keine Verweise auf fehlende Entitäten gefunden."));
    dlg.setActions([{ label: "Schließen" }]);
    return;
  }
  const ids = Object.keys(panel.hass.states).sort();
  const listId = `ent-${Math.random().toString(36).slice(2, 8)}`;
  const target = new Map(res.orphans.filter((o) => o.suggestion).map((o) => [o.id, o.suggestion]));
  // Nur sichere Vorschläge vorauswählen (Registry, Messgröße umbenannt, gleicher Name); »ähnlich« selbst prüfen
  const sel = new Set(res.orphans.filter((o) => o.suggestion && o.sure && o.writable).map((o) => o.id));
  let onlyShelly = res.orphans.some((o) => o.shelly);

  const draw = () => keepScroll(panel, render);
  const render = () => {
    const rows = res.orphans.filter((o) => !onlyShelly || o.shelly);
    dlg.setBody(h("div", {},
      h("p", { class: "small muted" }, `${res.orphans.length} fehlende Entitäts-IDs mit ${res.total} Verweisen. Vorschläge stammen aus der ` +
        "Entitäts-Registry (gleiche Entität, neue ID), gleichem Namen in anderer Domain oder sehr ähnlichen IDs – bitte prüfen."),
      h("label", { class: "row small" }, h("input", { type: "checkbox", checked: onlyShelly, onchange: (e) => { onlyShelly = e.target.checked; draw(); } }),
        "Nur Shelly-bezogene"),
      h("datalist", { id: listId }, ids.map((i) => h("option", { value: i }))),
      h("div", { class: "reflist", style: { marginLeft: 0, maxHeight: "420px" } }, rows.map((o) => h("div", { class: "refrow small", style: { alignItems: "center" } },
        h("input", { type: "checkbox", checked: sel.has(o.id), disabled: !o.writable, title: o.writable ? "" : "nur in nicht änderbaren Quellen",
          onchange: (e) => { e.target.checked ? sel.add(o.id) : sel.delete(o.id); update(); } }),
        h("span", { class: "mono", style: { flex: "1 1 40%" }, title: o.sources.join("\n") }, o.id,
          h("div", { class: "muted" }, `${o.count}× · ${o.sources.slice(0, 2).join(", ")}${o.sources.length > 2 ? " …" : ""}`)),
        icon(MDI.expand, 16),
        h("span", { style: { flex: "1 1 45%" } },
          h("input", { type: "text", class: "mono", list: listId, value: target.get(o.id) ?? "", placeholder: "neue Entitäts-ID",
            style: { width: "100%" }, oninput: (e) => {
              const v = e.target.value.trim();
              v ? target.set(o.id, v) : target.delete(o.id);
              if (v && o.writable) sel.add(o.id); else sel.delete(o.id);
              update();
            } }),
          o.reason ? h("div", { class: o.sure ? "muted" : "sev-warning" }, o.reason) : null))))));
    update();
  };
  const ready = () => [...sel].filter((id) => target.get(id) && panel.hass.states[target.get(id)]);
  const update = () => {
    const n = ready().length;
    dlg.setActions([{ label: "Abbrechen" }, { label: `${n} weiter zur Vorschau …`, primary: true, disabled: !n, onclick: () => {
      const mapping = Object.fromEntries(ready().map((id) => [id, target.get(id)]));
      dlg.close();
      refsDialog(panel, mapping, { title: "Verwaiste Verweise umschreiben" });
    } }]);
  };
  draw();
}
