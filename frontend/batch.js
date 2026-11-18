// Batch-Einstellungen: zusammenstellen → Vorschau → gestaffelt anwenden → Neustart/Rückgängig
import { modal, rebootDialog } from "./dialogs.js";
import { MDI, h, icon, spinner } from "./util.js";

const STATUS = {
  change: { label: "ändern", cls: "tag upd" },
  same: { label: "bereits so", cls: "tag off" },
  unsupported: { label: "nicht unterstützt", cls: "tag off" },
  offline: { label: "keine Daten", cls: "tag bad" },
  sleeping: { label: "schläft", cls: "tag off" },
};
const DEV_STATE = {
  queued: [MDI.clock, "muted", "wartet"],
  running: [MDI.refresh, "spin", "läuft"],
  done: [MDI.ok, "ok", "fertig"],
  partial: [MDI.alert, "sev-warning", "teilweise"],
  error: [MDI.error, "sev-error", "Fehler"],
  failed: [MDI.error, "sev-error", "fehlgeschlagen"],
  preparing: [MDI.refresh, "spin", "bereitet vor"],
  updating: [MDI.update, "spin", "lädt Firmware"],
  rebooting: [MDI.refresh, "spin", "startet neu"],
  skipped: [MDI.ok, "muted", "übersprungen"],
  cancelled: [MDI.close, "muted", "abgebrochen"],
  interrupted: [MDI.alert, "sev-warning", "unterbrochen (HA-Neustart)"],
};
const RUNNING = ["queued", "running", "preparing", "updating", "rebooting"];

let catalog = null;
async function loadCatalog(hass) {
  if (!catalog) catalog = (await hass.callWS({ type: "shelly_manager/settings/catalog" })).settings;
  return catalog;
}

const loading = (text) => h("p", { class: "row muted" }, spinner(16), text);
const fmtVal = (v, s) => {
  if (v === null || v === undefined || v === "") return "–";
  if (typeof v === "boolean") return v ? "an" : "aus";
  const opt = s?.options?.find((o) => o.value === v);
  return opt ? opt.label : String(v);
};

