// Einrichtungs-Assistent: Gerätename → Kanäle → Grundeinstellungen → Zusammenfassung
import { jobView } from "./batch.js";
import { entityDialog, modal } from "./dialogs.js";
import { MDI, h, icon, spinner } from "./util.js";

const loading = (text) => h("p", { class: "row muted" }, spinner(16), text);
const OUT = ["switch", "light", "rgb", "rgbw", "cct", "cover"];
const strip = (name, area) => {
  const n = (name ?? "").trim();
  return area && n.toLowerCase().startsWith(area.toLowerCase() + " ") ? n.slice(area.length).trim() : n;
};
const full = (area, name) => [area, name].filter(Boolean).join(" ").trim();

export async function wizardDialog(panel, mac) {
  const r = panel.byMac.get(mac);
  if (!r) return;
  const dlg = modal(panel, { title: `Einrichten: ${r.name}`, body: loading("Lade …"), wide: true });
  const [{ settings: catalog }, { changes: profile }, { backup }] = await Promise.all([
    panel.hass.callWS({ type: "shelly_manager/settings/catalog" }),
    panel.hass.callWS({ type: "shelly_manager/profile" }),
    panel.hass.callWS({ type: "shelly_manager/reset/backup", mac }).catch(() => ({ backup: null })),
  ]);
  const byId = Object.fromEntries(catalog.map((s) => [s.id, s]));
  const areas = panel.areas ?? [];
  const areaName = (id) => areas.find((a) => a.id === id)?.name ?? "";
  const outs = r.channels.filter((c) => OUT.includes(c.component));
  const multi = outs.length > 1;

  // Zustand des Assistenten
  const st = {
    area: r.area_id ?? "",
    name: strip(r.ha_name ?? r.shelly_name ?? "", r.area),
    channels: outs.map((c) => {
      const n = c.names;
      return {
        key: c.key, comp: c.component, idx: c.index,
        area: (areas.find((a) => a.name === n?.ha_area)?.id) ?? r.area_id ?? "",
        name: strip(n?.ha_name ?? n?.shelly ?? "", n?.ha_area),
        initial_state: c.initial_state, in_mode: c.in_mode, ctype: c.consumption_type ?? "",
        orig: { initial_state: c.initial_state, in_mode: c.in_mode, ctype: c.consumption_type ?? "" },
      };
    }),
    profile: profile.map((p) => ({ ...p, on: true })),
    saveProfile: false,
    checkIds: true,
  };
  let step = 0;

  // Sicherung vom Zurücksetzen: Namen, Bereiche und Kanal-Einstellungen übernehmen
  let restored = false;
  function restore() {
    const cfg = backup.config ?? {};
    const ha = backup.ha ?? {};
    const ctypes = cfg.sys?.ui_data?.consumption_types ?? [];
    if (ha.area_id && areas.some((a) => a.id === ha.area_id)) st.area = ha.area_id;
    st.name = ha.name_by_user || strip(cfg.sys?.device?.name ?? "", areaName(st.area)) || st.name;
    for (const c of st.channels) {
      const bc = cfg[c.key] ?? {};
      const hc = (ha.channels ?? {})[c.key] ?? {};
      if (hc.area_id && areas.some((a) => a.id === hc.area_id)) c.area = hc.area_id;
      if (multi) c.name = hc.name_by_user || strip(bc.name ?? "", areaName(c.area)) || c.name;
      if (bc.initial_state != null && c.initial_state != null) c.initial_state = bc.initial_state;
      if (bc.in_mode && c.in_mode) c.in_mode = bc.in_mode;
      if (c.comp === "switch" && ctypes[c.idx] !== undefined) c.ctype = ctypes[c.idx] ?? "";
    }
    restored = true;
    render();
  }
  const backupBox = () => backup ? h("div", { class: `small ${restored ? "ok" : ""}`, style: { marginBottom: "10px" } },
    restored ? [icon(MDI.ok, 14), " Werte aus der Sicherung übernommen – bitte prüfen."]
      : [h("span", {}, `Sicherung vom ${new Date(backup.ts * 1000).toLocaleString()} gefunden (»${backup.config?.sys?.device?.name ?? "ohne Namen"}«). `),
        h("button", { class: "btn", style: { height: "26px" }, onclick: restore }, icon(MDI.restore, 14), "Namen und Einstellungen übernehmen")]) : null;

  const areaSelect = (value, onchange) => h("select", { onchange: (e) => onchange(e.target.value) },
    h("option", { value: "", selected: !value }, "(kein Bereich)"),
    areas.map((a) => h("option", { value: a.id, selected: a.id === value }, a.name)));
  const enumSelect = (sid, value, onchange) => h("select", { onchange: (e) => onchange(e.target.value) },
    byId[sid].options.map((o) => h("option", { value: o.value, selected: o.value === (value ?? "") }, o.label)));
  const ruleBox = (name, area, kind) => {
    const box = h("div", { class: "small" });
    if (name.trim()) panel.hass.callWS({ type: "shelly_manager/names/check", name: full(area, name), area, kind }).then((res) => {
      box.replaceChildren(res.issues.length
        ? h("span", { class: "sev-warning" }, icon(MDI.alert, 14), " ", res.issues.map((x) => x.text).join(" · "))
        : h("span", { class: "ok" }, icon(MDI.ok, 14), " entspricht den Namensregeln"));
    });
    return box;
  };

  const STEPS = [
    { title: "Gerätename", render: () => {
      const preview = h("div", { class: "small muted" });
      let rules = ruleBox(st.name, areaName(st.area), r.kind);
      const upd = () => {
        preview.textContent = `Shelly: »${full(areaName(st.area), st.name)}« · HA: »${st.name}« im Bereich ${areaName(st.area) || "–"}`;
        const nb = ruleBox(st.name, areaName(st.area), r.kind);
        rules.replaceWith(nb);
        rules = nb;
      };
      const nameIn = h("input", { type: "text", value: st.name, placeholder: "z. B. Steckdose West", oninput: (e) => { st.name = e.target.value; upd(); } });
      setTimeout(upd, 0);
      return h("div", { class: "form" },
        h("label", { class: "field" }, h("span", { class: "flabel" }, "Bereich"), areaSelect(st.area, (v) => { st.area = v; upd(); })),
        h("label", { class: "field" }, h("span", { class: "flabel" }, "Gerätename (ohne Bereich)"), nameIn),
        preview, rules);
    } },
    { title: "Kanäle", skip: () => !outs.length, render: () => h("div", { class: "form" },
      multi ? h("p", { class: "muted small" }, "Mehrkanal-Gerät: jeder Kanal bekommt einen eigenen Namen und kann einen anderen Bereich haben.")
        : h("p", { class: "muted small" }, "Einkanal-Gerät: der Kanal trägt keinen eigenen Namen (er heißt wie das Gerät)."),
      st.channels.map((c) => h("div", { class: "wzch" },
        h("div", { class: "wzch-head" }, h("b", {}, c.key), multi ? null : h("span", { class: "muted small" }, full(areaName(st.area), st.name))),
        multi ? h("div", { class: "wzgrid" },
          h("label", { class: "field" }, h("span", { class: "flabel" }, "Bereich"), areaSelect(c.area, (v) => { c.area = v; })),
          h("label", { class: "field" }, h("span", { class: "flabel" }, "Name (ohne Bereich)"),
            h("input", { type: "text", value: c.name, oninput: (e) => { c.name = e.target.value; } }))) : null,
        h("div", { class: "wzgrid" },
          c.initial_state !== undefined && c.initial_state !== null ? h("label", { class: "field" }, h("span", { class: "flabel" }, "Nach Stromausfall"),
            enumSelect("initial_state", c.initial_state, (v) => { c.initial_state = v; })) : null,
          c.in_mode ? h("label", { class: "field" }, h("span", { class: "flabel" }, "Eingangsmodus"),
            enumSelect("in_mode", c.in_mode, (v) => { c.in_mode = v; })) : null,
          c.comp === "switch" ? h("label", { class: "field" }, h("span", { class: "flabel" }, "Gerätetyp für Drittsysteme"),
            enumSelect("consumption_type", c.ctype, (v) => { c.ctype = v; })) : null),
        c.comp === "switch" ? h("p", { class: "muted small" }, "»Licht« lässt HA den Kanal als Licht statt als Schalter anlegen.") : null))) },
    { title: "Grundeinstellungen", render: () => h("div", { class: "form" },
      h("p", { class: "muted small" }, "Basis-Profil für neue Geräte. Nicht unterstützte Einstellungen überspringt das Gerät automatisch."),
      st.profile.map((p) => {
        const s = byId[p.id];
        const cb = h("input", { type: "checkbox", checked: p.on, onchange: (e) => { p.on = e.target.checked; } });
        const val = s.type === "bool"
          ? h("select", { onchange: (e) => { p.value = e.target.value === "true"; } },
            h("option", { value: "true", selected: p.value === true }, "an"), h("option", { value: "false", selected: p.value === false }, "aus"))
          : h("input", { type: "text", value: p.value ?? "", oninput: (e) => { p.value = e.target.value; } });
        return h("label", { class: "row" }, cb, h("span", { class: "grow" }, s?.label ?? p.id), val);
      }),
      h("label", { class: "row small" }, h("input", { type: "checkbox", checked: st.saveProfile, onchange: (e) => { st.saveProfile = e.target.checked; } }),
        "Als Standard für weitere neue Geräte speichern")) },
    { title: "Zusammenfassung", render: () => {
      const lines = [`Gerät: Shelly »${full(areaName(st.area), st.name)}«, HA »${st.name}« in ${areaName(st.area) || "–"}`];
      for (const c of st.channels) {
        const bits = [];
        if (multi) bits.push(`»${full(areaName(c.area), c.name)}«`);
        if (c.initial_state !== c.orig.initial_state) bits.push(`nach Stromausfall: ${c.initial_state}`);
        if (c.in_mode !== c.orig.in_mode) bits.push(`Eingang: ${c.in_mode}`);
        if (c.ctype !== c.orig.ctype) bits.push(`Typ: ${c.ctype || "(keiner)"}`);
        if (bits.length) lines.push(`${c.key}: ${bits.join(", ")}`);
      }
      const prof = st.profile.filter((p) => p.on);
      if (prof.length) lines.push(`Grundeinstellungen: ${prof.map((p) => `${byId[p.id]?.label} ${p.value === true ? "an" : p.value === false ? "aus" : p.value}`).join(", ")}`);
      return h("div", { class: "form" },
        h("ul", { class: "small steps" }, lines.map((l) => h("li", {}, l))),
        h("label", { class: "row small" }, h("input", { type: "checkbox", checked: st.checkIds, onchange: (e) => { st.checkIds = e.target.checked; } }),
          "Danach Entity-IDs prüfen"),
        h("p", { class: "muted small" }, "Shelly Cloud: Falls gewünscht, das Gerät danach in der Shelly-App hinzufügen (landet im Standardraum)."));
    } },
  ];

  const visible = () => STEPS.filter((s) => !s.skip?.());
  function render() {
    const steps = visible();
    const cur = steps[step];
    dlg.setBody(h("div", {},
      h("div", { class: "wzsteps" }, steps.map((s, i) => h("span", { class: `wzstep${i === step ? " on" : i < step ? " done" : ""}` }, `${i + 1}. ${s.title}`))),
      step === 0 ? backupBox() : null,
      cur.render()));
    const last = step === steps.length - 1;
    dlg.setActions([
      step ? { label: "Zurück", onclick: () => { step--; render(); } } : { label: "Abbrechen" },
      last ? { label: "Anwenden", primary: true, onclick: apply } : { label: "Weiter", primary: true, onclick: () => { step++; render(); } },
    ]);
  }

  async function apply() {
    dlg.setBody(loading("Schreibe Namen …"));
    dlg.setActions([]);
    try {
      const devName = full(areaName(st.area), st.name);
      if (st.name.trim() && (devName !== r.shelly_name || (st.area || null) !== (r.area_id || null) || st.name !== r.ha_name)) {
        await panel.hass.callWS({ type: "shelly_manager/names/set", mac, key: "device", name: full(areaName(st.area), st.name), area_id: st.area || null });
      }
      // Einkanaler: Kanalname leeren (Namensregel); Mehrkanaler: voller Name je Kanal
      for (const c of st.channels) {
        if (multi && c.name.trim()) {
          await panel.hass.callWS({ type: "shelly_manager/names/set", mac, key: c.key, name: full(areaName(c.area), c.name), area_id: c.area || null });
        } else if (!multi && (r.names.channels.find((x) => x.key === c.key)?.shelly ?? "")) {
          await panel.hass.callWS({ type: "shelly_manager/names/set", mac, key: c.key, name: "" });
        }
      }
      const changes = [];
      for (const c of st.channels) {
        if (c.initial_state !== c.orig.initial_state) changes.push({ id: "initial_state", value: c.initial_state, channel: c.idx });
        if (c.in_mode !== c.orig.in_mode) changes.push({ id: "in_mode", value: c.in_mode, channel: c.idx });
        if (c.ctype !== c.orig.ctype) changes.push({ id: "consumption_type", value: c.ctype, channel: c.idx });
      }
      const prof = st.profile.filter((p) => p.on).map(({ id, value }) => ({ id, value }));
      if (st.saveProfile) await panel.hass.callWS({ type: "shelly_manager/profile", changes: prof });
      changes.push(...prof);
      await panel.hass.callWS({ type: "shelly_manager/new/dismiss", mac });
      if (changes.length) {
        const { job_id } = await panel.hass.callWS({ type: "shelly_manager/batch/apply", macs: [mac], changes, label: `Einrichtung ${full(areaName(st.area), st.name)}` });
        jobView(panel, dlg, job_id, byId);
        if (st.checkIds) {
          const off = panel.onJob((job) => {
            if (job.id === job_id && job.state !== "running") { off(); dlg.close(); setTimeout(() => entityDialog(panel, [mac]), 1500); }
          });
        }
      } else {
        dlg.close();
        if (st.checkIds) entityDialog(panel, [mac]);
      }
    } catch (err) {
      dlg.setBody(h("p", { class: "sev-error" }, String(err.message ?? err)));
      dlg.setActions([{ label: "Schließen" }]);
    }
  }

  render();
}
