// Fallback-Modus: Belegungen bearbeiten, Script hochladen, Tasten simuliert testen
import { modal } from "./dialogs.js";
import { MDI, fmtAgo, h, icon, spinner } from "./util.js";

const loading = (text) => h("p", { class: "row muted" }, spinner(16), text);
const TYPES = { double: "Doppeltaster (links ein/Szenen · rechts aus · halten dimmt)", single: "Einzeltaster (umschalten · halten dimmt)", switch: "Schalter (jede Änderung schaltet um)" };
const OUT = ["switch"];

export async function fallbackDialog(panel, mac) {
  const r = panel.byMac.get(mac);
  const dlg = modal(panel, { title: `Fallback-Modus: ${r?.name ?? mac}`, body: loading("Lade …"), wide: true });
  let st, raw, hue = null;
  try {
    [st, raw] = await Promise.all([
      panel.hass.callWS({ type: "shelly_manager/fallback/get", mac }),
      panel.hass.callWS({ type: "shelly_manager/device", mac }),
    ]);
  } catch (err) {
    dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
    return;
  }
  const cfg = raw.raw.config ?? {};
  const inputs = Object.keys(cfg).filter((k) => k.startsWith("input:")).map((k) => ({ idx: Number(k.split(":")[1]), name: cfg[k].name, type: cfg[k].type }))
    .sort((a, b) => a.idx - b.idx);
  const locals = (r?.channels ?? []).filter((c) => OUT.includes(c.component));
  const shellyTargets = panel.rows.filter((x) => x.mac !== mac && x.in_ha).flatMap((x) => x.channels.filter((c) => c.component === "switch")
    .map((c) => ({ mac: x.mac, channel: c.index, name: x.channels.filter((y) => y.component === "switch").length > 1 ? (c.names?.ha_full ?? `${x.name} ${c.key}`) : x.name })))
    .sort((a, b) => a.name.localeCompare(b.name, "de"));
  const bindings = JSON.parse(JSON.stringify(st.bindings ?? []));
  const inputLabel = (i) => { const x = inputs.find((y) => y.idx === Number(i)); return `input:${i}${x?.name ? ` – ${x.name}` : ""}`; };

  async function loadHue() {
    if (hue || !st.hue) return;
    try { hue = await panel.hass.callWS({ type: "shelly_manager/fallback/hue" }); } catch (err) { hue = { available: false, error: String(err.message ?? err) }; }
  }
  if (bindings.some((b) => b.targets.some((t) => t.kind === "hue"))) await loadHue();

  const select = (options, value, onchange) => h("select", { onchange: (e) => onchange(e.target.value) },
    options.map(([v, l]) => h("option", { value: v, selected: String(v) === String(value) }, l)));

  function targetLabel(t) {
    if (t.kind === "hue") return `Hue: ${t.name ?? t.id}`;
    if (t.kind === "shelly") return `Shelly: ${t.name ?? t.mac}`;
    return `Eigener Ausgang: switch:${t.channel}`;
  }

  function bindingCard(b, n) {
    const card = h("div", { class: "wzch" });
    const redraw = () => { card.replaceWith(bindingCard(b, n)); setActions(); };
    const inputsRow = b.type === "double"
      ? [h("label", { class: "field" }, h("span", { class: "flabel" }, "Links (ein / Szenen / heller)"),
          select(inputs.map((i) => [i.idx, inputLabel(i.idx)]), b.inputs[0], (v) => { b.inputs[0] = Number(v); })),
        h("label", { class: "field" }, h("span", { class: "flabel" }, "Rechts (aus / dunkler)"),
          select(inputs.map((i) => [i.idx, inputLabel(i.idx)]), b.inputs[1], (v) => { b.inputs[1] = Number(v); }))]
      : [h("label", { class: "field" }, h("span", { class: "flabel" }, b.type === "switch" ? "Eingang" : "Taste"),
          select(inputs.map((i) => [i.idx, inputLabel(i.idx)]), b.inputs[0], (v) => { b.inputs[0] = Number(v); }))];
    const hueGroups = new Set(b.targets.filter((t) => t.kind === "hue" && t.rtype === "grouped_light").map((t) => t.id));
    const sceneList = hue?.available && b.type === "double" && hueGroups.size
      ? hue.scenes.filter((s) => hueGroups.has(s.group)) : [];
    // Neues Ziel hinzufügen
    const addKind = select([["", "Ziel hinzufügen …"], ...(st.hue ? [["hue", "Hue-Lampe / -Raum"]] : []), ["shelly", "Anderer Shelly"],
      ...(locals.length ? [["local", "Eigener Ausgang"]] : [])], "", async (v) => {
      if (v === "hue") { await loadHue(); b._adding = "hue"; }
      else if (v) b._adding = v;
      redraw();
    });
    let picker = null;
    if (b._adding === "hue" && hue?.available) {
      picker = select([["", "Hue-Ziel wählen …"], ...hue.targets.map((t) => [t.id, `${t.name}${t.rtype === "light" ? "" : ""}`])], "", (v) => {
        const t = hue.targets.find((x) => x.id === v);
        if (t) b.targets.push({ kind: "hue", rtype: t.rtype, id: t.id, v1: t.v1, name: t.name });
        b._adding = null; redraw();
      });
    } else if (b._adding === "hue" && hue && !hue.available) {
      picker = h("span", { class: "sev-error small" }, hue.error ?? "Hue-Integration nicht verfügbar");
    } else if (b._adding === "shelly") {
      picker = select([["", "Shelly wählen …"], ...shellyTargets.map((t, i) => [i, t.name])], "", (v) => {
        const t = shellyTargets[Number(v)];
        if (t) b.targets.push({ kind: "shelly", ...t });
        b._adding = null; redraw();
      });
    } else if (b._adding === "local") {
      picker = select([["", "Ausgang wählen …"], ...locals.map((c) => [c.index, `${c.key} – ${c.names?.ha_full ?? c.name ?? ""}`])], "", (v) => {
        if (v !== "") b.targets.push({ kind: "local", channel: Number(v) });
        b._adding = null; redraw();
      });
    }
    card.append(...[
      h("div", { class: "wzch-head" }, h("b", { class: "grow" }, `Belegung ${n + 1}`),
        h("button", { class: "icon-btn sm", "aria-label": "Belegung entfernen", onclick: () => { bindings.splice(n, 1); render(); } }, icon(MDI.close, 16))),
      h("label", { class: "field" }, h("span", { class: "flabel" }, "Art"),
        select(Object.entries(TYPES), b.type, (v) => { b.type = v; b.inputs = v === "double" ? [b.inputs[0] ?? 0, b.inputs[1] ?? 1] : [b.inputs[0] ?? 0]; redraw(); })),
      h("div", { class: "wzgrid" }, inputsRow),
      h("div", { class: "field" }, h("span", { class: "flabel" }, "Ziele"),
        b.targets.length ? b.targets.map((t, i) => h("div", { class: "row small" }, h("span", { class: "grow" }, targetLabel(t)),
          h("button", { class: "icon-btn sm", "aria-label": "Ziel entfernen", onclick: () => { b.targets.splice(i, 1); redraw(); } }, icon(MDI.close, 14))))
          : h("span", { class: "muted small" }, "noch kein Ziel"),
        h("div", { class: "row" }, addKind, picker)),
      sceneList.length ? sceneEditor(b, sceneList, redraw) : null,
      b.type === "single" ? h("label", { class: "row small" }, h("input", { type: "checkbox", checked: b.dim !== false, onchange: (e) => { b.dim = e.target.checked; } }),
        "Halten dimmt (nur Hue)") : null,
      st.script_id != null && st.enabled ? testRow(b) : null,
    ].flat().filter((x) => x !== null && x !== undefined && x !== false));
    return card;
  }

  // Szenen für »Links« per Klick-Geste: geordnete Liste, höchstens 3, verschiebbar
  function sceneEditor(b, sceneList, redraw) {
    b.scenes = (b.scenes ?? []).filter((x) => typeof x === "object");
    const move = (i, d) => { const s = b.scenes; [s[i], s[i + d]] = [s[i + d], s[i]]; redraw(); };
    const avail = sceneList.filter((s) => !b.scenes.some((x) => x.id === s.id));
    return h("div", { class: "field" },
      h("span", { class: "flabel" }, "Szenen für »Links« – 1× klicken = 1. Szene, 2× = 2. Szene, 3× = 3. Szene (max. 3; leer = nur einschalten). " +
        "Bei mehreren Szenen schaltet es erst nach dem letzten Klick (ca. 0,5 s)."),
      b.scenes.map((sc, i) => h("div", { class: "row small scene-row" },
        h("span", { class: "badge" }, String(i + 1)), h("span", { class: "grow" }, sc.name ?? sc.id),
        h("button", { class: "icon-btn sm", "aria-label": "nach oben", disabled: i === 0, onclick: () => move(i, -1) }, icon(MDI.sortAsc, 16)),
        h("button", { class: "icon-btn sm", "aria-label": "nach unten", disabled: i === b.scenes.length - 1, onclick: () => move(i, 1) }, icon(MDI.sortDesc, 16)),
        h("button", { class: "icon-btn sm", "aria-label": "entfernen", onclick: () => { b.scenes.splice(i, 1); redraw(); } }, icon(MDI.close, 14)))),
      b.scenes.length < 3 && avail.length ? select([["", "Szene hinzufügen …"], ...avail.map((s) => [s.id, s.name])], "", (v) => {
        const s = sceneList.find((x) => x.id === v);
        if (s) b.scenes.push({ id: s.id, v1: s.v1, group_v1: s.group_v1, name: s.name });
        redraw();
      }) : null);
  }

  function testRow(b) {
    const fire = async (input, event) => {
      try {
        await panel.hass.callWS({ type: "shelly_manager/fallback/test", mac, input, event });
        panel.toast("Ausgelöst – das Script reagiert wie bei ausgefallenem HA.", "ok");
      } catch (err) { panel.toast(err.message ?? String(err), "error"); }
    };
    const btn = (label, input, event) => h("button", { class: "mini", onclick: () => fire(input, event) }, label);
    return h("div", { class: "row wrap small" }, h("span", { class: "muted" }, "Test:"),
      b.type === "double" ? [btn("Links drücken", b.inputs[0], "single_push"), btn("Rechts drücken", b.inputs[1], "single_push"),
        btn("Links halten", b.inputs[0], "long_push"), btn("Rechts halten", b.inputs[1], "long_push")]
        : b.type === "single" ? [btn("Drücken", b.inputs[0], "single_push"), btn("Halten", b.inputs[0], "long_push")]
          : [btn("Umschalten", b.inputs[0], "single_push")]);
  }

  // Dauerhaft aktiv: nur ein KVS-Wert am Gerät (shm_force), das Script bleibt unverändert
  function alwaysToggle() {
    const on = st.always ?? st.always_cfg;
    const cb = h("input", { type: "checkbox", checked: !!on });
    cb.addEventListener("change", async () => {
      try {
        await panel.hass.callWS({ type: "shelly_manager/fallback/always", mac, on: cb.checked });
        st.always = cb.checked;
        panel.toast(cb.checked ? "Fallback dauerhaft aktiv" : "Fallback nur noch, wenn HA nicht erreichbar ist");
        render();
      } catch (err) { cb.checked = !cb.checked; panel.toast(err.message ?? String(err), "error"); }
    });
    return h("div", { class: "field" },
      h("label", { class: "row" }, cb, h("b", {}, "Dauerhaft aktiv – Tasten immer direkt über den Shelly schalten")),
      on ? h("p", { class: "small sev-warning" }, "HA wertet die Tasten weiterhin aus. Damit nichts doppelt schaltet, die Tasten in HA " +
        "(z. B. im Switch Manager) für dieses Gerät deaktivieren.") : h("p", { class: "small muted" },
        "Für Schalter, die unabhängig von HA funktionieren sollen. Umschalten ohne neues Hochladen."));
  }

  let saveError = null;
  function render() {
    const errBox = saveError ? h("div", { class: "donebar bad" }, icon(MDI.error, 18), `Hochladen fehlgeschlagen: ${saveError}`) : null;
    saveError = null;
    const info = st.script_id != null && st.enabled
      ? h("p", { class: "small" }, icon(MDI.ok, 14, "ok"), ` Aktiv (Script ${st.script_id}) · hochgeladen ${fmtAgo(st.deployed_at)}`,
        st.ws_connected === true ? " · WebSocket zu HA verbunden" : st.ws_connected === false ? h("span", { class: "sev-warning" }, " · WebSocket zu HA getrennt") : "",
        st.last_beat ? ` · letzter Herzschlag ${fmtAgo(st.last_beat)}` : "", st.error ? h("span", { class: "sev-error" }, ` · ${st.error}`) : null)
      : h("p", { class: "small muted" }, "Noch nicht aktiv.");
    dlg.setBody(h("div", { class: "form" },
      errBox,
      h("p", { class: "muted small" }, "Solange Home Assistant läuft, tut das Script nichts. Der Shelly hält einen WebSocket zu HA offen – ist der länger als 15 s getrennt " +
        "(HA aus oder Neustart), übernimmt er die Tasten selbst. Als Absicherung dient ein Herzschlag (1×/Minute). Ziel-IPs hält der Manager aktuell."),
      info,
      st.script_id != null && st.enabled ? alwaysToggle() : null,
      !inputs.length ? h("p", { class: "sev-warning" }, "Dieses Gerät hat keine Eingänge.") : null,
      bindings.map((b, n) => bindingCard(b, n)),
      inputs.length ? h("button", { class: "btn", style: { alignSelf: "flex-start" }, onclick: () => {
        bindings.push({ type: "double", inputs: [inputs[0]?.idx ?? 0, inputs[1]?.idx ?? 0], targets: [], scenes: [], dim: true });
        render();
      } }, icon(MDI.tag, 16), "Belegung hinzufügen") : null));
    setActions();
  }

  // Bei jeder Änderung neu prüfen – Kartenänderungen zeichnen nur die Karte, nicht den ganzen Dialog
  function setActions() {
    const missing = bindings.filter((b) => !b.targets.length).length;
    dlg.setActions([
      st.script_id != null ? { label: "Fallback entfernen", onclick: remove } : { label: "Abbrechen" },
      { label: "Schließen" },
      { label: !bindings.length ? "Speichern & hochladen (erst Belegung anlegen)" : missing ? `Speichern & hochladen (${missing} Belegung ohne Ziel)` : "Speichern & hochladen",
        primary: true, disabled: !bindings.length || missing > 0, onclick: save },
    ]);
  }

  async function save() {
    dlg.setBody(loading("Erzeuge Script und lade es auf den Shelly …"));
    dlg.setActions([]);
    try {
      const clean = bindings.map(({ _adding, ...b }) => b);
      const res = await panel.hass.callWS({ type: "shelly_manager/fallback/save", mac, bindings: clean, enabled: true });
      dlg.close();
      panel.toast(res.deployed ? `Fallback gespeichert und auf den Shelly geladen (Script ${res.script_id}${res.rebooted ? ", Gerät neu gestartet" : ""})`
        : "Fallback gespeichert", "ok");
      return;
    } catch (err) {
      saveError = String(err.message ?? err);
    }
    render();
  }

  async function remove() {
    dlg.setBody(loading("Entferne Script …"));
    dlg.setActions([]);
    try {
      await panel.hass.callWS({ type: "shelly_manager/fallback/remove", mac });
      dlg.close();
      panel.toast("Fallback entfernt", "ok");
    } catch (err) {
      saveError = String(err.message ?? err);
      render();
    }
  }

  render();
}