export async function batchDialog(panel, macs, init = null) {
  const dlg = modal(panel, { title: `Einstellungen für ${macs.length} Geräte`, body: loading("Lade Einstellungen …"), wide: true });
  const cat = await loadCatalog(panel.hass);
  const byId = Object.fromEntries(cat.map((s) => [s.id, s]));
  const rows = [];

  const list = h("div", { class: "chg-list" });
  const addRow = (init = {}) => {
    const row = { id: init.id ?? cat[0].id, value: init.value, channel: init.channel ?? null };
    rows.push(row);
    const sel = h("select", {});
    const groups = [...new Set(cat.map((s) => s.group))];
    for (const g of groups) {
      sel.append(h("optgroup", { label: g }, cat.filter((s) => s.group === g).map((s) => h("option", { value: s.id, selected: s.id === row.id }, s.label))));
    }
    const valBox = h("span", { class: "chg-val" });
    const chBox = h("span", { class: "chg-ch" });
    const renderValue = () => {
      const s = byId[row.id];
      let input;
      if (s.type === "bool") {
        if (typeof row.value !== "boolean") row.value = true;
        input = h("span", { class: "segment" },
          [[true, "an"], [false, "aus"]].map(([v, l]) => h("button", { class: row.value === v ? "active" : "",
            onclick: () => { row.value = v; renderValue(); } }, l)));
      } else if (s.type === "enum") {
        if (!s.options.some((o) => o.value === row.value)) row.value = s.options[0]?.value ?? "";
        input = h("select", { onchange: (e) => { row.value = e.target.value; } },
          s.options.map((o) => h("option", { value: o.value, selected: o.value === row.value }, o.label)));
      } else {
        if (typeof row.value === "boolean") row.value = "";
        input = h("input", { type: s.type === "number" ? "number" : s.type === "password" ? "password" : "text",
          placeholder: s.placeholder ?? (s.type === "text" ? "leer = Feld leeren" : ""), value: row.value ?? "",
          oninput: (e) => { row.value = s.type === "number" ? e.target.value : e.target.value; } });
      }
      valBox.replaceChildren(input);
      chBox.replaceChildren(s.channel ? h("select", { onchange: (e) => { row.channel = e.target.value === "" ? null : Number(e.target.value); } },
        h("option", { value: "", selected: row.channel === null }, "alle Kanäle"),
        [0, 1, 2, 3].map((i) => h("option", { value: i, selected: row.channel === i }, `Kanal ${i}`))) : "");
    };
    sel.addEventListener("change", () => { row.id = sel.value; row.value = undefined; row.channel = null; renderValue(); });
    renderValue();
    const el = h("div", { class: "chg-row" }, sel, valBox, chBox,
      h("button", { class: "icon-btn sm", "aria-label": "Entfernen", onclick: () => { rows.splice(rows.indexOf(row), 1); el.remove(); } }, icon(MDI.close, 16)));
    list.append(el);
  };
  if (init?.length) init.forEach((c) => addRow(c));
  else addRow();

  const compose = () => {
    dlg.setBody(h("div", { class: "form" },
      h("p", { class: "muted small" }, "Einstellungen zusammenstellen. Die Vorschau zeigt pro Gerät, was sich ändert – geschrieben wird erst danach."),
      list,
      h("button", { class: "btn", style: { alignSelf: "flex-start" }, onclick: () => addRow() }, icon(MDI.tag, 16), "Einstellung hinzufügen")));
    dlg.setActions([{ label: "Abbrechen" }, { label: "Vorschau", primary: true, onclick: preview }]);
  };

  const changes = () => rows.map((r) => {
    const s = byId[r.id];
    const value = s.type === "number" ? (r.value === "" || r.value === undefined ? null : Number(r.value)) : r.value;
    return { id: r.id, value: value ?? null, ...(s.channel ? { channel: r.channel } : {}) };
  });

  async function preview() {
    const ch = changes();
    if (!ch.length) return;
    dlg.setBody(loading("Berechne Vorschau …"));
    dlg.setActions([]);
    let res;
    try {
      res = await panel.hass.callWS({ type: "shelly_manager/batch/preview", macs, changes: ch });
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
      dlg.setActions([{ label: "Zurück", onclick: compose }]);
      return;
    }
    const withChange = res.devices.filter((d) => d.items.some((i) => i.status === "change"));
    const chosen = new Set(withChange.map((d) => d.mac));
    const listEl = h("div", { class: "oplist" });
    for (const d of res.devices) {
      const r = panel.byMac.get(d.mac);
      const has = d.items.some((i) => i.status === "change");
      const cb = h("input", { type: "checkbox", checked: has, disabled: !has });
      cb.addEventListener("change", () => { cb.checked ? chosen.add(d.mac) : chosen.delete(d.mac); upd(); });
      listEl.append(h("label", { class: "opdev row" }, cb, r?.name ?? d.mac, h("span", { class: "muted small" }, r?.model ?? "")));
      for (const it of d.items) {
        const s = byId[it.setting];
        const st = STATUS[it.status] ?? STATUS.unsupported;
        listEl.append(h("div", { class: "bitem" },
          h("span", { class: "muted small" }, it.key.includes(":") ? it.key : ""),
          h("span", {}, it.label),
          h("span", { class: it.status === "change" ? "opold" : "muted" }, it.secret ? "•••" : fmtVal(it.old, s)), icon(MDI.expand, 14),
          h("span", { class: "opnew" }, it.secret ? "•••" : fmtVal(it.new, s)),
          h("span", { class: st.cls, title: it.reason ?? "" }, st.label)));
      }
    }
    const warn = changes().some((c) => ["sta1_enable", "sta1_ssid", "sta1_pass", "ap_enable"].includes(c.id))
      ? h("p", { class: "small sev-warning" }, "WLAN-Änderungen: Geräte werden einzeln nacheinander geschrieben; prüfe danach die Erreichbarkeit.") : null;
    const upd = () => dlg.setActions([{ label: "Zurück", onclick: compose },
      { label: `Auf ${chosen.size} Gerät${chosen.size === 1 ? "" : "e"} anwenden`, primary: true, disabled: !chosen.size, onclick: () => apply([...chosen], ch) }]);
    dlg.setBody(h("div", {}, h("p", { class: "small" }, `${withChange.length} von ${res.devices.length} Geräten würden geändert.`), warn, listEl));
    upd();
  }

  async function apply(targets, ch) {
    dlg.setBody(loading("Starte …"));
    dlg.setActions([]);
    const label = ch.map((c) => `${byId[c.id].label} → ${byId[c.id].secret ? "•••" : fmtVal(c.value, byId[c.id])}`).join(", ");
    try {
      const { job_id } = await panel.hass.callWS({ type: "shelly_manager/batch/apply", macs: targets, changes: ch, label });
      jobView(panel, dlg, job_id, byId);
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
      dlg.setActions([{ label: "Zurück", onclick: compose }]);
    }
  }

  compose();
}

