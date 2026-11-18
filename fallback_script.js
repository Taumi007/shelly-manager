// Shelly-Manager-Fallback – vom Shelly Manager erzeugt, Änderungen hier werden überschrieben.
// Übernimmt die Tasten nur, wenn Home Assistant länger als CFG.timeout Sekunden keinen Herzschlag
// (KVS »shm_hb«) geschrieben hat. Im Normalbetrieb tut das Script nichts.
// Belegungen: d = Doppeltaster [links, rechts], s = Einzeltaster [taste], w = Schalter [eingang]
// Ziele: h = Hue (CLIP v2), y = anderer Shelly, l = eigener Ausgang
var CFG = __CFG__;
var HB = 0;
var FORCE = false;
// Ausgehender WebSocket zu HA: verbunden = HA läuft. Getrennt länger als CFG.grace s = HA weg
// (greift damit auch bei einem HA-Neustart). Nie verbunden gewesen → Herzschlag entscheidet.
var WSSEEN = false;
var WSDOWN = 0;
// Shellys Script-Engine kennt nur einen Teil von JavaScript (kein Array.shift/indexOf),
// deshalb eine einfache Warteschlange mit Lesezeiger
var queue = [];
var qh = 0;
var busy = false;   // ein Aufruf unterwegs
var dim = null;          // {timer, b, dir}
var sceneIdx = {};       // Belegung → Szenenindex für den nächsten »ein«-Befehl
// Klick-Geste für Szenen: 1× = Szene 1, 2× = Szene 2, 3× = Szene 3 – ausgeführt erst nach dem letzten Klick
var CLICKWIN = 450;      // ms ohne weiteren Klick = Geste beendet
var clicks = {};
var clickT = {};

function now() {
  return Shelly.getComponentStatus("sys").unixtime || 0;
}

// RPC-/HTTP-Aufrufe nacheinander abarbeiten (Shelly erlaubt nur wenige gleichzeitige Aufrufe).
// Bewusst flach gehalten: Der Script-Stack des Shellys ist sehr klein, deshalb läuft das Senden
// immer in einem eigenen Timer-Durchlauf statt verschachtelt im Ereignis.
var pumpT = null;
var LASTR = null;
// Zeitmessung in ms, abrufbar per Script.Eval »LASTT«: [Taste→Absenden, Absenden→Antwort]
var T0 = 0;
var LASTT = null;
function kick() {
  if (!busy && !pumpT && qh < queue.length) pumpT = Timer.set(10, false, pump);
}
function pump() {
  pumpT = null;
  if (busy || qh >= queue.length) return;
  busy = true;
  var c = queue[qh];
  qh++;
  if (qh >= queue.length) { queue = []; qh = 0; }
  var t1 = Shelly.getUptimeMs();
  Shelly.call(c[0], c[1], function (r, e, m) {
    LASTT = [t1 - T0, Shelly.getUptimeMs() - t1];
    // Diagnose, abrufbar per Script.Eval »LASTR«: Methode, Ziel, Fehler, HTTP-Status, Antwortanfang, Zeit
    LASTR = [c[0], c[1].url || c[1].id, e, m, r && r.code, r && r.body ? r.body.slice(0, 120) : null, now()];
    busy = false;
    kick();
  });
}

// Bewusst ohne Hilfsfunktionen und ohne JSON.stringify: Der Script-Stack des Shellys ist so klein,
// dass jeder zusätzliche Aufruf in der Kette Taste → Belegung → Befehl zum Überlauf führen kann.
function act(b, a) {
  for (var i = 0; i < b.g.length; i++) {
    var t = b.g[i];
    if (t.k === "h") {
      var body;
      var url = CFG.hue + t.p;
      if (a === "on" && b.sc.length) {
        var n = sceneIdx[b.n] || 0;
        url = CFG.hue + b.sc[n][0];
        body = '{"scene":"' + b.sc[n][1] + '"}';
        sceneIdx[b.n] = (n + 1) % b.sc.length;
      } else if (a === "on") body = '{"on":true}';
      else if (a === "off") body = '{"on":false}';
      else if (a === "toggle") { b.st = !b.st; body = b.st ? '{"on":true}' : '{"on":false}'; }
      else if (a === "up") body = '{"on":true,"bri_inc":50,"transitiontime":10}';
      else body = '{"bri_inc":-50,"transitiontime":10}';
      queue.push(["HTTP.Request", { method: "PUT", url: url, body: body, ssl_ca: "*", timeout: 4 }]);
    } else if (t.k === "y") {
      var u = "http://" + t.ip + "/rpc/Switch.";
      if (a === "toggle") queue.push(["HTTP.GET", { url: u + "Toggle?id=" + t.ch, timeout: 3 }]);
      else if (a === "on") queue.push(["HTTP.GET", { url: u + "Set?id=" + t.ch + "&on=true", timeout: 3 }]);
      else if (a === "off") queue.push(["HTTP.GET", { url: u + "Set?id=" + t.ch + "&on=false", timeout: 3 }]);
    } else if (t.k === "l") {
      if (a === "toggle") queue.push(["Switch.Toggle", { id: t.ch }]);
      else if (a === "on" || a === "off") queue.push(["Switch.Set", { id: t.ch, on: a === "on" }]);
    }
  }
  kick();
}

function startDim(b, dir) {
  stopDim();
  act(b, dir);
  // Beim Halten nur nachlegen, wenn nichts mehr in der Warteschlange steht – sonst laufen Befehle nach
  dim = { b: b, dir: dir, timer: Timer.set(400, true, function () {
    if (dim && !busy && qh >= queue.length) act(dim.b, dim.dir);
  }) };
}
function stopDim() {
  if (dim) { Timer.clear(dim.timer); dim = null; }
}

