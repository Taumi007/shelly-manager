// Shelly komplett zurücksetzen: Vorschau mit allen Spuren (Gerät, HA, UniFi, Manager), dann Schritt für Schritt
import { doneBar, modal, newOpId, progressList } from "./dialogs.js";
import { MDI, h, icon } from "./util.js";

const USES = { automation: "Automation", script: "Skript", scene: "Szene" };

export async function resetDialog(panel, mac) {
  const dlg = modal(panel, { title: "Shelly zurücksetzen", wide: true, body: h("p", { class: "muted" }, "Lade …"), actions: [{ label: "Abbrechen" }] });
  let plan;
  try {
    plan = await panel.hass.callWS({ type: "shelly_manager/reset/preview", mac });
  } catch (err) {
    dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    return;
  }
  // Wo das Gerät in HA verwendet wird – das funktioniert danach nicht mehr
  const uses = [];
  for (const id of plan.ha?.device_ids ?? []) {
    try {
      const rel = await panel.hass.callWS({ type: "search/related", item_type: "device", item_id: id });
      for (const [k, label] of Object.entries(USES)) for (const e of rel[k] ?? []) {
        if (!uses.some((u) => u.id === e)) uses.push({ id: e, label, name: panel.hass.states[e]?.attributes?.friendly_name ?? e });
      }
    } catch { /* Suche ist nur ein Hinweis */ }
  }

  const sel = new Set(plan.steps.filter((s) => s.available && s.default).map((s) => s.id));
  const title = plan.name ?? plan.mac;
  const draw = () => {
    const factory = sel.has("factory");
    dlg.setBody(h("div", { class: "form" },
      h("p", {}, h("b", {}, title), h("span", { class: "muted small" }, ` · ${plan.model ?? ""} · ${plan.host ?? "keine IP"} · ${plan.mac}`)),
      h("ul", { class: "small steps" },
        plan.ha ? h("li", {}, `Home Assistant: Eintrag »${plan.ha.title}« mit ${plan.ha.devices.length} Gerät(en) und ${plan.ha.entities} Entitäten`) : h("li", { class: "muted" }, "Nicht in Home Assistant eingerichtet"),
        plan.unifi ? h("li", {}, `UniFi: ${plan.unifi.name ? `Name »${plan.unifi.name}«` : "ohne Namen"}` +
          (plan.unifi.use_fixedip ? `, IP-Reservierung ${plan.unifi.fixed_ip}` : ", keine IP-Reservierung")) : h("li", { class: "muted" }, "In UniFi nicht bekannt"),
        plan.fallback ? h("li", {}, "Fallback-Modus ist eingerichtet") : null),
      uses.length ? h("div", { class: "small sev-warning" },
        h("p", {}, `Verwendet in ${uses.length} Automation(en)/Skript(en)/Szene(n) – diese verweisen danach ins Leere:`),
        h("ul", { class: "steps" }, uses.map((u) => h("li", {}, `${u.label}: ${u.name}`)))) : null,
      h("div", { class: "rsteps small" }, plan.steps.map((s) => {
        const blocked = !s.available || s.fixed || (s.id === "name" && factory);
        const checked = s.fixed || (sel.has(s.id) && !(s.id === "name" && factory));
        return h("label", { class: blocked && !s.fixed ? "off" : "" },
          h("input", { type: "checkbox", checked, disabled: blocked, onchange: (e) => { e.target.checked ? sel.add(s.id) : sel.delete(s.id); draw(); } }),
          h("span", {}, s.label, s.note && (!s.available || ["name", "history", "statistics"].includes(s.id))
            ? h("span", { class: ["history", "statistics"].includes(s.id) && s.available ? "sev-warning" : "muted" }, ` – ${s.note}`) : null));
      })),
      factory ? h("p", { class: "small muted" }, "Nach dem Werksreset startet das Gerät ohne WLAN, Namen und Einstellungen. Es meldet sich per Bluetooth " +
        "und erscheint dann unter »Neue Geräte« zum Wiedereinrichten. Die gesicherte Konfiguration bleibt im Manager.") : null,
      !factory && sel.has("ha") ? h("p", { class: "small sev-warning" }, "Ohne Werksreset bleibt das Gerät im WLAN; HA bietet es nach dem Entfernen ggf. erneut zur Einrichtung an.") : null,
    ));
    const any = [...sel].some((s) => s !== "manager");
    dlg.setActions([{ label: "Abbrechen" }, { label: factory ? "Endgültig zurücksetzen" : "Ausführen", danger: true, disabled: !any, onclick: run }]);
  };
  draw();

  async function run() {
    const steps = [...sel];
    const opId = newOpId();
    let order;
    try {
      ({ steps: order } = await panel.hass.callWS({ type: "shelly_manager/reset/start", mac, steps, op_id: opId }));
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
      dlg.setActions([{ label: "Schließen" }]);
      return;
    }
    const labels = Object.fromEntries(plan.steps.map((s) => [s.id, s.label]));
    const prog = progressList(panel, order.map((id) => ({ id, label: labels[id] })));
    dlg.setBody(h("div", {}, h("p", { class: "small muted" }, `${title} wird zurückgesetzt …`), prog.el));
    dlg.setActions([{ label: "Im Hintergrund weiterlaufen lassen" }]);
    let done = 0;
    let bad = 0;
    const off = panel.onOp(opId, (ev) => {
      prog.set(ev.item, ev.state, ev.error);
      if (ev.state === "done" || ev.state === "error") done++;
      if (ev.state === "error") bad++;
      if (done >= order.length) {
        off();
        dlg.appendBody(bad ? doneBar(bad, order.length, "") : h("div", { class: "donebar ok" }, icon(MDI.ok, 18),
          `${title} ist zurückgesetzt${order.includes("factory") ? " – es meldet sich gleich per Bluetooth unter »Neue Geräte«" : ""}.`));
        if (panel.detail?.mac === mac) panel.detail.close?.();
        dlg.setActions([{ label: "Schließen", primary: true }]);
      }
    });
  }
}
