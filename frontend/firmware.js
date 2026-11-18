// Firmware-Updater: Übersicht mit Schaltzustands-Prüfung → gestaffelt aktualisieren
import { jobView } from "./batch.js";
import { modal } from "./dialogs.js";
import { MDI, fmtW, h, icon, spinner } from "./util.js";

const loading = (text) => h("p", { class: "row muted" }, spinner(16), text);
const onOff = (v) => (v === true ? "an" : v === false ? "aus" : "?");
const INIT = { off: "aus", on: "ein", restore_last: "letzter Zustand", match_input: "wie Eingang" };

export async function firmwareDialog(panel, macs) {
  const dlg = modal(panel, { title: macs?.length ? `Firmware für ${macs.length} Geräte` : "Firmware-Updates", body: loading("Lade …"), wide: true });
  const opts = { stage: "stable", concurrency: 1, gap: 30, keep_state: true, stop_on_error: true };
  let chosen = new Set();

  async function load() {
    dlg.setBody(loading("Prüfe verfügbare Versionen und Schaltverhalten …"));
    dlg.setActions([]);
    const res = await panel.hass.callWS({ type: "shelly_manager/firmware/preview", stage: opts.stage, ...(macs?.length ? { macs } : {}) });
    render(res.devices);
  }

  function render(devices) {
    const upd = devices.filter((d) => d.state === "update");
    chosen = new Set(upd.map((d) => d.mac));
    const risky = upd.filter((d) => d.risky.length);
    const listEl = h("div", { class: "oplist" });
    for (const d of devices) {
      const r = panel.byMac.get(d.mac);
      const can = d.state === "update";
      const cb = h("input", { type: "checkbox", checked: can, disabled: !can });
      cb.addEventListener("change", () => { cb.checked ? chosen.add(d.mac) : chosen.delete(d.mac); actions(); });
      listEl.append(h("label", { class: "fwdev" }, cb,
        h("span", { class: "grow" }, h("div", {}, r?.name ?? d.mac), h("div", { class: "muted small" }, r?.model ?? "")),
        h("span", { class: "mono small" }, d.current ?? "?", can ? [" ", icon(MDI.expand, 12), " ", h("b", {}, d.target)] : ""),
        can ? null : h("span", { class: "tag off" }, d.reason)));
      for (const e of d.risky) {
        listEl.append(h("div", { class: `fwrisk ${e.severity === "error" ? "sev-error" : "sev-warning"}` },
          icon(e.severity === "error" ? MDI.error : MDI.alert, 14),
          h("span", {}, `${e.name ?? e.key}: jetzt ${onOff(e.now)} → nach Neustart ${onOff(e.after)}`,
            h("span", { class: "muted" }, ` (nach Stromausfall: ${INIT[e.initial_state] ?? e.initial_state}${typeof e.power === "number" ? `, ${fmtW(e.power)}` : ""})`))));
      }
    }
    const stage = h("span", { class: "segment" }, [["stable", "Stabil"], ["beta", "Beta"]].map(([v, l]) =>
      h("button", { class: opts.stage === v ? "active" : "", onclick: () => { opts.stage = v; load(); } }, l)));
    const num = (key, min, max, label, suffix) => h("label", { class: "row small" }, label,
      h("input", { type: "number", min, max, value: opts[key], style: { width: "70px" }, oninput: (e) => { opts[key] = Number(e.target.value); actions(); } }), suffix);
    const chk = (key, label) => {
      const c = h("input", { type: "checkbox", checked: opts[key] });
      c.addEventListener("change", () => { opts[key] = c.checked; });
      return h("label", { class: "row small" }, c, label);
    };
    dlg.setBody(h("div", { class: "form" },
      h("div", { class: "row wrap" }, stage,
        h("button", { class: "btn", style: { height: "32px" }, onclick: check }, icon(MDI.radar, 16), "Nach Updates suchen"),
        h("span", { class: "grow" }),
        h("span", { class: "muted small" }, `${upd.length} von ${devices.length} mit Update`)),
      risky.length ? h("p", { class: "small sev-warning" },
        `${risky.length} Gerät${risky.length === 1 ? "" : "e"} würden beim Neustart ihren Schaltzustand ändern. ` +
        "Mit »Schaltzustände sichern« stellt der Manager die betroffenen Ausgänge vorher auf »letzter Zustand« und danach wieder zurück.") : null,
      upd.length || devices.length ? listEl : h("p", { class: "muted" }, "Keine Updates verfügbar."),
      h("div", { class: "fwopts" },
        num("concurrency", 1, 4, "Gleichzeitig", "Geräte"),
        num("gap", 0, 600, "Abstand zwischen Starts", "s"),
        chk("keep_state", "Schaltzustände während des Updates sichern"),
        chk("stop_on_error", "Bei einem Fehler anhalten"))));
    actions();
  }

  function actions() {
    const n = chosen.size;
    const mins = Math.ceil((n * 2) / Math.max(1, opts.concurrency) + (n * opts.gap) / 60);
    dlg.setActions([{ label: "Abbrechen" },
      { label: n ? `${n} aktualisieren (ca. ${mins} min)` : "Nichts ausgewählt", primary: true, disabled: !n, onclick: start }]);
  }

  async function check() {
    const res = await panel.hass.callWS({ type: "shelly_manager/firmware/check", ...(macs?.length ? { macs } : {}) });
    panel.toast(`${res.queued} Geräte suchen nach Updates – die Liste lädt danach neu.`);
    dlg.setBody(loading("Geräte suchen nach Updates …"));
    dlg.setActions([]);
    if (!res.queued) return load();
    const off = panel.onBusyIdle(() => { off(); load(); });
  }

  async function start() {
    dlg.setBody(loading("Starte …"));
    dlg.setActions([]);
    const { job_id } = await panel.hass.callWS({ type: "shelly_manager/firmware/start", macs: [...chosen], ...opts });
    jobView(panel, dlg, job_id, {});
  }

  load().catch((err) => dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err))));
}