// Fortschritt eines Auftrags; aktualisiert sich über die Panel-Events
export function jobView(panel, dlg, jobId, byId) {
  const render = (job) => {
    if (!job) return;
    const devs = Object.entries(job.devices);
    const done = devs.filter(([, d]) => !RUNNING.includes(d.state)).length;
    const fw = job.kind === "firmware";
    const restart = devs.filter(([, d]) => d.restart_required).map(([m]) => m);
    dlg.setBody(h("div", {},
      h("p", { class: "small" }, job.label),
      h("div", { class: "row" }, h("span", { class: "progress" }, h("i", { style: { width: `${Math.round((done / devs.length) * 100)}%` } })),
        h("span", { class: "muted small" }, `${done} / ${devs.length}`)),
      h("div", { class: "oplist" }, devs.map(([mac, d]) => {
        const [ic, cls, label] = DEV_STATE[d.state] ?? DEV_STATE.queued;
        const r = panel.byMac.get(mac);
        const errs = (d.items ?? []).filter((i) => i.ok === false).map((i) => `${i.label}: ${i.error}`);
        return h("div", { class: "jobdev" }, h("span", { class: cls === "spin" ? "" : cls, title: label }, cls === "spin" ? spinner(15) : icon(ic, 16)),
          h("span", { class: "grow" }, r?.name ?? mac),
          fw ? h("span", { class: "muted small" }, label) : null,
          fw && (d.old || d.target) ? h("span", { class: "mono small" }, `${d.old ?? "?"} → ${d.new ?? d.target ?? "?"}`) : null,
          fw && d.protected ? h("span", { class: "tag", title: "Ausgänge vorübergehend auf »letzter Zustand« gestellt" }, `${d.protected} gesichert`) : null,
          (d.warnings ?? []).length ? h("span", { class: "sev-warning small", title: d.warnings.join("\n") }, icon(MDI.alert, 14)) : null,
          d.restart_required ? h("span", { class: "tag bad" }, "Neustart nötig") : null,
          d.error || errs.length ? h("span", { class: "sev-error small", title: [d.error, ...errs].filter(Boolean).join("\n") }, d.error ?? `${errs.length} Fehler`) : null);
      }))));
    const finished = job.state !== "running";
    if (finished) {
      const bad = devs.filter(([, d]) => d.error || (d.items ?? []).some((i) => i.ok === false) || d.state === "error" || d.state === "failed").length;
      dlg.appendBody?.(h("div", { class: `donebar ${bad ? "bad" : "ok"}` }, icon(bad ? MDI.alert : MDI.ok, 18),
        bad ? `Fertig – ${bad} von ${devs.length} Gerät(en) mit Fehlern` : `Fertig – ${devs.length === 1 ? "übernommen" : `alle ${devs.length} Geräte übernommen`}` +
          (restart.length ? `, ${restart.length} brauchen einen Neustart` : "")));
    }
    const acts = [{ label: "Schließen", primary: finished && !restart.length }];
    if (finished && !job.undo_of && !fw) acts.unshift({ label: "Rückgängig", onclick: () => undo(job.id) });
    if (!finished && fw) {
      dlg.setActions([{ label: "Schließen (läuft weiter)" }, { label: "Restliche abbrechen", onclick: async () => {
        await panel.hass.callWS({ type: "shelly_manager/firmware/cancel", job_id: job.id });
        panel.toast("Laufende Updates werden noch fertig, wartende nicht mehr gestartet.");
      } }]);
      return;
    }
    if (finished && restart.length) acts.push({ label: `${restart.length} neu starten`, primary: true, onclick: () => reboot(restart) });
    dlg.setActions(finished ? acts : []);
  };

  async function undo(id) {
    try {
      const { job_id } = await panel.hass.callWS({ type: "shelly_manager/batch/undo", job_id: id });
      jobView(panel, dlg, job_id, byId);
    } catch (err) {
      panel.toast(err.message ?? String(err), "error");
    }
  }

  function reboot(list) {
    dlg.close();
    rebootDialog(panel, list, "Diese Geräte melden »Neustart nötig«, damit die geänderten Einstellungen wirksam werden.");
  }

  const prev = dlg.onClose;
  const off = panel.onJob((job) => { if (job.id === jobId) render(job); });
  dlg.onClose = () => { off(); prev?.(); };
  panel.hass.callWS({ type: "shelly_manager/jobs" }).then((r) => render(r.jobs.find((j) => j.id === jobId)));
}

