// Ein-Klick-Reparaturen pro Problemart – immer über eine Vorschau bzw. Bestätigung
import { batchDialog } from "./batch.js";
import { helperDialog, inputDisableDialog, modal, namesDialog, progressList, rebootDialog, renameItemDialog } from "./dialogs.js";
import { firmwareDialog } from "./firmware.js";
import { unifiDialog } from "./unifi.js";
import { applyWsPreset } from "./presets.js";
import { MDI, h, icon, spinner } from "./util.js";

const uniq = (entries) => [...new Set(entries.map((e) => e.r.mac))];
const loading = (text) => h("p", { class: "row muted" }, spinner(16), text);

function names(direction, title) {
  return (panel, entries) => namesDialog(panel, uniq(entries).map((mac) => ({ mac, direction })), title);
}

function chooseDirection(panel, entries, e) {
  panel.openMenu(e, [
    h("div", { class: "menu-title" }, `${uniq(entries).length} Geräte abgleichen`),
    panel.item("HA-Namen in die Shellys schreiben", MDI.chip, () => names("to_shelly", "HA-Namen → Shellys")(panel, entries)),
    panel.item("Shelly-Namen in HA übernehmen", MDI.ha, () => names("to_ha", "Shelly-Namen → Home Assistant")(panel, entries)),
  ]);
}

function confirm(panel, title, text, label, run) {
  const dlg = modal(panel, { title, body: h("p", {}, text), actions: [{ label: "Abbrechen" }, { label, primary: true, onclick: async () => {
    dlg.setBody(loading("Läuft …"));
    dlg.setActions([]);
    try {
      dlg.setBody(h("p", { class: "row ok" }, icon(MDI.ok, 18), await run()));
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    }
    dlg.setActions([{ label: "Schließen" }]);
  } }] });
}

// Namensregeln: Vorschlag je Eintrag, editierbar, mit Live-Prüfung
export function renameDialog(panel, entries) {
  const items = [];
  const seen = new Set();
  for (const { r } of entries) {
    for (const ri of r.rule_issues ?? []) {
      const id = `${r.mac}|${ri.key}`;
      if (seen.has(id)) continue;
      seen.add(id);
      items.push({ r, ri, value: ri.suggest ?? ri.name, on: !!ri.suggest });
    }
  }
  const list = h("div", { class: "oplist" });
  const dlg = modal(panel, { title: "Namen nach Regeln korrigieren", body: list, wide: true });
  const update = () => {
    const n = items.filter((i) => i.on).length;
    dlg.setActions([{ label: "Abbrechen" }, { label: `${n} umbenennen`, primary: true, disabled: !n, onclick: apply }]);
  };
  for (const it of items) {
    const cb = h("input", { type: "checkbox", checked: it.on });
    const issues = h("span", { class: "small" });
    const field = h("input", { type: "text", value: it.value, style: { width: "100%" } });
    let t;
    const check = () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        const res = await panel.hass.callWS({ type: "shelly_manager/names/check", name: field.value, area: it.ri.area ?? it.r.area, kind: it.ri.kind });
        issues.replaceChildren(res.issues.length
          ? h("span", { class: "sev-warning", title: res.issues.map((x) => x.text).join("\n") }, icon(MDI.alert, 14), ` ${res.issues[0].text}`)
          : h("span", { class: "ok" }, icon(MDI.ok, 14), " passt"));
      }, 250);
    };
    cb.addEventListener("change", () => { it.on = cb.checked; update(); });
    field.addEventListener("input", () => { it.value = field.value; it.on = true; cb.checked = true; update(); check(); });
    check();
    list.append(h("div", { class: "rnitem" },
      cb,
      h("div", { class: "grow" },
        h("div", { class: "small muted" }, `${it.r.name}${it.ri.key === "device" ? "" : ` · ${it.ri.key}`} – ${it.ri.text}`),
        h("div", { class: "row" }, h("span", { class: "opold", style: { flex: "0 1 40%" } }, it.ri.name), icon(MDI.expand, 14), field),
        issues)));
  }
  list.prepend(h("p", { class: "muted small" }, "Vorschläge sind vorausgefüllt, wo sich der Verstoß mechanisch beheben lässt. Geschrieben wird der volle Name in den Shelly und der reine Name (ohne Bereich) in HA."));
  update();

  async function apply() {
    const todo = items.filter((i) => i.on);
    const prog = progressList(panel, todo.map((it, i) => ({ id: String(i), label: `${it.ri.name} → ${it.value}` })));
    dlg.setBody(h("div", {}, h("p", { class: "small muted" }, `Benenne ${todo.length} um …`), prog.el));
    dlg.setActions([]);
    const errors = [];
    for (const [i, it] of todo.entries()) {
      prog.set(String(i), "running");
      try {
        await panel.hass.callWS({ type: "shelly_manager/names/set", mac: it.r.mac, key: it.ri.key, name: it.value });
        prog.set(String(i), "done");
      } catch (err) {
        errors.push(`${it.r.name}: ${err.message ?? err}`);
        prog.set(String(i), "error", String(err.message ?? err));
      }
    }
    dlg.setBody(h("div", {}, h("p", { class: "row ok" }, icon(MDI.ok, 18), `${todo.length - errors.length} von ${todo.length} umbenannt.`),
      errors.length ? h("ul", { class: "small sev-error" }, errors.map((e) => h("li", {}, e))) : null));
    dlg.setActions([{ label: "Schließen" }]);
  }
}

