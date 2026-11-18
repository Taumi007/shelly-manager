// Styles: nur HA-Theme-Variablen, dadurch automatisch Hell/Dunkel (wie Stromnetz)
export const STYLE = `
:host {
  --sm-bg: var(--primary-background-color);
  --sm-card: var(--card-background-color, var(--ha-card-background));
  --sm-text: var(--primary-text-color);
  /* Sekundärtext etwas heller als das Theme – besser lesbar, aber klar vom Haupttext abgesetzt */
  --sm-sub: color-mix(in srgb, var(--secondary-text-color) 68%, var(--primary-text-color) 32%);
  --sm-div: var(--divider-color);
  --sm-accent: var(--primary-color);
  --sm-raised: color-mix(in srgb, var(--sm-card) 88%, var(--sm-text) 12%);
  --sm-hover: color-mix(in srgb, var(--sm-card) 82%, var(--sm-text) 18%);
  --sm-sel: color-mix(in srgb, var(--sm-card) 78%, var(--sm-accent) 22%);
  --sm-ok: var(--success-color, #43a047);
  --sm-warn: var(--warning-color, #ffa600);
  --sm-err: var(--error-color, #db4437);
  --sm-info: var(--info-color, #039be5);
  display: flex; flex-direction: column; height: 100vh; height: 100dvh; overflow: hidden; background: var(--sm-bg); color: var(--sm-text);
  font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif); font-size: 14px;
}
* { box-sizing: border-box; }
button { font: inherit; color: inherit; cursor: pointer; }
input, select { font: inherit; color: var(--sm-text); background: var(--sm-raised); border: 1px solid var(--sm-div); border-radius: 8px; padding: 7px 10px; min-width: 0; }
input:focus, select:focus { outline: 2px solid var(--sm-accent); outline-offset: -1px; }
input[type=checkbox] { width: 16px; height: 16px; min-width: 16px; flex: none; padding: 0; margin: 2px 0 0; accent-color: var(--sm-accent); cursor: pointer; }
label.row:has(> span > div) { align-items: flex-start; }
a { color: var(--sm-accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.ico { flex: none; }
.muted { color: var(--sm-sub); }
.small { font-size: 12px; }
.grow { flex: 1; min-width: 0; }
.row { display: flex; align-items: center; gap: 8px; }
.row.wrap { flex-wrap: wrap; }
.empty { padding: 32px; color: var(--sm-sub); }
.sev-error { color: var(--sm-err); }
.sev-warning { color: var(--sm-warn); }
.sev-info { color: var(--sm-info); }
/* WLAN-Fehler: WLAN-Symbol mit »!« in der Farbe der Schwere (rot/gelb/blau) */
.wifiprob { position: relative; display: inline-flex; align-items: center; }
.wifiprob > svg:first-child + * { margin-left: 4px; }
.wifiprob::before { content: "!"; position: absolute; left: 10px; top: -5px; font-size: 10px; font-weight: 900; line-height: 1; }
.ok { color: var(--sm-ok); }
.spin { animation: spin 1s linear infinite; color: var(--sm-accent); }
@keyframes spin { to { transform: rotate(360deg); } }
.spinner { display: inline-block; box-sizing: border-box; flex: none; vertical-align: middle; border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--sm-accent) 25%, transparent); border-top-color: var(--sm-accent);
  animation: spin .8s linear infinite; }

header { display: flex; align-items: center; gap: 10px; height: var(--header-height, 56px); padding: 0 12px 0 8px; background: var(--app-header-background-color, var(--sm-card)); color: var(--app-header-text-color, var(--sm-text)); border-bottom: 1px solid var(--sm-div); min-width: 0; overflow: hidden; }
.menu-btn { display: none; }
:host([narrow]) .menu-btn { display: inline-flex; }
.brand { display: flex; align-items: center; gap: 8px; font-size: 20px; padding-left: 8px; white-space: nowrap; }
.brand .ico { color: var(--sm-accent); }
.tabs { display: flex; align-self: stretch; margin-left: 8px; }
.tab { background: none; border: 0; border-bottom: 2px solid transparent; padding: 0 14px; color: var(--sm-sub); font-weight: 500; white-space: nowrap; }
.tab.active { color: var(--sm-text); border-bottom-color: var(--sm-accent); }
.tab .badge { margin-left: 6px; }
.search { display: flex; align-items: center; gap: 6px; padding: 0 10px; height: 36px; border-radius: 18px; background: var(--sm-raised); border: 1px solid var(--sm-div); color: var(--sm-sub); flex: 0 1 300px; min-width: 120px; }
.search input { background: none; border: 0; padding: 0; width: 100%; outline: none; }
.btn { display: inline-flex; align-items: center; gap: 6px; height: 36px; padding: 0 14px; border-radius: 18px; border: 1px solid var(--sm-div); background: transparent; font-weight: 500; white-space: nowrap; }
.btn:hover:not(:disabled) { background: var(--sm-hover); }
.btn.primary { background: var(--sm-accent); border-color: var(--sm-accent); color: var(--text-primary-color, #fff); }
.newitem:has(.newbackup) { flex-wrap: wrap; }
.newbackup { flex-basis: 100%; display: flex; gap: 8px; align-items: center; margin: 2px 0 4px 0; padding: 4px 8px; border-radius: 6px; background: color-mix(in srgb, var(--sm-accent) 10%, transparent); }
.refsrc { border-top: 1px solid var(--divider-color); padding: 6px 0; }
.refsrc .linkish { display: flex; align-items: center; gap: 4px; background: none; border: 0; color: inherit; cursor: pointer; text-align: left; padding: 2px 0; font: inherit; }
.reflist { margin: 4px 0 4px 26px; max-height: 260px; overflow: auto; }
.refrow { display: flex; gap: 8px; align-items: baseline; padding: 2px 0; }
.refrow > span { min-width: 0; overflow-wrap: anywhere; }
.refwhere { flex: 0 0 auto; max-width: 30%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.refctx { white-space: pre-wrap; word-break: break-all; }
.refctx del { color: var(--sm-err); text-decoration: line-through; }
.refctx ins { color: var(--sm-ok, #2e7d32); text-decoration: none; font-weight: 600; }
.btn.danger { background: var(--sm-err); border-color: var(--sm-err); color: #fff; }
.btn.danger:disabled { opacity: .5; }
.rsteps label { display: flex; gap: 8px; align-items: flex-start; padding: 3px 0; }
.rsteps label.off { opacity: .5; }
.btn:disabled { opacity: .45; cursor: default; }
.split { display: inline-flex; }
.split .btn:first-child { border-radius: 18px 0 0 18px; padding-right: 10px; }
.split .btn:last-child { border-radius: 0 18px 18px 0; border-left: 0; padding: 0 8px; }
.icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px; border-radius: 50%; border: 0; background: transparent; flex: none; }
.icon-btn:hover { background: var(--sm-hover); }
.icon-btn.sm { width: 28px; height: 28px; }
.chip { display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 10px 0 12px; border-radius: 15px; border: 1px solid var(--sm-div); background: var(--sm-card); font-size: 13px; white-space: nowrap; }
.chip.active { background: var(--sm-sel); border-color: var(--sm-accent); }
.chip button { display: inline-flex; border: 0; background: none; padding: 0; color: var(--sm-sub); }
.chip.dashed { border-style: dashed; color: var(--sm-sub); cursor: pointer; }
.badge { display: inline-flex; align-items: center; justify-content: center; min-width: 20px; height: 20px; padding: 0 6px; border-radius: 10px; font-size: 11.5px; font-weight: 600; background: var(--sm-raised); color: var(--sm-text); }
.badge.err { background: var(--sm-err); color: #fff; }
.badge.warn { background: var(--sm-warn); color: #000; }
.badge.accent { background: var(--sm-accent); color: var(--text-primary-color, #fff); }
.tag { display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 11.5px; background: var(--sm-raised); white-space: nowrap; }
.tag.on { background: color-mix(in srgb, var(--sm-ok) 22%, transparent); }
.tag.off { color: var(--sm-sub); }
.tag.bad { background: color-mix(in srgb, var(--sm-err) 22%, transparent); }
.tag.upd { background: color-mix(in srgb, var(--sm-accent) 22%, transparent); }

.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 16px; border-bottom: 1px solid var(--sm-div); flex-wrap: wrap; min-height: 48px; }
.toolbar .count { color: var(--sm-sub); font-size: 13px; margin-right: 4px; }
.toolbar[hidden] { display: none; }
.newcard { margin: 10px 16px 0; padding: 10px 14px; border: 1px solid var(--sm-accent); border-radius: 12px; background: var(--sm-sel); display: flex; flex-direction: column; gap: 10px; }
.newcard[hidden] { display: none; }
.newhead { display: flex; align-items: center; gap: 8px; font-weight: 500; margin-bottom: 4px; }
.newitem { display: flex; align-items: center; gap: 10px; padding: 4px 0 4px 24px; font-size: 13.5px; }
.wzsteps { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; }
.wzstep { font-size: 12.5px; padding: 3px 10px; border-radius: 12px; background: var(--sm-raised); color: var(--sm-sub); }
.wzstep.on { background: var(--sm-accent); color: var(--text-primary-color, #fff); }
.wzstep.done { color: var(--sm-text); }
.wzch { border: 1px solid var(--sm-div); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.wzch-head { display: flex; gap: 10px; align-items: baseline; }
.wzgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }
.field { display: flex; flex-direction: column; gap: 4px; }
.flabel { font-size: 12px; color: var(--sm-sub); }
.selbar { background: var(--sm-sel); }
.banner { padding: 8px 16px; background: var(--sm-sel); border-bottom: 1px solid var(--sm-accent); display: flex; align-items: center; gap: 10px; }
.banner[hidden] { display: none; }
.progress { flex: 0 0 160px; height: 6px; border-radius: 3px; background: var(--sm-raised); overflow: hidden; }
.progress i { display: block; height: 100%; background: var(--sm-accent); transition: width .3s; }

main { flex: 1; min-height: 0; position: relative; display: flex; }
.view { flex: 1; min-width: 0; overflow: auto; }

/* Tabelle */
table { border-collapse: separate; border-spacing: 0; width: max-content; min-width: 100%; }
thead th { position: sticky; top: 0; z-index: 2; background: var(--sm-card); border-bottom: 1px solid var(--sm-div); text-align: left; font-weight: 500; font-size: 12.5px; color: var(--sm-sub); padding: 0; white-space: nowrap; }
thead th .th { display: flex; align-items: center; gap: 2px; padding: 10px 8px; cursor: pointer; user-select: none; }
thead th .th:hover { color: var(--sm-text); }
thead th.sorted, thead th.filtered { color: var(--sm-text); }
thead th.filtered .th::after { content: ""; width: 6px; height: 6px; border-radius: 3px; background: var(--sm-accent); margin-left: 4px; }
th.fix, td.fix { position: sticky; z-index: 1; background: var(--sm-card); }
thead th.fix { z-index: 3; }
th.c-sel, td.c-sel { left: 0; width: 36px; min-width: 36px; text-align: center; padding: 0 6px; }
.probcount { font-weight: 600; }
th.c-ico, td.c-ico { width: 28px; min-width: 28px; max-width: 28px; text-align: center; padding-left: 2px; padding-right: 2px; }
th.c-ico .th { justify-content: center; }
th.c-state, td.c-state { left: 36px; width: 30px; min-width: 30px; text-align: center; padding: 0; }
th.c-name, td.c-name { left: 66px; border-right: 1px solid var(--sm-div); }
tbody td { padding: 6px 8px; border-bottom: 1px solid color-mix(in srgb, var(--sm-div) 60%, transparent); white-space: nowrap; vertical-align: middle; max-width: 340px; overflow: hidden; text-overflow: ellipsis; background: var(--sm-bg); }
tbody td.fix { background: var(--sm-bg); }
tbody tr.dev { cursor: pointer; }
tbody tr.dev:hover td { background: var(--sm-hover); }
tbody tr.sel td { background: var(--sm-sel); }
tbody tr.open td { box-shadow: inset 0 2px 0 var(--sm-accent); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.nmwrap { display: inline-flex; align-items: center; gap: 0.3em; max-width: 100%; }
.nmwrap > span:not(.areatag):not(.warnmark) { overflow: hidden; text-overflow: ellipsis; }
/* Bereich als normaler Text, nur blaugrau eingefärbt – damit Shelly- und HA-Namen bündig untereinander stehen */
.areatag { flex: none; color: color-mix(in srgb, var(--sm-text) 62%, var(--sm-accent) 38%); }
.warnmark { display: inline-flex; flex: none; }
.namecell { display: flex; align-items: center; gap: 4px; min-width: 220px; max-width: 360px; }
.namecell .nm { overflow: hidden; text-overflow: ellipsis; font-weight: 500; }
.namecell .sub { color: var(--sm-sub); font-size: 12px; overflow: hidden; text-overflow: ellipsis; }
.namecell .txt { display: flex; flex-direction: column; min-width: 0; line-height: 1.25; }
.exp { width: 22px; height: 22px; border: 0; background: none; padding: 0; display: inline-flex; align-items: center; justify-content: center; color: var(--sm-sub); border-radius: 4px; flex: none; }
.exp:hover { background: var(--sm-raised); }
.exp.none { visibility: hidden; }
.dots { display: inline-flex; gap: 3px; align-items: center; }
.dot { width: 10px; height: 10px; border-radius: 5px; border: 1px solid var(--sm-sub); background: transparent; display: inline-block; }
.dot.on { background: var(--state-active-color, var(--sm-accent)); border-color: transparent; }
.dot.na { border-style: dashed; opacity: .5; }
.rssi { display: inline-flex; align-items: center; gap: 4px; }
.rssi.good { color: var(--sm-ok); } .rssi.mid { color: var(--sm-warn); } .rssi.bad { color: var(--sm-err); }
tr.chrow td { max-width: none; overflow: visible; background: color-mix(in srgb, var(--sm-bg) 92%, var(--sm-text) 8%); cursor: default; }
.chgrid { display: grid; grid-template-columns: 70px minmax(180px, 1.3fr) minmax(180px, 1.3fr) 28px 70px 80px 110px 110px 90px; gap: 2px 12px; align-items: center; padding: 4px 0 6px 64px; font-size: 13px; }
.chgrid .hd { font-size: 11.5px; color: var(--sm-sub); }
.chgrid > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* Seitenpanel */
.drawer { flex: none; width: 0; overflow: hidden; background: var(--sm-card); border-left: 1px solid var(--sm-div); position: relative; }
.drawer.open { width: 460px; overflow-y: auto; }
.drawer .close { position: absolute; right: 8px; top: 8px; }
.dtools { position: absolute; right: 8px; top: 8px; display: flex; gap: 2px; }
.sechead { display: flex; align-items: center; gap: 6px; }
.nblocks { display: flex; flex-direction: column; gap: 8px; }
.nblock { border: 1px solid color-mix(in srgb, var(--sm-div) 70%, transparent); border-radius: 8px; padding: 6px 8px 8px 10px; display: flex; flex-direction: column; gap: 3px; font-size: 13px; }
.nhead { display: flex; align-items: center; gap: 8px; }
.nkey { font-size: 12px; color: var(--sm-sub); }
.nline { display: grid; grid-template-columns: 78px minmax(0, 1fr); gap: 8px; align-items: center; }
.nlab { font-size: 12px; color: var(--sm-sub); }
.nval { min-width: 0; }
.nhint { font-size: 12px; padding-left: 86px; }
.ncmp.ncmp4 { grid-template-columns: 22px minmax(0, 1fr) minmax(0, 1fr) 28px; }
.ncmp.ncmp4 .lbl { grid-column: 1 / -1; }
.ncmp.ncmp4 .hint { grid-column: 2 / -1; }
.dbody { display: flex; flex-direction: column; gap: 16px; padding: 18px 20px 28px; }
.dtitle { font-size: 18px; font-weight: 500; padding-right: 76px; }
.links { display: flex; flex-wrap: wrap; gap: 6px; }
.links .btn { height: 32px; padding: 0 12px; font-size: 13px; }
.sec h4 { margin: 0 0 8px; font-size: 13px; font-weight: 500; color: var(--sm-sub); text-transform: uppercase; letter-spacing: .4px; }
.kv { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 5px 12px; font-size: 13px; }
.kv > :nth-child(odd) { color: var(--sm-sub); }
.kv > span { overflow-wrap: anywhere; }
.plist { display: flex; flex-direction: column; gap: 6px; }
.pitem { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; }
.pitem .mini { flex: none; height: 24px; padding: 0 8px; font-size: 12px; white-space: nowrap; }
.ncmp { display: grid; grid-template-columns: 22px minmax(0, 1fr) minmax(0, 1fr); gap: 4px 10px; font-size: 13px; align-items: start; }
.ncmp .hd { font-size: 11.5px; color: var(--sm-sub); }
.ncmp .lbl { grid-column: 1 / -1; font-size: 11.5px; color: var(--sm-sub); margin-top: 6px; }
.ncmp .hint { grid-column: 2 / -1; font-size: 12px; }
.ncmp .area { color: var(--sm-sub); }
.chlist { display: flex; flex-direction: column; gap: 4px; }
.chitem { display: flex; align-items: center; gap: 10px; padding: 6px; border-radius: 6px; font-size: 13px; background: none; border: 0; text-align: left; }
.chitem:hover { background: var(--sm-hover); }
pre.raw { margin: 0; max-height: 360px; overflow: auto; font-size: 11.5px; background: var(--sm-raised); padding: 10px; border-radius: 8px; }
details summary { cursor: pointer; color: var(--sm-sub); font-size: 13px; padding: 4px 0; }

/* Probleme (vertikale Liste) */
.plist-v { max-width: 980px; margin: 0 auto; padding: 16px 24px 32px; display: flex; flex-direction: column; gap: 6px; }
.psum { padding: 0 4px 6px; }
.psection { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
.pshead { display: flex; align-items: baseline; gap: 10px; padding: 4px 4px 2px; font-weight: 500; font-size: 13.5px; color: var(--sm-text); }
.pgroup.pblocked { opacity: .6; }
.pdev.waiting { opacity: .55; }
.pgroup { background: var(--sm-card); border: 1px solid var(--sm-div); border-radius: 10px; padding: 0; display: block; }
.pgroup > summary { list-style: none; display: flex; align-items: center; gap: 10px; padding: 10px 14px; cursor: pointer; color: var(--sm-text); font-size: 14px; }
.pgroup > summary::-webkit-details-marker { display: none; }
.pgroup > summary::after { content: "›"; color: var(--sm-sub); font-size: 18px; transition: transform .15s; }
.pgroup[open] > summary::after { transform: rotate(90deg); }
.pgroup > summary:hover { background: var(--sm-hover); border-radius: 10px; }
.pgroup.sev-error-group { border-color: color-mix(in srgb, var(--sm-err) 55%, var(--sm-div)); }
.fixbtn { height: 28px; padding: 0 10px; font-size: 12.5px; }
.rnitem { display: flex; gap: 10px; align-items: flex-start; padding: 8px 2px; border-bottom: 1px solid color-mix(in srgb, var(--sm-div) 50%, transparent); }
.rnitem input[type=checkbox] { margin-top: 3px; }
.ptitle { font-weight: 500; }
.pcount { color: var(--sm-sub); font-size: 13px; white-space: nowrap; }
.pgroup[open] .pbody { margin-top: 0; }
.pbody { display: flex; flex-direction: column; gap: 2px; padding: 0 14px 10px 42px; border-top: 1px solid var(--sm-div); padding-top: 8px; }
.pfoot { margin-top: 6px; }
/* Probleme */
.problems { padding: 16px 24px 32px; display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: 14px; align-content: start; }
.pcard { background: var(--sm-card); border: 1px solid var(--sm-div); border-radius: 12px; padding: 12px 14px; display: flex; flex-direction: column; gap: 6px; }
.pcard h3 { margin: 0; font-size: 14.5px; font-weight: 500; display: flex; gap: 8px; align-items: center; }
.pdev { display: flex; justify-content: space-between; gap: 8px; background: none; border: 0; text-align: left; padding: 4px 6px; border-radius: 6px; font-size: 13px; }
.pdev:hover { background: var(--sm-hover); }

/* Menü, Popover, Hinweis */
.menu { position: fixed; display: none; z-index: 20; min-width: 240px; max-width: 360px; max-height: calc(100vh - 16px); overflow-y: auto; padding: 6px 0; background: var(--sm-card); border: 1px solid var(--sm-div); border-radius: 10px; box-shadow: 0 12px 32px rgba(0,0,0,.4); }
.menu.open { display: block; }
.menu-title { padding: 6px 16px 8px; font-size: 12px; color: var(--sm-sub); border-bottom: 1px solid var(--sm-div); margin-bottom: 4px; }
.menu > button, .menu .mi { display: flex; align-items: center; gap: 12px; width: 100%; min-height: 36px; padding: 0 16px; border: 0; background: transparent; text-align: left; }
.menu > button:hover:not(:disabled), .menu label.mi:hover { background: var(--sm-hover); }
.menu > button:disabled { opacity: .4; cursor: default; }
.menu .ico { color: var(--sm-sub); }
.menu .sep { height: 1px; margin: 4px 0; background: var(--sm-div); }
.menu .grp { padding: 8px 16px 2px; font-size: 11.5px; color: var(--sm-sub); text-transform: uppercase; letter-spacing: .4px; }
.menu .pad { padding: 6px 16px; display: flex; flex-direction: column; gap: 8px; }
.menu label.mi { cursor: pointer; }
.menu .mi .cnt { margin-left: auto; color: var(--sm-sub); font-size: 12px; }
.menu .mi .lbl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tip { position: fixed; z-index: 50; max-width: 380px; padding: 7px 11px; border-radius: 7px; font-size: 12.5px; line-height: 1.45; pointer-events: none; opacity: 0; transition: opacity .1s; background: rgba(28, 30, 33, .96); color: #e8eaed; border: 1px solid rgba(255,255,255,.12); box-shadow: 0 6px 18px rgba(0,0,0,.45); }
.tip .th { font-weight: 600; margin-bottom: 2px; }
.tip ul { margin: 0; padding-left: 16px; }
.tip li { margin: 1px 0; }
.tip.show { opacity: 1; }
.toast { position: fixed; left: 50%; bottom: 24px; transform: translate(-50%, 40px); opacity: 0; transition: .2s; background: #2b2f36; color: #fff; padding: 10px 16px; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,.35); pointer-events: none; z-index: 60; max-width: 80vw; display: flex; gap: 8px; align-items: center; }
.toast.ok svg { color: #66bb6a; }
.toast.error { background: var(--sm-err); color: #fff; }
.donebar { display: flex; gap: 8px; align-items: center; margin-top: 10px; padding: 8px 10px; border-radius: 8px; font-weight: 600; }
.donebar.ok { background: color-mix(in srgb, var(--sm-ok, #4caf50) 15%, transparent); }
.donebar.bad { background: color-mix(in srgb, var(--sm-err) 15%, transparent); }
.toast.show { opacity: 1; transform: translate(-50%, 0); }

/* Dialog */
.modal-bg { position: fixed; inset: 0; z-index: 40; background: rgba(0,0,0,.45); display: flex; align-items: center; justify-content: center; padding: 16px; }
.modal { background: var(--sm-card); border-radius: 14px; box-shadow: 0 16px 48px rgba(0,0,0,.5); width: min(560px, 100%); max-height: 88vh; display: flex; flex-direction: column; }
.modal.wide { width: min(920px, 100%); }
.mhead { display: flex; align-items: center; gap: 8px; padding: 14px 12px 10px 20px; font-size: 17px; font-weight: 500; border-bottom: 1px solid var(--sm-div); }
.mbody { padding: 14px 20px; overflow: auto; flex: 1; min-height: 80px; }
.mfoot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 20px; border-top: 1px solid var(--sm-div); }
.mfoot:empty { display: none; }
.oplist { display: flex; flex-direction: column; gap: 2px; }
.opdev { font-weight: 500; margin-top: 10px; padding-bottom: 2px; border-bottom: 1px solid var(--sm-div); }
.op { display: grid; grid-template-columns: 18px auto 80px minmax(0, 1fr) 16px minmax(0, 1fr) auto; gap: 8px; align-items: center; padding: 4px 2px; font-size: 13px; cursor: pointer; border-radius: 6px; }
.op:hover { background: var(--sm-hover); }
.oplist .op:has(.mono) { grid-template-columns: 18px minmax(0, 1fr) 16px minmax(0, 1fr) auto; }
.opold { color: var(--sm-sub); text-decoration: line-through; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.opnew { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mono { font-family: ui-monospace, Menlo, monospace; font-size: 12px; }
.skipped { margin-top: 12px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.chg-list { display: flex; flex-direction: column; gap: 8px; }
.chg-row { display: grid; grid-template-columns: minmax(200px, 1.3fr) minmax(160px, 1fr) 130px 32px; gap: 8px; align-items: center; }
.chg-val input, .chg-val select, .chg-ch select { width: 100%; }
.segment { display: inline-flex; border: 1px solid var(--sm-div); border-radius: 8px; overflow: hidden; }
.segment button { height: 34px; padding: 0 16px; border: 0; background: transparent; color: var(--sm-sub); }
.segment button + button { border-left: 1px solid var(--sm-div); }
.segment button.active { background: var(--sm-sel); color: var(--sm-text); font-weight: 600; }
.bitem { display: grid; grid-template-columns: 60px minmax(140px, 1fr) minmax(0, 1fr) 14px minmax(0, 1fr) 120px; gap: 8px; align-items: center; font-size: 13px; padding: 2px 2px 2px 24px; }
.bitem > span:last-child { justify-self: end; }
.jobdev { display: flex; align-items: center; gap: 10px; padding: 5px 2px; font-size: 13px; border-bottom: 1px solid color-mix(in srgb, var(--sm-div) 50%, transparent); }
.fwdev { display: flex; align-items: center; gap: 10px; padding: 6px 2px; border-bottom: 1px solid color-mix(in srgb, var(--sm-div) 50%, transparent); font-size: 13px; cursor: pointer; }
.fwrisk { display: flex; align-items: flex-start; gap: 6px; font-size: 12.5px; padding: 2px 0 4px 32px; }
.fwopts { display: flex; flex-wrap: wrap; gap: 10px 22px; padding-top: 6px; border-top: 1px solid var(--sm-div); }
.hist { display: flex; flex-direction: column; gap: 4px; }
.spark { width: 100%; height: 70px; background: var(--sm-raised); border-radius: 6px; }
.spark .sl { fill: none; stroke: var(--sm-accent); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.spark .sb { stroke: color-mix(in srgb, var(--sm-accent) 35%, transparent); stroke-width: 1; vector-effect: non-scaling-stroke; }
.spark .gl { stroke: var(--sm-div); stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; }
.spark .gl82 { stroke: color-mix(in srgb, var(--sm-err) 50%, transparent); }
.spark .gl75 { stroke: color-mix(in srgb, var(--sm-warn) 50%, transparent); }
.spark .glt { fill: var(--sm-sub); font-size: 9px; }
.scenes { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 2px 12px; }
.presets { padding: 16px 24px 32px; display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; align-content: start; }
.presets > p { grid-column: 1 / -1; margin: 0; }
.steps { margin: 0; padding-left: 20px; }
.nbtns { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.nbtns .btn, .ncmp .mini { height: 28px; padding: 0 10px; font-size: 12.5px; }
.ncmp .acts { grid-column: 2 / -1; display: flex; gap: 6px; }
.mini { display: inline-flex; align-items: center; gap: 4px; border: 1px solid var(--sm-div); border-radius: 14px; background: transparent; }
.mini:hover { background: var(--sm-hover); }

@media (max-width: 1000px) {
  .brand span { display: none; }
  .bl { display: none; }
}
@media (max-width: 900px) {
  .drawer.open { position: absolute; right: 0; top: 0; bottom: 0; width: min(460px, 100%); z-index: 10; }
  .tab { padding: 0 8px; }
  th.c-name, td.c-name { position: static; }
}
`;