export async function jobsDialog(panel) {
  const dlg = modal(panel, { title: "Verlauf der Änderungen", body: loading("Lade …"), wide: true, actions: [{ label: "Schließen" }] });
  const { jobs } = await panel.hass.callWS({ type: "shelly_manager/jobs" });
  const byId = Object.fromEntries((await loadCatalog(panel.hass)).map((s) => [s.id, s]));
  if (!jobs.length) {
    dlg.setBody(h("p", { class: "muted" }, "Noch keine Änderungen über den Shelly Manager."));
    return;
  }
  dlg.setBody(h("div", { class: "oplist" }, jobs.map((j) => {
    const n = Object.keys(j.devices).length;
    const [ic, cls] = DEV_STATE[j.state] ?? DEV_STATE.done;
    return h("div", { class: "jobdev" }, h("span", { class: cls }, icon(ic, 16)),
      h("span", { class: "grow" }, h("div", {}, j.label), h("div", { class: "muted small" }, `${new Date(j.ts * 1000).toLocaleString("de-AT")} · ${n} Geräte${j.undo_of ? " · Rückgängig" : ""}`)),
      h("button", { class: "btn", style: { height: "28px" }, onclick: () => jobView(panel, dlg, j.id, byId) }, "Details"));
  })));
}

// Kanal-Einstellungen eines Geräts (eigener kleiner Batch-Auftrag mit Fortschritt)
export async function channelSettingsDialog(panel, row, ch) {
  const byId = Object.fromEntries((await loadCatalog(panel.hass)).map((s) => [s.id, s]));
  const st = { initial_state: ch.initial_state, in_mode: ch.in_mode, consumption_type: ch.consumption_type ?? "",
    auto_off: !!ch.auto_off, auto_off_delay: ch.auto_off_delay ?? 60 };
  const orig = { ...st };
  const sel = (sid) => h("select", { onchange: (e) => { st[sid] = e.target.value; } },
    byId[sid].options.map((o) => h("option", { value: o.value, selected: o.value === (st[sid] ?? "") }, o.label)));
  const delay = h("input", { type: "number", min: 1, value: st.auto_off_delay, oninput: (e) => { st.auto_off_delay = Number(e.target.value); } });
  const autoOff = h("input", { type: "checkbox", checked: st.auto_off, onchange: (e) => { st.auto_off = e.target.checked; } });
  const dlg = modal(panel, { title: `${row.name} – ${ch.key}`, body: h("div", { class: "form" },
    st.initial_state != null ? h("label", { class: "field" }, h("span", { class: "flabel" }, "Nach Stromausfall"), sel("initial_state")) : null,
    st.in_mode ? h("label", { class: "field" }, h("span", { class: "flabel" }, "Eingangsmodus"), sel("in_mode")) : null,
    ch.component === "switch" ? h("label", { class: "field" }, h("span", { class: "flabel" }, "Gerätetyp für Drittsysteme"), sel("consumption_type")) : null,
    ch.component === "switch" ? h("label", { class: "row small" }, autoOff, "Automatisch aus nach", delay, "s") : null),
  actions: [{ label: "Abbrechen" }, { label: "Speichern", primary: true, onclick: async () => {
    const changes = [];
    const idx = ch.index;
    for (const k of ["initial_state", "in_mode", "consumption_type"]) if (st[k] !== orig[k]) changes.push({ id: k, value: st[k], channel: idx });
    if (st.auto_off !== orig.auto_off) changes.push({ id: "auto_off", value: st.auto_off, channel: idx });
    if (st.auto_off && st.auto_off_delay !== orig.auto_off_delay) changes.push({ id: "auto_off_delay", value: st.auto_off_delay, channel: idx });
    if (!changes.length) return dlg.close();
    dlg.setBody(loading("Starte …"));
    dlg.setActions([]);
    const { job_id } = await panel.hass.callWS({ type: "shelly_manager/batch/apply", macs: [row.mac], changes, label: `${row.name} ${ch.key}` });
    jobView(panel, dlg, job_id, byId);
  } }] });
}