function helpers(panel, entries) {
  const list = h("div", { class: "oplist" }, entries.map(({ r, p }) => h("div", { class: "jobdev" },
    h("span", { class: "grow" }, r.name, h("div", { class: "muted small" }, p.entity_id)),
    h("button", { class: "btn", style: { height: "28px" }, onclick: () => helperDialog(panel, p.entity_id) }, "Umstellen …"))));
  modal(panel, { title: "Helfer durch Gerätetyp ersetzen", body: list, actions: [{ label: "Schließen" }] });
}

// Kanäle ohne (sinnvollen) Namen: je Eintrag benennen oder deaktivieren
function channelsDialog(panel, entries) {
  const dlg = modal(panel, { title: "Kanäle ohne Namen", wide: true, actions: [{ label: "Schließen" }], body: h("div", { class: "oplist" },
    h("p", { class: "muted small" }, "Ungenutzte Kanäle lassen sich deaktivieren: Eingänge im Shelly, Ausgänge in HA (Gerät bzw. Entität)."),
    entries.map(({ r, p }) => {
      const it = [r.names.device, ...r.names.channels].find((x) => x.key === p.key);
      const isInput = it?.kind === "input";
      const row = h("div", { class: "jobdev" },
        h("span", { class: "grow" }, r.name, h("div", { class: "muted small" }, `${isInput ? "Eingang" : "Kanal"} ${p.key}${it?.ha_name ? ` · HA: ${it.ha_name}` : ""}`)),
        it ? h("button", { class: "mini", onclick: () => renameItemDialog(panel, r, it) }, "Benennen …") : null,
        isInput ? h("button", { class: "mini", onclick: () => inputDisableDialog(panel, r, it) }, "Eingang deaktivieren …")
          : h("button", { class: "mini", onclick: async (e) => {
            try {
              const res = await panel.hass.callWS({ type: "shelly_manager/ha/disable", mac: r.mac, key: p.key });
              e.target.replaceWith(h("span", { class: "small ok" }, res.disabled === "device" ? "Gerät in HA deaktiviert" : "Entität deaktiviert"));
            } catch (err) { panel.toast(err.message ?? String(err), "error"); }
          } }, "In HA deaktivieren"));
      return row;
    })) });
  return dlg;
}

