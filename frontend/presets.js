// Tab »Vorgaben«: WLAN, MQTT und WebSocket einmal hinterlegen und gezielt auf Geräte anwenden
import { jobView } from "./batch.js";
import { modal } from "./dialogs.js";
import { MDI, h, icon } from "./util.js";

const KINDS = {
  wifi: { title: "WLAN", icon: MDI.wifi },
  mqtt: { title: "MQTT", icon: MDI.radar },
  ws: { title: "WebSocket zu Home Assistant", icon: MDI.ha },
};

export function presetsView(panel) {
  const root = h("div", { class: "view" }, h("p", { class: "empty" }, "Lade …"));
  load();
  return root;

  async function load() {
    let res;
    try {
      res = await panel.hass.callWS({ type: "shelly_manager/presets/get" });
    } catch (err) {
      root.replaceChildren(h("p", { class: "empty sev-error" }, String(err.message ?? err)));
      return;
    }
    const { presets, eligible } = res;
    const keys = presets.secret_keys ?? [];
    const sf = (v, key, label, type = "text", ph = "") => secretField(v, key, label, type, ph, keys);
    root.replaceChildren(h("div", { class: "presets" },
      h("p", { class: "muted small" }, "Vorgaben werden hier gespeichert und erst beim Anwenden auf Geräte geschrieben – mit Fortschritt, Rückgängig und " +
        "gestaffeltem Neustart, falls nötig. Jedes Feld kann fester Text sein oder auf einen Eintrag in secrets.yaml verweisen; " +
        "secrets.yaml wird erst beim Anwenden gelesen. Passwörter werden nie angezeigt."),
      card("wifi", presets.wifi ?? {}, eligible.wifi, (v) => [
        sf(v, "ssid", "SSID"),
        sf(v, "pass", "Passwort", "password", presets.wifi?.has_pass ? "gespeichert – leer lassen zum Behalten" : ""),
        field("Ziel im Shelly", select(v, "slot", [["sta1", "WLAN 2 (Ausweich-WLAN) – empfohlen"], ["sta", "WLAN 1 (Haupt-WLAN)"]])),
        v.slot === "sta" ? h("p", { class: "small sev-warning" }, "Das Haupt-WLAN zu ändern kann Geräte vom Netz trennen, wenn SSID oder Passwort nicht stimmen.") : null,
        autoToggle(),
      ]),
      card("mqtt", presets.mqtt ?? { enable: true }, eligible.mqtt, (v) => [
        h("label", { class: "row small" }, h("input", { type: "checkbox", checked: v.enable !== false, onchange: (e) => { v.enable = e.target.checked; } }), "MQTT aktiv"),
        sf(v, "server", "Server (Host:Port)", "text", "192.168.1.10:1883"),
        sf(v, "user", "Benutzer"),
        sf(v, "pass", "Passwort", "password", presets.mqtt?.has_pass ? "gespeichert – leer lassen zum Behalten" : ""),
      ]),
      card("ws", presets.ws ?? {}, eligible.ws, () => [
        h("p", { class: "small" }, "Adresse: ", h("code", {}, presets.ws?.server ?? "–")),
        h("p", { class: "muted small" }, "Nur für Geräte, bei denen es sinnvoll ist: Akku-Geräte (z. B. H&T – HA erreicht sie nur so) und Geräte mit Fallback-Modus " +
          "(Signal, ob HA erreichbar ist). Die Adresse ermittelt der Manager aus HA."),
      ], false),
    ));
  }

  // Neue Bluetooth-Funde sofort mit dieser WLAN-Vorgabe einrichten (Einstellung im Store, übersteht Neustarts)
  function autoToggle() {
    const on = !!panel.options?.auto_provision;
    return h("div", { class: "form", style: { borderTop: "1px solid var(--divider-color)", paddingTop: "8px" } },
      h("label", { class: "row small" },
        h("input", { type: "checkbox", checked: on, onchange: async (e) => {
          e.stopPropagation();
          const res = await panel.hass.callWS({ type: "shelly_manager/provision/auto", enabled: e.target.checked });
          panel.options = { ...(panel.options ?? {}), auto_provision: res.enabled, provision_wifi: res.wifi };
          panel.toast(res.enabled ? (res.wifi ? "Neue Geräte werden automatisch eingerichtet" : "Aktiv, aber ohne WLAN – erst Vorgabe speichern")
            : "Automatische Einrichtung aus");
        } }),
        h("span", {}, h("b", {}, "Neue Geräte per Bluetooth sofort automatisch ins WLAN bringen"),
          h("div", { class: "muted" }, "Mit dieser WLAN-Vorgabe, sobald ein Bluetooth-Proxy ein unkonfiguriertes Gerät findet. " +
            "Gerade zurückgesetzte Geräte sind ausgenommen (30 Tage oder bis sie manuell eingerichtet werden)."))));
  }

  function card(kind, values, eligible, fields, editable = true) {
    const v = { ...values };
    const body = h("div", { class: "form" });
    const draw = () => body.replaceChildren(...fields(v).filter(Boolean));
    draw();
    // Felder können sich gegenseitig beeinflussen (z. B. Warnung beim Haupt-WLAN)
    body.addEventListener("change", draw);
    const sel = [...panel.selected].filter((m) => eligible.includes(m));
    return h("div", { class: "pcard" },
      h("h3", {}, icon(KINDS[kind].icon, 18), h("span", { class: "grow" }, KINDS[kind].title)),
      body,
      h("div", { class: "row wrap" },
        editable ? h("button", { class: "btn", onclick: async () => {
          await panel.hass.callWS({ type: "shelly_manager/presets/save", kind, values: v });
          panel.toast("Vorgabe gespeichert", "ok");
          load();
        } }, "Speichern") : null,
        h("span", { class: "grow" }),
        h("button", { class: "btn", disabled: !sel.length, title: "In der Gerätetabelle markierte Geräte",
          onclick: () => apply(kind, sel) }, `Auf ${sel.length} markierte anwenden …`),
        kind === "ws" ? h("button", { class: "btn primary", disabled: !eligible.length, onclick: () => apply(kind, eligible) },
          `Auf alle ${eligible.length} passenden anwenden …`) : null));
  }

  function apply(kind, macs) {
    const names = macs.map((m) => panel.byMac.get(m)?.name ?? m);
    const dlg = modal(panel, { title: `Vorgabe ${KINDS[kind].title} anwenden`, wide: true,
      body: h("div", {}, h("p", { class: "small" }, `${macs.length} Geräte:`), h("ul", { class: "small steps" }, names.map((n) => h("li", {}, n)))),
      actions: [{ label: "Abbrechen" }, { label: "Anwenden", primary: true, onclick: async () => {
        try {
          const { job_id } = await panel.hass.callWS({ type: "shelly_manager/presets/apply", kind, macs });
          jobView(panel, dlg, job_id, {});
        } catch (err) {
          dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
          dlg.setActions([{ label: "Schließen" }]);
        }
      } }] });
  }
}

