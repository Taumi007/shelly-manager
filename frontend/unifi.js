// UniFi: Client-Aliasse und feste IPs – Vorschau, Auswahl, Anwenden
import { modal, newOpId, progressList } from "./dialogs.js";
import { MDI, h, icon, spinner } from "./util.js";

const loading = (text) => h("p", { class: "row muted" }, spinner(16), text);
const KIND = { name: "Alias", fixed_ip: "Feste IP", clear_fixed: "Reservierung" };
const TITLES = { names: "Namen nach UniFi übertragen", fixed_ip: "Feste IPs in UniFi vergeben",
  cleanup: "Alte Reservierungen entfernen", release: "Reservierung in UniFi entfernen" };

export async function unifiDialog(panel, macs, what) {
  const title = TITLES[what[0]] ?? "UniFi";
  const dlg = modal(panel, { title, body: loading("Frage UniFi ab …"), wide: true, actions: [{ label: "Abbrechen" }] });
  let res;
  try {
    res = await panel.hass.callWS({ type: "shelly_manager/unifi/preview", what, ...(macs?.length ? { macs } : {}) });
  } catch (err) {
    dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    return;
  }
  if (!res.status.available) {
    dlg.setBody(h("p", { class: "sev-warning" }, "Die UniFi-Network-Integration ist in HA nicht geladen."));
    return;
  }
  const checked = new Set(res.ops.map((o) => `${o.mac}|${o.kind}`));
  const list = h("div", { class: "oplist" }, res.ops.map((o) => {
    const id = `${o.mac}|${o.kind}`;
    const cb = h("input", { type: "checkbox", checked: true });
    cb.addEventListener("change", () => { cb.checked ? checked.add(id) : checked.delete(id); upd(); });
    return h("label", { class: "op" }, cb, h("span", { class: "tag" }, KIND[o.kind]), h("span", { class: "muted small oplbl" }, o.device),
      h("span", { class: "opold" }, o.old), icon(MDI.expand, 16), h("span", { class: "opnew" }, o.new));
  }));
  const upd = () => dlg.setActions([{ label: "Abbrechen" },
    { label: `${checked.size} in UniFi schreiben`, primary: true, disabled: !checked.size, onclick: apply }]);
  dlg.setBody(h("div", {},
    h("p", { class: "muted small" }, `Über die UniFi-Integration »${res.status.title}«. ` +
      (what.includes("fixed_ip") ? "Feste IPs kommen aus dem Bereich in den Optionen; das Gerät übernimmt die neue Adresse nach einem Neustart. " : "") +
      (what.includes("names") ? "Alias = voller Shelly-Name (Bereich + Name)." : "")),
    res.ops.length ? list : h("p", { class: "row ok" }, icon(MDI.ok, 18), "Alles passt bereits."),
    res.skipped.length ? h("details", { class: "skipped" }, h("summary", {}, `${res.skipped.length} übersprungen`),
      res.skipped.map((s) => h("div", { class: "small" }, h("b", {}, s.device), ` – ${s.reason}`))) : null));
  upd();

  async function apply() {
    const chosen = [...checked];
    const targets = [...new Set(chosen.map((c) => c.split("|")[0]))];
    const opId = newOpId();
    const byId = Object.fromEntries(res.ops.map((o) => [`${o.mac}|${o.kind}`, o]));
    const prog = progressList(panel, chosen.map((c) => ({ id: c, label: `${byId[c]?.device ?? c} – ${KIND[byId[c]?.kind] ?? ""}` })), opId);
    dlg.setBody(h("div", {}, h("p", { class: "small muted" }, `Schreibe ${chosen.length} Änderungen in UniFi …`), prog.el));
    dlg.setActions([]);
    try {
      const out = await panel.hass.callWS({ type: "shelly_manager/unifi/apply", macs: targets, what, only: chosen, op_id: opId });
      prog.close();
      const bad = out.results.filter((r) => !r.ok);
      const reboot = [...new Set(out.results.filter((r) => r.ok && r.reboot).map((r) => r.mac))];
      dlg.setBody(h("div", {}, h("p", { class: "row ok" }, icon(MDI.ok, 18), `${out.results.length - bad.length} von ${out.results.length} übernommen.`),
        out.results.filter((r) => r.ok && r.new && r.kind === "clear_fixed").map((r) => h("div", { class: "small muted" }, `${r.device}: ${r.new}`)),
        bad.length ? h("ul", { class: "small sev-error" }, bad.map((b) => h("li", {}, `${b.device}: ${b.error}`))) : null,
        reboot.length ? h("p", { class: "small" }, `${reboot.length} Gerät(e) übernehmen die geänderte Adresse erst nach einem Neustart.`) : null));
      if (reboot.length) {
        dlg.setActions([{ label: "Schließen" }, { label: `${reboot.length} neu starten`, primary: true, onclick: async () => {
          const r = await panel.hass.callWS({ type: "shelly_manager/reboot", macs: reboot });
          panel.toast(`${r.queued} Gerät(e) werden neu gestartet.`);
          dlg.close();
        } }]);
        return;
      }
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    }
    dlg.setActions([{ label: "Schließen" }]);
  }
}