// Problemcode → Reparatur (null = keine automatische Reparatur)
export function fixFor(code) {
  if (code === "name_channel_single_channel_named" || code === "rule_space")
    return { label: "Beheben …", run: names("to_shelly", "Namen korrigieren (HA → Shelly)") };
  if (/^name_(device|channel)_(ha_area_in_name|ha_no_user_name)$/.test(code))
    return { label: "Beheben …", run: names("to_ha", "HA-Namen bereinigen (Shelly → HA)") };
  if (code === "name_channel_shelly_empty" || code === "name_channel_generic")
    return { label: "Benennen / deaktivieren …", run: channelsDialog };
  if (/^name_(device|channel)_(diff|shelly_no_area|shelly_empty|generic|duplicate_channel)$/.test(code) || code === "name_diff")
    return { label: "Abgleichen …", run: (panel, entries, e) => chooseDirection(panel, entries, e) };
  if (code.startsWith("rule_")) return { label: "Korrigieren …", run: renameDialog };
  if (code === "switch_as_light") return { label: "Umstellen …", run: helpers };
  if (code === "ap_open") return { label: "AP abschalten …", run: (panel, entries) => batchDialog(panel, uniq(entries), [{ id: "ap_enable", value: false }]) };
  if (code === "mqtt") return { label: "MQTT abschalten …", run: (panel, entries) => batchDialog(panel, uniq(entries), [{ id: "mqtt_enable", value: false }]) };
  if (code === "ws") return { label: "Neu einrichten / abschalten …", run: (panel, entries, e) => panel.openMenu(e, [
    h("div", { class: "menu-title" }, "Ausgehender WebSocket"),
    panel.item("Mit aktueller HA-Adresse neu einrichten", MDI.ha, () => applyWsPreset(panel, uniq(entries)),
      { sub: "nur für Akku- und Fallback-Geräte" }),
    panel.item("Abschalten", MDI.close, () => batchDialog(panel, uniq(entries), [{ id: "ws_enable", value: false }])),
  ]) };
  if (code === "fetch_error") return { label: "Erneut abfragen", run: (panel, entries) => panel.refresh("full", uniq(entries)) };
  if (code === "entry_title") return { label: "Titel angleichen …", run: (panel, entries) => {
    const macs = uniq(entries);
    confirm(panel, "Integrationseinträge umbenennen", `Die Titel von ${macs.length} Shelly-Einträgen unter Einstellungen → Integrationen bekommen den vollen Gerätenamen aus dem Shelly.`,
      "Umbenennen", async () => `${(await panel.hass.callWS({ type: "shelly_manager/entries/retitle", macs })).changed} Einträge umbenannt.`);
  } };
  if (code === "unifi_name") return { label: "Übertragen …", run: (panel, entries) => unifiDialog(panel, uniq(entries), ["names"]) };
  if (code === "unifi_stale") return { label: "Aufräumen …", run: (panel, entries) => unifiDialog(panel, uniq(entries), ["cleanup"]) };
  if (code === "unifi_fixed_mismatch") return { label: "Reservierung entfernen …", run: (panel, entries) => unifiDialog(panel, uniq(entries), ["release"]) };
  if (code === "update") return { label: "Aktualisieren …", run: (panel, entries) => firmwareDialog(panel, uniq(entries)) };
  if (code === "restart_required") return { label: "Neu starten …", run: (panel, entries) =>
    rebootDialog(panel, uniq(entries), "Diese Geräte melden »Neustart nötig«, damit geänderte Einstellungen wirksam werden.") };
  if (code === "entry_state" || code === "unavailable") return { label: "Neu laden …", run: (panel, entries) => {
    const macs = uniq(entries);
    confirm(panel, "In HA neu laden", `Die Shelly-Integration lädt ${macs.length} Geräte nacheinander neu. Das hilft, wenn ein Gerät inzwischen wieder erreichbar ist.`,
      "Neu laden", async () => `${(await panel.hass.callWS({ type: "shelly_manager/reload", macs })).queued} Einträge werden neu geladen.`);
  } };
  return null;
}