// Feld mit Umschalter: fester Text oder Verweis auf secrets.yaml (nur der Schlüssel wird gespeichert)
function secretField(v, key, label, type, placeholder, keys) {
  const isRef = v[key] && typeof v[key] === "object";
  const mode = h("select", { class: "srcsel", onchange: (e) => {
    v[key] = e.target.value === "secret" ? { secret: "" } : "";
  } }, h("option", { value: "text", selected: !isRef }, "Text"), h("option", { value: "secret", selected: isRef }, "secrets.yaml"));
  let el;
  if (isRef) {
    const listId = `sk-${Math.random().toString(36).slice(2, 8)}`;
    const known = keys.includes(v[key].secret);
    el = h("span", { class: "row grow" },
      h("input", { type: "text", list: listId, value: v[key].secret ?? "", placeholder: "Schlüssel, z. B. shelly_wifi_password",
        oninput: (e) => { v[key] = { secret: e.target.value.trim() }; } }),
      h("datalist", { id: listId }, keys.map((k) => h("option", { value: k }))),
      v[key].secret ? h("span", { class: `small ${known ? "ok" : "sev-error"}`, title: known ? "in secrets.yaml vorhanden" : "fehlt in secrets.yaml" },
        icon(known ? MDI.ok : MDI.error, 14)) : null);
  } else {
    el = input(v, key, type, placeholder);
  }
  return h("label", { class: "field" }, h("span", { class: "flabel" }, label), h("span", { class: "row" }, mode, el));
}

function field(label, el) {
  return h("label", { class: "field" }, h("span", { class: "flabel" }, label), el);
}

function input(v, key, type = "text", placeholder = "") {
  return h("input", { type, value: type === "password" ? "" : v[key] ?? "", placeholder, oninput: (e) => { v[key] = e.target.value; } });
}

function select(v, key, options) {
  return h("select", { onchange: (e) => { v[key] = e.target.value; } },
    options.map(([val, label]) => h("option", { value: val, selected: (v[key] ?? options[0][0]) === val }, label)));
}

// Für die Reparatur »WebSocket aktiviert, aber nicht verbunden«
export async function applyWsPreset(panel, macs) {
  const res = await panel.hass.callWS({ type: "shelly_manager/presets/get" });
  const ok = macs.filter((m) => res.eligible.ws.includes(m));
  if (!ok.length) {
    panel.toast("Für diese Geräte ist der WebSocket nicht vorgesehen – besser abschalten.");
    return;
  }
  const dlg = modal(panel, { title: "WebSocket neu einrichten", body: h("p", {}, `${ok.length} Gerät(e) bekommen die aktuelle HA-Adresse ${res.presets.ws.server}.`),
    actions: [{ label: "Abbrechen" }, { label: "Einrichten", primary: true, onclick: async () => {
      const { job_id } = await panel.hass.callWS({ type: "shelly_manager/presets/apply", kind: "ws", macs: ok });
      jobView(panel, dlg, job_id, {});
    } }] });
}