// Ein Tastenereignis auf die Belegungen abbilden
function sceneGesture(b) {
  clicks[b.n] = (clicks[b.n] || 0) + 1;
  if (clickT[b.n]) Timer.clear(clickT[b.n]);
  clickT[b.n] = Timer.set(CLICKWIN, false, function () {
    var n = clicks[b.n];
    clicks[b.n] = 0;
    clickT[b.n] = null;
    if (n > b.sc.length) n = b.sc.length;
    sceneIdx[b.n] = n - 1;
    act(b, "on");
  });
}

function handle(idx, ev) {
  if (ev === "btn_up") { stopDim(); return; }
  for (var i = 0; i < CFG.b.length; i++) {
    var b = CFG.b[i];
    var pos = b.i[0] === idx ? 0 : (b.i.length > 1 && b.i[1] === idx ? 1 : -1);
    if (pos < 0) continue;
    if (b.t === "d") {
      if (pos === 0 && ev === "single_push") {
        // Mehrere Szenen: auf das Ende der Klick-Geste warten; sonst sofort einschalten
        if (b.sc.length > 1) sceneGesture(b);
        else { sceneIdx[b.n] = 0; act(b, "on"); }
      } else if (pos === 1 && ev === "single_push") act(b, "off");
      else if (ev === "long_push") startDim(b, pos === 0 ? "up" : "down");
    } else if (b.t === "s") {
      if (ev === "single_push") act(b, "toggle");
      else if (ev === "long_push" && b.dim) { b.dd = b.dd === "up" ? "down" : "up"; startDim(b, b.dd); }
    }
  }
}

function haDown() {
  if (FORCE) return true;
  var w = CFG.ws ? Shelly.getComponentStatus("ws") : null;
  if (w && w.connected) { WSSEEN = true; WSDOWN = 0; return false; }
  if (w && WSSEEN) {
    if (!WSDOWN) WSDOWN = now() - CFG.grace;   // Trennung nicht mitbekommen → als länger her werten
    return now() - WSDOWN >= CFG.grace;
  }
  return !HB || now() - HB > CFG.timeout;
}

// Herzschlag und »dauerhaft aktiv« im Speicher halten (alle 20 s aus dem KVS) – so muss ein
// Tastendruck nicht erst auf eine Abfrage warten. Der Manager setzt FORCE zusätzlich sofort per Eval.
function refresh() {
  Shelly.call("KVS.GetMany", { match: "shm_*" }, function (r) {
    if (!r || !r.items) return;
    var f = false;
    for (var k in r.items) {
      var it = r.items[k];
      var key = it.key || k;
      if (key === "shm_hb") HB = Number(it.value) || 0;
      if (key === "shm_force") f = it.value === "1";
    }
    FORCE = f;
  });
}

// Taster: sofort beim Loslassen reagieren statt auf »single_push« zu warten (das kommt erst nach dem
// Doppelklick-Fenster). DOWN: 1 = gedrückt, 2 = gehalten (long_push) – dann kein Kurzdruck beim Loslassen.
var DOWN = {};
var SHORT = {};
Shelly.addEventHandler(function (e) {
  if (!e || e.name !== "input" || !e.info || !e.info.event) return;
  var idx = e.id;
  var ev = e.info.event;
  if (ev === "btn_up" || ev === "long_push") T0 = Shelly.getUptimeMs();
  if (ev === "btn_down") { DOWN[idx] = 1; return; }
  if (ev === "long_push") { DOWN[idx] = 2; if (haDown()) handle(idx, "long_push"); return; }
  if (ev === "btn_up") {
    stopDim();
    var short = DOWN[idx] === 1;
    DOWN[idx] = 0;
    if (short) { SHORT[idx] = now(); if (haDown()) handle(idx, "single_push"); }
    return;
  }
  // Nur falls btn_up ausbleibt: der klassische Einzeldruck, aber nicht doppelt
  if (ev === "single_push" && now() - (SHORT[idx] || 0) > 2 && haDown()) handle(idx, "single_push");
});

// Schalter-Eingänge (kein Taster): jede Zustandsänderung schaltet um
Shelly.addStatusHandler(function (s) {
  if (s && s.name === "ws" && s.delta && typeof s.delta.connected === "boolean") {
    if (s.delta.connected) { WSSEEN = true; WSDOWN = 0; } else WSDOWN = now();
    return;
  }
  if (!s || s.name !== "input" || !s.delta || typeof s.delta.state !== "boolean") return;
  var idx = s.id;
  if (!haDown()) return;
  for (var i = 0; i < CFG.b.length; i++) if (CFG.b[i].t === "w" && CFG.b[i].i[0] === idx) act(CFG.b[i], "toggle");
});

// Für Tests aus dem Shelly Manager (Script.Eval): Ereignis ohne HA-Prüfung auslösen.
// Läuft im eigenen Timer-Durchlauf, damit der Eval-Aufruf den kleinen Stack nicht zusätzlich belastet.
function test(idx, ev) {
  Timer.set(10, false, function () {
    handle(idx, ev);
    if (ev === "long_push") Timer.set(1400, false, stopDim);
  });
  return "ok";
}

// Startzustand des WebSockets übernehmen
if (CFG.ws) {
  var w0 = Shelly.getComponentStatus("ws");
  if (w0 && w0.connected) WSSEEN = true;
}

// Gespeicherten Zustand laden und regelmäßig auffrischen
refresh();
Timer.set(20000, true, refresh);
