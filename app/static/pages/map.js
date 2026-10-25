/* Precedenz · the map (docs/decide-once-plan.md, section 6).

   A live circular dendrogram in a Neural Bioluminescence style. The people who
   decide glow at the center; workstreams sit on ring 1, precedents and the amber
   "waiting for a person" node on ring 2, cases are leaves on the rim, and an
   outer arc per sector mirrors the stage the Mubit console reports.

   The rule: every pulse is a real decision. Comets, sparks and shockwaves are
   only started by an event from /map/events (or by ?simulate=1, which wears a
   SIMULATED badge and is never used in the video). Ambient motion is breathing
   light and drifting plankton, nothing else.

   Structure is SVG, laid out here as a radial cluster in a fixed world (the
   Mubit arc has radius 450) that the camera scales to fit. Light that moves is
   one canvas composited "lighter": comets follow the SVG paths through
   getPointAtLength. One blur filter per layer group, none per leaf.

   Query string:
     ?fixture=1    load map.fixture.json instead of /map/data; no live stream
     ?present=1    recording: only counters, legend and ticker stay
     ?simulate=1   development only: scripted events under a SIMULATED badge
     ?stress=400   development only: pad the fixture to N leaves (badged too)
     ?fps=1        development only: a frame-rate meter

   Recording API, window.precedenzMap (each call returns a promise):
     focus(workstreamId, {zoom, duration})   camera to a sector (Motion, ~1.2 s)
     reset()                                 camera back to the whole map
     openReceipt(decisionId)                 drawer with that decision's receipt
     openWaiting(workstreamId)               drawer with the cases waiting there
     closeDrawer()
     ready                                   resolves when the bloom is at rest */
(function () {
  "use strict";

  /* ============================================================== setup */
  var M = window.Motion && window.Motion.animate ? window.Motion : null;
  var qs = new URLSearchParams(location.search);
  var SIMULATE = qs.has("simulate");
  var STRESS = Math.min(2000, parseInt(qs.get("stress") || "0", 10) || 0);
  var FIXTURE = qs.has("fixture") || SIMULATE || STRESS > 0;
  var PRESENT = qs.has("present");
  var SHOW_FPS = qs.has("fps");
  /* ?debug=1 (development only): mirror errors into the DOM, where a headless
     browser's --dump-dom can see them. */
  var DEBUG = qs.has("debug");
  function debugLog(msg) {
    var pre = document.getElementById("map-debug") || document.body.appendChild(Object.assign(document.createElement("pre"), { id: "map-debug", hidden: true }));
    pre.textContent += msg + "\n";
  }
  if (DEBUG) {
    var origError = console.error.bind(console);
    console.error = function () { debugLog(Array.prototype.map.call(arguments, String).join(" ")); origError.apply(null, arguments); };
    window.addEventListener("error", function (e) { debugLog("uncaught: " + e.message + " @" + e.lineno); });
    window.addEventListener("unhandledrejection", function (e) { debugLog("rejection: " + (e.reason && e.reason.stack || e.reason)); });
  }
  var mqReduced = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var reduced = !!(mqReduced && mqReduced.matches);
  var CONFIG = readConfig();
  var SVGNS = "http://www.w3.org/2000/svg";
  var TAU = Math.PI * 2;
  var START = -Math.PI / 2;
  /* World radii. Fixed: the camera, not the layout, adapts to the screen. */
  var RING = { soma: 34, r1: 160, r2: 282, r3: 376, inlet: 410, arc: 450, label: 474 };
  var STAGES = ["Collecting examples", "Training", "Tested", "Silent test", "Partial rollout", "Live"];
  var AFTERGLOW_MS = 20000;

  var rootStyle = getComputedStyle(document.documentElement);
  function tok(name, fallback) { return (rootStyle.getPropertyValue(name) || "").trim() || fallback; }
  var COL = {
    ask: tok("--ask", "#ffb547"), decide: tok("--decide", "#f4fbff"), overrule: tok("--overrule", "#ff5a6e"),
    dormant: tok("--dormant", "#2a3550"), mubit: tok("--mubit", "#7c7bff"), plankton: tok("--plankton", "#7fdcff"),
    slate: tok("--slate", "#5d6b86"), ink: tok("--ink", "#d6ecff"), inkDim: tok("--ink-dim", "#8aa4bf"),
  };
  var HUES = ["#42e8ff", "#4dffc0", "#a98bff", "#ff6bd6", "#c4ff6b"].map(function (fb, i) { return tok("--hue-" + (i + 1), fb); });
  var MUBIT_LIT = "#a9a8ff";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var svgRoot = $("#map-svg"), world = $("#world"), defs = $("#map-defs");
  var G = {
    arcs: $("#g-arcs"), links: $("#g-links"), hairs: $("#g-hairs"), leaves: $("#g-leaves"),
    soma: $("#g-soma"), nodes: $("#g-nodes"), labels: $("#g-labels"), routes: $("#g-routes"),
  };
  G.wash = svgEl("g", { class: "washes", "aria-hidden": "true" });
  world.insertBefore(G.wash, world.firstChild);
  var pkCanvas = $("#map-plankton"), fxCanvas = $("#map-fx");
  var pkCtx = pkCanvas.getContext("2d"), fxCtx = fxCanvas.getContext("2d");
  var drawerEl = $("#drawer"), drawerBody = $("#drawer-body"), tickerEl = $("#ticker");
  var routePath = svgEl("path", {}, G.routes);   // scratch path for getPointAtLength

  var S = {
    model: null, layout: null, items: new Map(), hue: new Map(), born: new Map(), glowing: new Set(),
    fx: [], shimmers: new Map(), colorTweens: [], queue: [], playing: false, lastSeq: 0,
    cam: { x: 0, y: 0, k: 1, ox: 0 }, camT: { tx: 0, ty: 0, k: 1 }, kb: 1, ls: 1,
    view: { w: 1, h: 1, dpr: 1, cx: 0, cy: 0, narrow: false },
    dirty: false, fxPainted: false, raf: 0, lastT: 0, drawer: null, needRefetch: false,
    fixtureSnap: null, simSeq: 100000, simTimers: [],
  };
  var readyResolve;
  var ready = new Promise(function (res) { readyResolve = res; });

  /* ============================================================ helpers */
  function readConfig() {
    /* Runs before `$` is defined, so it queries the document directly. */
    try { return JSON.parse((document.querySelector("#map-config") || {}).textContent || "{}") || {}; } catch (e) { return {}; }
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function polar(a, r) { return [Math.cos(a) * r, Math.sin(a) * r]; }
  function f1(n) { return Math.round(n * 10) / 10; }
  function easeOut(t) { return 1 - Math.pow(1 - t, 3); }
  function easeInOut(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function easeGrow(t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }
  function hexRgb(h) {
    h = String(h).replace("#", "");
    if (h.length === 3) h = h.split("").map(function (c) { return c + c; }).join("");
    var n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function rgbStr(c) { return "rgb(" + (c[0] | 0) + "," + (c[1] | 0) + "," + (c[2] | 0) + ")"; }
  function mixRgb(a, b, t) { return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)]; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function svgEl(tag, attrs, parent) {
    var el = document.createElementNS(SVGNS, tag);
    if (attrs) for (var k in attrs) el.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(el);
    return el;
  }
  function later(ms, fn) { return setTimeout(fn, ms); }
  function now() { return performance.now(); }
  function cssId(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, "_"); }
  function leafKey(wsId, ref) { return "lf:" + wsId + ":" + ref; }
  function byAt(a, b) { return String(a.at || "").localeCompare(String(b.at || "")); }
  function timeOf(iso, withSeconds) {
    var d = iso ? new Date(iso) : new Date();
    if (isNaN(d.getTime())) return withSeconds ? "--:--:--" : "--:--";
    var o = { hour: "2-digit", minute: "2-digit", hour12: false };
    if (withSeconds) o.second = "2-digit";
    return d.toLocaleTimeString([], o);
  }
  function fmtInt(n) { return Math.round(n).toLocaleString("en-US"); }
  function fmtNum(n) { var x = +n; return isFinite(x) ? (Math.round(x * 100) / 100).toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(n); }
  function cap(s) { s = String(s || ""); return s.charAt(0).toUpperCase() + s.slice(1); }
  function personLabel(id) {
    var people = (S.model && S.model.people) || [];
    for (var i = 0; i < people.length; i++) if (people[i].id === id) return people[i].label;
    if (!id) return "a person";
    var local = String(id).split("@")[0].split(/[._-]/)[0];
    return cap(local);
  }
  function personShort(id) { return String(personLabel(id)).split(" ")[0]; }
  /* Ask for a frame. If the browser stops delivering frames (a throttled tab,
     a headless capture), a short timeout runs the loop anyway. */
  function kick() {
    if (document.hidden) return;
    if (!S.raf) S.raf = requestAnimationFrame(loop);
    if (!S.fallback) {
      S.fallback = setTimeout(function () {
        S.fallback = 0;
        if (S.raf) { cancelAnimationFrame(S.raf); S.raf = 0; loop(); }
      }, 120);
    }
  }

  /* ============================================================== data */
  function normalize(snap) {
    snap = snap || {};
    return {
      generatedAt: snap.generated_at || null,
      lastSeq: +snap.last_seq || 0,
      people: Array.isArray(snap.people) ? snap.people : [],
      templates: Array.isArray(snap.templates) ? snap.templates : [],
      totals: Object.assign({ answered_once: 0, repeated: 0, asked: 0, model_calls: 0 }, snap.totals || {}),
      receipts: snap.receipts || {},
      ws: (snap.workstreams || []).map(normWs),
    };
  }
  function normWs(w) {
    return {
      id: String(w.id), label: w.label || String(w.id), kind: w.kind || "planted", audience: w.audience || "team",
      reader: w.reader !== false, task_key: w.task_key || null, task_key_changed: !!w.task_key_changed,
      mubit: w.mubit && w.mubit.stage ? w.mubit : null,
      repeatable_answer: w.repeatable_answer || null,
      other_answers: Array.isArray(w.other_answers) ? w.other_answers : [],
      fields: Array.isArray(w.fields) ? w.fields : [],
      precedents: (w.precedents || []).map(function (p) { return Object.assign({}, p, { cases: (p.cases || []).slice() }); }),
      waiting: (w.waiting || []).slice(),
      answered: (w.answered || []).slice(),
      counts: Object.assign({ follow: 0, ask: 0, answered: 0, model_calls: 0 }, w.counts || {}),
    };
  }
  /* Hues follow planting order and stick to a workstream for the page's life. */
  function hueOf(wsId) {
    if (!S.hue.has(wsId)) S.hue.set(wsId, HUES[S.hue.size % HUES.length]);
    return S.hue.get(wsId);
  }
  function wsById(id) {
    var list = (S.model && S.model.ws) || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }
  function precById(ws, id) {
    if (!ws) return null;
    for (var i = 0; i < ws.precedents.length; i++) if (ws.precedents[i].id === id) return ws.precedents[i];
    return null;
  }
  function activePrec(ws) {
    if (!ws) return null;
    var act = ws.precedents.filter(function (p) { return p.status === "active"; });
    return act.length ? act[act.length - 1] : null;
  }
  function findPrecAnywhere(id) {
    var list = S.model.ws;
    for (var i = 0; i < list.length; i++) { var p = precById(list[i], id); if (p) return { ws: list[i], p: p }; }
    return null;
  }
  /* Where a decision lives in the snapshot: under a precedent, waiting, or answered. */
  function findDecision(decisionId, caseRef, wsId) {
    var list = S.model.ws;
    for (var i = 0; i < list.length; i++) {
      var ws = list[i];
      if (wsId && ws.id !== wsId) continue;
      for (var j = 0; j < ws.waiting.length; j++) {
        var w = ws.waiting[j];
        if ((decisionId && w.decision_id === decisionId) || (caseRef && w.case_ref === caseRef)) return { ws: ws, kind: "waiting", item: w };
      }
      for (var a = 0; a < ws.answered.length; a++) {
        var an = ws.answered[a];
        if ((decisionId && an.decision_id === decisionId) || (caseRef && an.case_ref === caseRef)) return { ws: ws, kind: "answered", item: an };
      }
      for (var k = 0; k < ws.precedents.length; k++) {
        var p = ws.precedents[k];
        for (var c = 0; c < p.cases.length; c++) {
          var cs = p.cases[c];
          if ((decisionId && cs.decision_id === decisionId) || (caseRef && cs.case_ref === caseRef)) return { ws: ws, kind: "case", item: cs, p: p };
        }
        if (caseRef && p.origin_case === caseRef) return { ws: ws, kind: "origin", item: { case_ref: caseRef, decision_id: null, at: p.decided_at }, p: p };
      }
    }
    return null;
  }

  /* ============================================================ layout */
  /* A precedent's leaves: its origin case (answered by a person), then the cases
     it decided, oldest first. Waiting and answered-but-not-remembered cases hang
     off the amber node. Retired precedents keep their leaves, dimmed. */
  function childrenOf(ws) {
    var kids = [], seen = new Set();
    var precs = ws.precedents.slice().sort(function (a, b) { return (a.version || 0) - (b.version || 0); });
    precs.forEach(function (p) {
      var key = "p:" + p.id, dormant = p.status !== "active", leaves = [];
      if (p.origin_case && !seen.has(p.origin_case)) {
        seen.add(p.origin_case);
        var oc = p.cases.filter(function (c) { return c.case_ref === p.origin_case; })[0];
        leaves.push({ key: leafKey(ws.id, p.origin_case), ref: p.origin_case, decision_id: (oc && oc.decision_id) || p.origin_decision_id || null,
          state: dormant ? "dormant" : "answered", at: p.decided_at, parent: key, origin: true });
      }
      p.cases.slice().sort(byAt).forEach(function (c) {
        if (seen.has(c.case_ref)) return;
        seen.add(c.case_ref);
        leaves.push({ key: leafKey(ws.id, c.case_ref), ref: c.case_ref, decision_id: c.decision_id, via: c.via,
          state: dormant ? "dormant" : (c.verdict === "answered" ? "answered" : "follow"), at: c.at, parent: key });
      });
      kids.push({ key: key, kind: "prec", p: p, leaves: leaves });
    });
    var wkey = "w:" + ws.id, wl = [];
    ws.answered.slice().sort(byAt).forEach(function (c) {
      if (seen.has(c.case_ref)) return;
      seen.add(c.case_ref);
      wl.push({ key: leafKey(ws.id, c.case_ref), ref: c.case_ref, decision_id: c.decision_id, state: "answered", at: c.at, parent: wkey });
    });
    ws.waiting.slice().sort(byAt).forEach(function (c) {
      if (seen.has(c.case_ref)) return;
      seen.add(c.case_ref);
      wl.push({ key: leafKey(ws.id, c.case_ref), ref: c.case_ref, decision_id: c.decision_id, state: "ask", at: c.at, parent: wkey, waiting: c });
    });
    if (wl.length) kids.push({ key: wkey, kind: "wait", leaves: wl, count: ws.waiting.length });
    return kids;
  }

  function computeLayout(model) {
    var n = model.ws.length, gap = n > 1 ? 0.12 : 0;
    var info = model.ws.map(function (ws) {
      var kids = childrenOf(ws);
      var cw = kids.map(function (k) { return 1.3 + Math.pow(k.leaves.length, 0.8); });
      return { ws: ws, kids: kids, cw: cw, w: 2.6 + cw.reduce(function (s, x) { return s + x; }, 0) };
    });
    var total = info.reduce(function (s, x) { return s + x.w; }, 0) || 1;
    var avail = TAU - gap * n;
    var L = { sectors: new Map(), pos: new Map(), kids: new Map(), leaves: new Map() };
    var a = START + gap / 2;
    info.forEach(function (it) {
      var span = avail * it.w / total, a0 = a, a1 = a + span, mid = a + span / 2;
      L.sectors.set(it.ws.id, { a0: a0, a1: a1 });
      L.pos.set("ws:" + it.ws.id, { a: mid, r: RING.r1 });
      var pad = Math.min(span * 0.08, 0.06), i0 = a0 + pad, i1 = a1 - pad;
      var cwT = it.cw.reduce(function (s, x) { return s + x; }, 0) || 1, c = i0;
      it.kids.forEach(function (k, j) {
        var cs = (i1 - i0) * it.cw[j] / cwT, cm = c + cs / 2;
        L.pos.set(k.key, { a: cm, r: RING.r2 });
        L.kids.set(k.key, Object.assign({ wsId: it.ws.id }, k));
        placeLeaves(L, k.leaves, cs, cm, it.ws.id);
        c += cs;
      });
      a += span + gap;
    });
    return L;
  }
  /* Leaves spread evenly under their parent; a crowded parent stacks rows, hex packed. */
  function placeLeaves(L, leaves, cs, cm, wsId) {
    var n = leaves.length;
    if (!n) return;
    var r = RING.r3, usable = cs * 0.9, minStep = 6.6 / r, maxStep = 21 / r, rows = 1;
    while (rows < 5 && usable / Math.ceil(n / rows) < minStep) rows++;
    var cols = Math.ceil(n / rows), step = Math.min(maxStep, usable / Math.max(1, cols));
    var start = cm - step * (cols - 1) / 2;
    leaves.forEach(function (lf, i) {
      var col = Math.floor(i / rows), row = i % rows;
      L.pos.set(lf.key, { a: start + col * step + (row % 2 ? step / 2 : 0), r: r + row * 8.5 });
      L.leaves.set(lf.key, Object.assign({ wsId: wsId }, lf));
    });
  }

  /* ============================================================= paths */
  function pt(p) { return f1(p[0]) + "," + f1(p[1]); }
  function radialLinkD(p0, p1) {
    var rm = (p0.r + p1.r) / 2;
    return "M" + pt(polar(p0.a, p0.r)) + "C" + pt(polar(p0.a, rm)) + " " + pt(polar(p1.a, rm)) + " " + pt(polar(p1.a, p1.r));
  }
  /* Soma to workstream: a dendrite with a gentle twist, so the arms read as grown, not drawn. */
  function somaLinkD(a, r) {
    return "M" + pt(polar(a - 0.5, RING.soma * 0.9)) + "C" + pt(polar(a - 0.42, r * 0.46)) + " " +
      pt(polar(a - 0.05, r * 0.6)) + " " + pt(polar(a, r));
  }
  function arcD(a0, a1, r) {
    if (a1 - a0 < 0.002) a1 = a0 + 0.002;
    var large = a1 - a0 > Math.PI ? 1 : 0;
    return "M" + pt(polar(a0, r)) + "A" + r + "," + r + " 0 " + large + " 1 " + pt(polar(a1, r));
  }
  /* Rim to workstream: the way an incoming case dives in. */
  function diveD(a, wsCur) {
    return "M" + pt(polar(a, RING.inlet)) + "C" + pt(polar(a + 0.05, RING.inlet * 0.8)) + " " +
      pt(polar(wsCur.a + 0.06, RING.r1 * 1.35)) + " " + pt(polar(wsCur.a, wsCur.r));
  }
  /* Along ring 2, bulging outward: how a waiting case crosses to its new precedent. */
  function hopD(p0, p1) {
    var am = (p0.a + p1.a) / 2, rb = RING.r2 + 34 + Math.abs(p1.a - p0.a) * 40;
    return "M" + pt(polar(p0.a, p0.r)) + "Q" + pt(polar(am, rb)) + " " + pt(polar(p1.a, p1.r));
  }
  /* Sample SVG geometry into a world-space polyline, one point every ~5 units.
     Each segment is a path `d` (or an element, whose d is copied, so a
     pathLength attribute never skews the distances), optionally reversed. */
  function route(segments) {
    var out = [];
    segments.forEach(function (seg) {
      var d = typeof seg === "string" ? seg : seg.el ? seg.el.getAttribute("d") : seg.d;
      if (!d) return;
      routePath.setAttribute("d", d);
      var len = routePath.getTotalLength(), n = Math.max(2, Math.ceil(len / 5));
      for (var i = out.length ? 1 : 0; i <= n; i++) {
        var p = routePath.getPointAtLength(seg.reverse ? len * (1 - i / n) : len * i / n);
        out.push(p.x, p.y);
      }
    });
    return out;
  }

  /* ============================================================= scene */
  var haloIds = new Map();
  function haloFill(hex) {
    if (haloIds.has(hex)) return haloIds.get(hex);
    var id = "halo-" + haloIds.size;
    var g = svgEl("radialGradient", { id: id }, defs);
    svgEl("stop", { offset: "0", "stop-color": hex, "stop-opacity": ".55" }, g);
    svgEl("stop", { offset: ".3", "stop-color": hex, "stop-opacity": ".18" }, g);
    svgEl("stop", { offset: "1", "stop-color": hex, "stop-opacity": "0" }, g);
    haloIds.set(hex, "url(#" + id + ")");
    return haloIds.get(hex);
  }
  function breathe(el, min, max) {
    el.classList.add("breathe");
    var dur = (min || 3) + Math.random() * ((max || 6) - (min || 3));
    el.style.setProperty("--bdur", dur.toFixed(2) + "s");
    el.style.setProperty("--bdelay", (-Math.random() * dur).toFixed(2) + "s");
  }
  function ensureSoma() {
    if (S.soma) return S.soma;
    /* Faint guides for ring 1 and ring 2: static structure, never motion. */
    svgEl("circle", { class: "ring-guide", r: String(RING.r1) }, G.soma);
    svgEl("circle", { class: "ring-guide", r: String(RING.r2) }, G.soma);
    var g = svgEl("g", { class: "soma" }, G.soma);
    var inner = svgEl("g", { class: "soma-inner" }, g);
    var halo = svgEl("circle", { class: "soma-halo", r: "96" }, inner);
    breathe(halo, 5, 6.5);
    svgEl("circle", { class: "soma-body", r: String(RING.soma) }, inner);
    svgEl("circle", { class: "soma-membrane", r: String(RING.soma + 5) }, inner);
    svgEl("circle", { class: "soma-core", r: "10" }, inner);
    var label = svgEl("text", { class: "lbl lbl-soma", y: String(RING.soma + 27) }, g);
    var sub = svgEl("text", { class: "lbl lbl-soma-sub", y: String(RING.soma + 42) }, g);
    S.soma = { g: g, inner: inner, label: label, sub: sub };
    return S.soma;
  }
  function renderSomaText() {
    var s = ensureSoma(), people = S.model.people;
    s.label.textContent = people.length ? people.map(function (p) { return p.label; }).join(" · ") : "People who decide";
    s.sub.textContent = people.length > 1 ? "decide" : people.length ? "decides" : "";
  }
  function makeWs(ws) {
    var hex = hueOf(ws.id), id = "lg-" + cssId(ws.id);
    var it = { key: "ws:" + ws.id, type: "ws", id: ws.id, hex: hex };
    it.grad = svgEl("linearGradient", { id: id, gradientUnits: "userSpaceOnUse" }, defs);
    svgEl("stop", { offset: "0", "stop-color": COL.decide, "stop-opacity": ".9" }, it.grad);
    svgEl("stop", { offset: ".5", "stop-color": hex, "stop-opacity": ".95" }, it.grad);
    svgEl("stop", { offset: "1", "stop-color": hex }, it.grad);
    it.link = svgEl("path", { class: "link soma-link", pathLength: "1", stroke: "url(#" + id + ")" }, G.links);
    it.g = svgEl("g", { class: "node node-ws", tabindex: "0", role: "button", "data-key": it.key }, G.nodes);
    it.inner = svgEl("g", { class: "pop" }, it.g);
    svgEl("circle", { class: "hit", r: "24" }, it.inner);
    it.halo = svgEl("circle", { class: "halo", r: "46", fill: haloFill(hex) }, it.inner);
    breathe(it.halo);
    it.ring = svgEl("circle", { class: "ring", r: "15.5", stroke: hex }, it.inner);
    it.core = svgEl("circle", { class: "core", r: "9.5", fill: hex, stroke: "#effdff" }, it.inner);
    svgEl("circle", { class: "focus-ring", r: "20" }, it.inner);
    /* A faint wash of the sector's hue behind its branch: breathing light only. */
    it.wash = svgEl("circle", { class: "sector-wash", r: "250", fill: haloFill(hex) }, G.wash);
    breathe(it.wash, 5, 7);
    it.arcG = svgEl("g", { class: "arc" }, G.arcs);
    it.arcTrack = svgEl("path", { class: "arc-track", pathLength: "1" }, it.arcG);
    it.arcSegs = STAGES.map(function () { return svgEl("path", { class: "arc-seg", pathLength: "1" }, it.arcG); });
    it.label = svgEl("g", { class: "ws-label" }, G.labels);
    it.l1 = svgEl("text", { class: "lbl lbl-ws" }, it.label);
    it.l2 = svgEl("text", { class: "lbl lbl-mubit" }, it.label);
    it.l3 = svgEl("text", { class: "lbl lbl-ws-sub" }, it.label);
    return it;
  }
  function makeKid(key, k) {
    var wait = k.kind === "wait";
    var it = { key: key, type: wait ? "wait" : "prec", wsId: k.wsId };
    it.link = svgEl("path", { class: "link " + (wait ? "wait-link" : "prec-link"), pathLength: "1" }, G.links);
    it.g = svgEl("g", { class: "node " + (wait ? "node-wait" : "node-prec"), tabindex: "0", role: "button", "data-key": key }, G.nodes);
    it.inner = svgEl("g", { class: "pop" }, it.g);
    svgEl("circle", { class: "hit", r: "18" }, it.inner);
    it.halo = svgEl("circle", { class: "halo", r: wait ? "30" : "27" }, it.inner);
    breathe(it.halo);
    it.ring = svgEl("circle", { class: "ring", r: "11.5" }, it.inner);
    it.core = svgEl("circle", { class: "core", r: "8.4" }, it.inner);
    it.text = svgEl("text", { class: "node-text", "text-anchor": "middle", dy: "3.3" }, it.inner);
    it.focus = svgEl("circle", { class: "focus-ring", r: "16" }, it.inner);
    return it;
  }
  function makeLeaf(key) {
    return {
      key: key, type: "leaf",
      hair: svgEl("path", { class: "hair" }, G.hairs),
      el: svgEl("circle", { class: "leaf", r: "2.6", "data-leaf": key }, G.leaves),
    };
  }
  var LEAF_R = { follow: 3.1, ask: 3.8, answered: 3.5, dormant: 2.6 };
  function leafHex(state, wsId) {
    return state === "ask" ? COL.ask : state === "answered" ? COL.decide : state === "dormant" ? COL.slate : hueOf(wsId);
  }

  function setTarget(it, pos, sec) {
    it.tgt = { a: pos.a, r: pos.r };
    if (!it.cur) it.cur = { a: pos.a, r: pos.r };
    if (sec) {
      it.secTgt = { a0: sec.a0, a1: sec.a1 };
      if (!it.sec) it.sec = { a0: sec.a0, a1: sec.a1 };
    }
  }

  function updateWsContent(it, ws) {
    var narrow = S.view.narrow;
    var label = narrow ? String(ws.label).split(" · ")[0] : ws.label;
    it.l1.textContent = label;
    var l2 = "", l3 = "", none = false, si = -1;
    if (!ws.reader) { l2 = "Rules only · no model step"; none = true; }
    else if (!ws.mubit) { l2 = "Mubit · no console snapshot"; none = true; }
    else {
      si = stageIndex(ws.mubit.stage);
      l2 = "Mubit · " + ws.mubit.stage + (ws.mubit.detail ? " · " + ws.mubit.detail : "");
      l3 = (ws.mubit.source || "Mubit console") + " · as of " + timeOf(ws.mubit.as_of);
    }
    if (ws.audience === "individual") l3 = l3 ? "For one person · " + l3 : "For one person";
    it.l2.textContent = narrow ? "" : l2;
    it.l3.textContent = narrow ? "" : l3;
    it.l2.classList.toggle("is-none", none);
    it.lines = narrow ? 1 : l3 ? 3 : 2;
    it.arcG.style.display = ws.reader ? "" : "none";
    it.arcTrack.classList.toggle("is-unknown", !ws.mubit);
    it.arcSegs.forEach(function (s, i) {
      s.style.display = ws.mubit ? "" : "none";
      s.classList.toggle("is-lit", i === si);
      s.classList.toggle("is-done", si >= 0 && i < si);
    });
    it.stage = si;
    it.g.setAttribute("aria-label", ws.label + ": workstream, " + ws.precedents.filter(function (p) { return p.status === "active"; }).length +
      " active precedent, " + ws.waiting.length + " waiting. Open details.");
  }
  function stageIndex(stage) {
    var s = String(stage || "").toLowerCase();
    if (s.indexOf("collect") === 0) return 0;
    if (s === "training") return 1;
    if (["passed", "below target", "prompt only", "not tested"].indexOf(s) >= 0) return 2;
    if (s === "silent test") return 3;
    if (s === "partial rollout") return 4;
    if (s === "live") return 5;
    return -1;
  }
  function updateKidContent(it, k, opts) {
    if (it.type === "wait") {
      var count = k.count;
      it.text.textContent = String(count);
      var sc = count ? 1 + Math.min(0.9, Math.sqrt(count) * 0.16) : 0.7;
      it.core.setAttribute("r", (8 * sc).toFixed(1));
      it.ring.setAttribute("r", (11.5 * sc).toFixed(1));
      it.halo.setAttribute("r", (28 * sc).toFixed(1));
      it.focus.setAttribute("r", (15 * sc + 1).toFixed(1));
      setNodeColor(it, COL.ask, count ? 1 : 0.5);
      it.link.setAttribute("stroke", COL.ask);
      it.g.setAttribute("aria-label", count + " waiting for a person in " + (wsById(k.wsId) || {}).label + ". Open the list.");
      return;
    }
    var p = k.p, retired = p.status !== "active";
    it.text.textContent = "v" + (p.version == null ? "?" : p.version);
    it.retired = retired;
    if (!(opts && opts.keepColor && opts.keepColor.has(it.key))) {
      setNodeColor(it, retired ? COL.slate : (it.born ? COL.decide : hueOf(k.wsId)), retired ? 0.35 : 1);
      it.link.setAttribute("stroke", retired ? COL.slate : hueOf(k.wsId));
      it.link.classList.toggle("is-retired", retired);
    }
    it.halo.classList.toggle("breathe", !retired);
    it.g.setAttribute("aria-label", "Precedent v" + p.version + (retired ? ", retired" : ", active") + ", " + p.cases.length +
      " cases: " + (p.text || "") + " Open details.");
  }
  function setNodeColor(it, hex, alpha) {
    it.hex = hex;
    it.core.setAttribute("fill", hex);
    it.core.setAttribute("stroke", alpha < 0.6 ? "rgba(214,236,255,.25)" : "#f4fbff");
    it.core.setAttribute("fill-opacity", alpha < 0.6 ? ".55" : "1");
    it.ring.setAttribute("stroke", hex);
    it.halo.setAttribute("fill", haloFill(hex));
    it.halo.style.visibility = alpha < 0.6 ? "hidden" : "";
    if (it.text) it.text.setAttribute("fill", alpha < 0.6 ? COL.inkDim : "#03121c");
  }
  function setLeafState(it, lf, opts) {
    var hex = leafHex(lf.state, lf.wsId), prev = it.state;
    it.state = lf.state;
    it.el.setAttribute("r", String(LEAF_R[lf.state] || 2.6));
    it.hair.classList.toggle("is-dormant", lf.state === "dormant");
    if (prev && prev !== lf.state && opts && opts.colorMs && !reduced) {
      colorTween(it.hex || hex, hex, opts.colorMs, function (c) { it.el.setAttribute("fill", c); it.hair.setAttribute("stroke", c); });
    } else {
      it.el.setAttribute("fill", hex);
      it.hair.setAttribute("stroke", hex);
    }
    it.hex = hex;
  }
  function colorTween(fromHex, toHex, ms, apply, delay) {
    S.colorTweens.push({ a: hexRgb(fromHex), b: hexRgb(toHex), t0: now() + (delay || 0), ms: ms, apply: apply });
    kick();
  }

  /* Reconcile the scene with the model: create, retarget, remove; then tween. */
  function sync(opts) {
    opts = opts || {};
    var L = computeLayout(S.model);
    S.layout = L;
    var alive = new Set(), created = [];
    ensureSoma();
    S.model.ws.forEach(function (ws, i) {
      var key = "ws:" + ws.id, it = S.items.get(key);
      if (!it) { it = makeWs(ws); S.items.set(key, it); created.push(it); }
      alive.add(key);
      it.order = i;
      setTarget(it, L.pos.get(key), L.sectors.get(ws.id));
      updateWsContent(it, ws);
    });
    L.kids.forEach(function (k, key) {
      var it = S.items.get(key);
      if (!it) { it = makeKid(key, k); S.items.set(key, it); created.push(it); if (opts.born === key) it.born = true; }
      alive.add(key);
      it.wsId = k.wsId;
      setTarget(it, L.pos.get(key));
      updateKidContent(it, k, opts);
    });
    L.leaves.forEach(function (lf, key) {
      var it = S.items.get(key);
      if (!it) { it = makeLeaf(key); S.items.set(key, it); created.push(it); }
      alive.add(key);
      it.wsId = lf.wsId;
      it.parent = lf.parent;
      it.data = lf;
      setTarget(it, L.pos.get(key));
      setLeafState(it, lf, opts);
    });
    S.items.forEach(function (it, key) { if (!alive.has(key)) removeItem(it); });
    created.forEach(function (it) {
      it.cur = { a: it.tgt.a, r: it.tgt.r };
      if (it.secTgt) it.sec = { a0: (it.secTgt.a0 + it.secTgt.a1) / 2, a1: (it.secTgt.a0 + it.secTgt.a1) / 2 };
    });
    (opts.teleport || []).forEach(function (key) {
      var it = S.items.get(key);
      if (it) it.cur = { a: it.tgt.a, r: it.tgt.r };
    });
    orderNodes();
    if (!opts.quietNew) decorateNew(created, opts);
    startTween(opts);
    updateTable();
    return created;
  }
  /* Tab order follows the map: each workstream, then its precedents and waiting node. */
  function orderNodes() {
    var want = [];
    S.model.ws.forEach(function (ws) {
      var w = S.items.get("ws:" + ws.id);
      if (w) want.push(w.g);
      S.items.forEach(function (it) { if ((it.type === "prec" || it.type === "wait") && it.wsId === ws.id && it.g.parentNode) want.push(it.g); });
    });
    var current = Array.prototype.filter.call(G.nodes.children, function (el) { return want.indexOf(el) >= 0; });
    var same = current.length === want.length && current.every(function (el, i) { return el === want[i]; });
    if (!same) want.forEach(function (el) { G.nodes.appendChild(el); });
  }
  /* Entrances for anything that appears after the bloom. Choreographed events
     pass hold: [...] for the keys they reveal themselves. */
  function decorateNew(created, opts) {
    if (opts.bloom || reduced) return;
    var hold = new Set(opts.hold || []);
    created.forEach(function (it) {
      if (hold.has(it.key)) return;
      if (it.type === "leaf") { replay(it.el, "ignite"); it.hair.classList.add("fadein"); }
      else if (it.type === "ws") {
        replay(it.link, "grow"); it.inner.style.setProperty("--d", ".55s"); replay(it.inner, "popin");
        it.arcG.classList.add("fadein"); it.arcG.style.setProperty("--d", ".7s");
        it.label.classList.add("fadein"); it.label.style.setProperty("--d", ".8s");
      } else { replay(it.link, "grow"); it.inner.style.setProperty("--d", ".5s"); replay(it.inner, "popin"); }
    });
  }
  function replay(el, cls) {
    el.classList.remove(cls);
    void el.getBoundingClientRect();
    el.classList.add(cls);
    var done = function () { el.classList.remove(cls); el.removeEventListener("animationend", done); };
    el.addEventListener("animationend", done);
  }
  function removeItem(it) {
    S.items.delete(it.key);
    S.glowing.delete(it.key);
    var els = [it.g, it.link, it.hair, it.el, it.arcG, it.label, it.wash].filter(Boolean);
    if (reduced) { els.forEach(function (e) { e.remove(); }); return; }
    els.forEach(function (e) { e.style.transition = "opacity .5s ease"; e.style.opacity = "0"; });
    later(560, function () { els.forEach(function (e) { e.remove(); }); });
  }

  /* ============================================================ tween */
  /* Relayout tweens run on the map's own clock (the frame loop), so positions
     stay right even when frames are throttled. Eases come from Motion; the
     planting relayout is a damped spring (stiffness 70, damping 11, mass 1)
     that overshoots a little: sectors rotate apart, then settle. */
  var EASE_RELAYOUT = M && M.cubicBezier ? M.cubicBezier(0.22, 1, 0.36, 1) : easeOut;
  var EASE_CAMERA = M && M.cubicBezier ? M.cubicBezier(0.65, 0, 0.35, 1) : easeInOut;
  function springAt(sec) {
    var w0 = Math.sqrt(70), z = 11 / (2 * w0), wd = w0 * Math.sqrt(1 - z * z);
    return 1 - Math.exp(-z * w0 * sec) * (Math.cos(wd * sec) + (z * w0 / wd) * Math.sin(wd * sec));
  }
  function startTween(opts) {
    S.items.forEach(function (it) {
      it.from = { a: it.cur.a, r: it.cur.r };
      if (it.sec) it.secFrom = { a0: it.sec.a0, a1: it.sec.a1 };
    });
    S.tween = { p: 0, t0: now(), spring: !!opts.spring, ms: opts.spring ? 1700 : (opts.duration || 0.8) * 1000 };
    if (reduced || opts.instant) { S.tween.p = 1; stepTween(); S.tween = null; renderAll(); return; }
    S.dirty = true;
    kick();
  }
  function advanceTween(t) {
    var tw = S.tween, u = (t - tw.t0) / tw.ms;
    if (u >= 1) { tw.p = 1; stepTween(); S.tween = null; return; }
    tw.p = tw.spring ? springAt(u * tw.ms / 1000) : EASE_RELAYOUT(Math.max(0, u));
    stepTween();
  }
  function stepTween() {
    var p = S.tween ? S.tween.p : 1;
    S.items.forEach(function (it) {
      if (!it.from || !it.tgt) return;
      it.cur.a = lerp(it.from.a, it.tgt.a, p);
      it.cur.r = lerp(it.from.r, it.tgt.r, p);
      if (it.sec && it.secFrom) {
        it.sec.a0 = lerp(it.secFrom.a0, it.secTgt.a0, p);
        it.sec.a1 = lerp(it.secFrom.a1, it.secTgt.a1, p);
      }
    });
  }
  function renderAll() {
    S.items.forEach(function (it) { var p = polar(it.cur.a, it.cur.r); it.x = p[0]; it.y = p[1]; });
    S.items.forEach(renderItem);
  }
  function renderItem(it) {
    if (it.type === "leaf") {
      var par = S.items.get(it.parent);
      it.el.setAttribute("cx", f1(it.x));
      it.el.setAttribute("cy", f1(it.y));
      if (par) it.hair.setAttribute("d", radialLinkD(par.cur, it.cur));
      return;
    }
    it.g.setAttribute("transform", "translate(" + f1(it.x) + " " + f1(it.y) + ")");
    if (it.type === "ws") {
      var wc = polar((it.sec.a0 + it.sec.a1) / 2, RING.r2 + 10);
      it.wash.setAttribute("cx", f1(wc[0]));
      it.wash.setAttribute("cy", f1(wc[1]));
      it.link.setAttribute("d", somaLinkD(it.cur.a, it.cur.r));
      var s = polar(it.cur.a - 0.5, RING.soma);
      it.grad.setAttribute("x1", f1(s[0])); it.grad.setAttribute("y1", f1(s[1]));
      it.grad.setAttribute("x2", f1(it.x)); it.grad.setAttribute("y2", f1(it.y));
      var a0 = it.sec.a0 + 0.016, a1 = it.sec.a1 - 0.016;
      it.arcTrack.setAttribute("d", arcD(a0, Math.max(a0 + 0.002, a1), RING.arc));
      var gapA = 0.012, segA = (a1 - a0 - gapA * (STAGES.length - 1)) / STAGES.length;
      it.arcSegs.forEach(function (seg, i) {
        var s0 = a0 + i * (segA + gapA);
        seg.setAttribute("d", arcD(s0, s0 + Math.max(0.002, segA), RING.arc));
      });
      placeWsLabel(it);
      return;
    }
    var ws = S.items.get("ws:" + it.wsId);
    if (ws) it.link.setAttribute("d", radialLinkD(ws.cur, it.cur));
  }
  function placeWsLabel(it) {
    var a = (it.sec.a0 + it.sec.a1) / 2, p = polar(a, RING.label), c = Math.cos(a), s = Math.sin(a);
    var anchor = c > 0.3 ? "start" : c < -0.3 ? "end" : "middle";
    var ls = S.ls, lines = it.lines || 2, h1 = 15 * ls, h2 = 14 * ls;
    var block = h1 + (lines - 1) * h2;
    var y0 = s < -0.5 ? -block + h1 * 0.7 : s > 0.5 ? h1 * 0.9 : -block / 2 + h1 * 0.8;
    it.label.setAttribute("transform", "translate(" + f1(p[0]) + " " + f1(p[1]) + ")");
    [it.l1, it.l2, it.l3].forEach(function (t, i) {
      t.setAttribute("text-anchor", anchor);
      t.setAttribute("y", f1(y0 + (i === 0 ? 0 : h1 * 0.15 + i * h2)));
    });
  }

  /* ============================================================ canvas */
  var sprites = new Map();
  function sprite(hex, hot) {
    var key = hex + (hot ? "*" : "");
    var c = sprites.get(key);
    if (c) return c;
    c = document.createElement("canvas");
    c.width = c.height = 64;
    var g = c.getContext("2d"), rgb = hexRgb(hex), r = rgb[0], gg = rgb[1], b = rgb[2];
    var grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    var col = function (a) { return "rgba(" + r + "," + gg + "," + b + "," + a + ")"; };
    if (hot) {
      grd.addColorStop(0, "rgba(255,255,255,1)");
      grd.addColorStop(0.16, col(0.95));
      grd.addColorStop(0.42, col(0.28));
      grd.addColorStop(1, col(0));
    } else {
      grd.addColorStop(0, col(0.9));
      grd.addColorStop(0.24, col(0.4));
      grd.addColorStop(0.6, col(0.08));
      grd.addColorStop(1, col(0));
    }
    g.fillStyle = grd;
    g.fillRect(0, 0, 64, 64);
    sprites.set(key, c);
    return c;
  }
  function blit(ctx, spr, x, y, size, alpha) {
    if (alpha <= 0.004 || size <= 0) return;
    ctx.globalAlpha = Math.min(1, alpha);
    ctx.drawImage(spr, x - size / 2, y - size / 2, size, size);
  }
  function resize() {
    var stage = $("#stage"), w = stage.clientWidth || window.innerWidth, h = stage.clientHeight || window.innerHeight;
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var narrow = w < 760;
    S.view = { w: w, h: h, dpr: dpr, narrow: narrow, cx: w / 2, cy: narrow ? (96 + h - 232) / 2 : h / 2 };
    [pkCanvas, fxCanvas].forEach(function (c) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); });
    var fitR = narrow ? Math.max(80, Math.min(w / 2 - 46, (h - 340) / 2)) : Math.max(140, Math.min(h / 2 - 74, w / 2 - 330));
    S.kb = fitR / (RING.label + 6);
    S.ls = Math.max(1, (narrow ? 10.5 : 13) / (15 * S.kb));
    svgRoot.style.setProperty("--ls", S.ls.toFixed(3));
    svgRoot.classList.toggle("is-narrow", narrow);
    applyCam();
    S.pkDirty = true;
  }
  function applyCam() {
    var v = S.view, k = S.kb * S.cam.k;
    var tx = v.cx + S.cam.ox - S.cam.x * k, ty = v.cy - S.cam.y * k;
    world.setAttribute("transform", "translate(" + tx.toFixed(2) + " " + ty.toFixed(2) + ") scale(" + k.toFixed(4) + ")");
    S.camT = { tx: tx, ty: ty, k: k };
    S.fxPainted = true;
    S.pkDirty = true;
    kick();
  }
  function toWorld(ctx) {
    var t = S.camT, d = S.view.dpr;
    ctx.setTransform(d * t.k, 0, 0, d * t.k, d * t.tx, d * t.ty);
  }

  /* Plankton: ~120 motes drifting with depth parallax; the only ambient motion
     besides breathing. Paused with the tab (the frame loop stops when hidden). */
  function initPlankton() {
    S.plankton = [];
    for (var i = 0; i < 120; i++) {
      S.plankton.push({ x: Math.random(), y: Math.random(), z: 0.2 + Math.random() * 0.8,
        vx: (Math.random() - 0.5) * 0.006, vy: -0.002 - Math.random() * 0.005,
        ph: Math.random() * TAU, s: 0.5 + Math.random() });
    }
  }
  function drawPlankton(t, dt) {
    var ctx = pkCtx, v = S.view, d = v.dpr, spr = sprite(COL.plankton);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, pkCanvas.width, pkCanvas.height);
    ctx.globalCompositeOperation = "lighter";
    var camX = S.cam.x * S.kb * S.cam.k - S.cam.ox, camY = S.cam.y * S.kb * S.cam.k, zoom = S.cam.k;
    for (var i = 0; i < S.plankton.length; i++) {
      var p = S.plankton[i];
      if (!reduced) {
        p.x = (p.x + p.vx * p.z * dt / 1000 + 1) % 1;
        p.y = (p.y + p.vy * p.z * dt / 1000 + 1) % 1;
      }
      var px = ((p.x * v.w - camX * p.z * 0.3) % v.w + v.w) % v.w;
      var py = ((p.y * v.h - camY * p.z * 0.3) % v.h + v.h) % v.h;
      var tw = reduced ? 0.8 : 0.7 + 0.3 * Math.sin(t / 1000 * (0.4 + p.z * 0.6) + p.ph);
      var size = (2.2 + 7.5 * p.z * p.s) * (1 + (zoom - 1) * p.z * 0.35);
      blit(ctx, spr, px * d, py * d, size * d, (0.08 + 0.4 * p.z * p.z) * tw);
    }
    ctx.globalAlpha = 1;
  }

  function drawFx(t) {
    var active = S.fx.length || S.glowing.size || S.shimmers.size;
    if (!active && !S.fxPainted) return;
    var ctx = fxCtx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, fxCanvas.width, fxCanvas.height);
    S.fxPainted = !!active;
    if (!active) return;
    toWorld(ctx);
    ctx.globalCompositeOperation = "lighter";
    S.glowing.forEach(function (key) {
      var it = S.items.get(key), b = S.born.get(key);
      if (!it || b == null) { S.glowing.delete(key); return; }
      var age = t - b;
      if (age < 0) return;
      if (age > AFTERGLOW_MS) { S.glowing.delete(key); return; }
      var k = Math.pow(1 - age / AFTERGLOW_MS, 1.6);
      blit(ctx, sprite(it.hex || COL.decide), it.x, it.y, 12 + 30 * k, 0.95 * k);
      if (age < 1000) { var f = 1 - age / 1000; blit(ctx, sprite(it.hex || COL.decide, true), it.x, it.y, 10 + 46 * f, f); }
    });
    S.shimmers.forEach(function (sh, key) {
      if (t > sh.until) { S.shimmers.delete(key); return; }
      drawShimmer(ctx, sh, t);
    });
    var keep = [];
    for (var i = 0; i < S.fx.length; i++) {
      var e = S.fx[i], alive = true;
      try { alive = e.step(ctx, t) !== false; } catch (err) { alive = false; if (window.console) console.error("map effect failed:", err && err.stack || err); }
      if (alive) keep.push(e);
    }
    S.fx = keep.concat(S.fx.slice(S.fx.length));
    ctx.globalAlpha = 1;
  }
  function drawShimmer(ctx, sh, t) {
    var it = S.items.get("ws:" + sh.wsId);
    if (!it) return;
    var pts = route([{ el: it.link }]), n = pts.length / 2, age = (t - sh.t0) / 1000;
    var fadeIn = Math.min(1, age / 0.35), spr = sprite(COL.mubit);
    for (var i = 0; i < n; i += 2) {
      var w = 0.5 + 0.5 * Math.sin(age * 7 - i * 0.32);
      blit(ctx, spr, pts[i * 2], pts[i * 2 + 1], 9 + 10 * w, (0.1 + 0.4 * w * w) * fadeIn);
    }
    blit(ctx, spr, it.x, it.y, 70 + 16 * Math.sin(age * 5), 0.5 * fadeIn);
  }

  /* --------------------------------------------------- canvas primitives */
  function comet(pts, o) {
    var n = pts.length / 2;
    if (n < 2 || reduced) { if (o.onArrive) o.onArrive(); return null; }
    var e = {
      t0: now() + (o.delay || 0), arrived: false, tEnd: 0,
      step: function (ctx, t) {
        var u = (t - this.t0) / (o.dur || 900);
        if (u < 0) return true;
        if (u >= 1 && !this.arrived) this.arrive(t);
        var fade = this.arrived ? 1 - (t - this.tEnd) / 280 : 1;
        if (fade <= 0) return false;
        var head = (o.ease || easeInOut)(Math.min(1, u)) * (n - 1);
        var size = o.size || 12, tail = o.tail || 24, last = n - 1;
        for (var j = tail; j >= 0; j--) {
          var q = head - j * 0.85;
          if (q < 0) continue;
          var i = Math.floor(q), f = q - i, i2 = Math.min(last, i + 1);
          var x = lerp(pts[i * 2], pts[i2 * 2], f), y = lerp(pts[i * 2 + 1], pts[i2 * 2 + 1], f);
          var k = 1 - j / (tail + 1), a = Math.pow(k, 1.7) * fade, sz = size * (0.3 + 0.7 * k);
          var m = o.color2 ? clamp((q / last) * 1.4 - 0.2, 0, 1) : 0;
          if (m < 1) blit(ctx, sprite(o.color), x, y, sz, a * (1 - m));
          if (m > 0) blit(ctx, sprite(o.color2), x, y, sz, a * m);
          if (j === 0) {
            blit(ctx, sprite(m > 0.5 ? o.color2 : o.color, true), x, y, size * 1.7, fade);
            if (o.flare) blit(ctx, sprite(m > 0.5 ? o.color2 : o.color), x, y, size * 4.2, 0.35 * fade);
          }
        }
        return true;
      },
    };
    /* The model change rides on a timer, not on the frame that draws the
       arrival: dropped frames can cost light, never a decision on the map. */
    e.arrive = function (t) {
      if (e.arrived) return;
      e.arrived = true;
      e.tEnd = t || now();
      clearTimeout(e.timer);
      if (o.onArrive) {
        try { o.onArrive(); } catch (err) { if (window.console) console.error("map arrival failed:", err && err.stack || err); }
      }
    };
    e.timer = later((o.delay || 0) + (o.dur || 900) + 8, function () { e.arrive(); });
    S.fx.push(e);
    kick();
    return e;
  }
  function ripple(x, y, hex, r0, r1, dur, width, delay) {
    if (reduced) return;
    S.fx.push({
      t0: now() + (delay || 0),
      step: function (ctx, t) {
        var u = (t - this.t0) / dur;
        if (u < 0) return true;
        if (u >= 1) return false;
        var e = easeOut(u), r = lerp(r0, r1, e), a = Math.pow(1 - u, 1.4);
        ctx.strokeStyle = hex;
        ctx.globalAlpha = a * 0.22;
        ctx.lineWidth = width * 4.5 * (1 - u * 0.5);
        ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.stroke();
        ctx.globalAlpha = a * 0.9;
        ctx.lineWidth = width * (1 - u * 0.6);
        ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.stroke();
        return true;
      },
    });
    kick();
  }
  function flash(x, y, hex, size, dur, delay) {
    if (reduced) return;
    S.fx.push({
      t0: now() + (delay || 0),
      step: function (ctx, t) {
        var u = (t - this.t0) / dur;
        if (u < 0) return true;
        if (u >= 1) return false;
        var a = u < 0.12 ? u / 0.12 : Math.pow(1 - (u - 0.12) / 0.88, 2);
        blit(ctx, sprite(hex, true), x, y, size * (0.55 + 0.45 * easeOut(u)), a);
        blit(ctx, sprite(hex), x, y, size * 2.2, a * 0.45);
        return true;
      },
    });
    kick();
  }
  function burst(x, y, hex, count, dist, dur, delay) {
    if (reduced) return;
    var parts = [];
    for (var i = 0; i < count; i++) {
      var a = (i / count) * TAU + Math.random() * 0.4, d = dist * (0.45 + Math.random() * 0.75);
      parts.push({ dx: Math.cos(a) * d, dy: Math.sin(a) * d, s: 5 + Math.random() * 8, hot: i % 4 === 0, w: 0.7 + Math.random() * 0.6 });
    }
    S.fx.push({
      t0: now() + (delay || 0),
      step: function (ctx, t) {
        var u = (t - this.t0) / dur;
        if (u < 0) return true;
        if (u >= 1) return false;
        var e = easeOut(u);
        parts.forEach(function (p) {
          blit(ctx, sprite(p.hot ? COL.decide : hex, p.hot), x + p.dx * e, y + p.dy * e, p.s * (1 - u * 0.6), Math.pow(1 - u, 1.3) * p.w);
        });
        return true;
      },
    });
    kick();
  }
  /* A dendrite growing: drives the link's draw-in and rides its tip with light,
     reading the live geometry each frame so a relayout under it stays in step. */
  function grow(it, dur, hex, delay, onDone) {
    var link = it.link;
    if (reduced) { if (onDone) onDone(); return; }
    link.style.strokeDasharray = "1 1";
    link.style.strokeDashoffset = "1";
    var fx = {
      t0: now() + (delay || 0), done: false,
      finish: function () {
        if (fx.done) return;
        fx.done = true;
        clearTimeout(fx.timer);
        link.style.strokeDasharray = "";
        link.style.strokeDashoffset = "";
        if (onDone) {
          try { onDone(); } catch (err) { if (window.console) console.error("map grow failed:", err && err.stack || err); }
        }
      },
      step: function (ctx, t) {
        if (fx.done) return false;
        var u = (t - fx.t0) / dur;
        if (u < 0) return true;
        if (u > 1) { fx.finish(); return false; }
        var e = easeGrow(u);
        link.style.strokeDashoffset = String(1 - e);
        routePath.setAttribute("d", link.getAttribute("d"));
        var p = routePath.getPointAtLength(routePath.getTotalLength() * e);
        blit(ctx, sprite(hex, true), p.x, p.y, 20, 1);
        blit(ctx, sprite(hex), p.x, p.y, 54, 0.55);
        return true;
      },
    };
    fx.timer = later((delay || 0) + dur + 30, fx.finish);
    S.fx.push(fx);
    kick();
  }
  function glowLeaf(key, delay) {
    S.born.set(key, now() + (delay || 0));
    S.glowing.add(key);
    kick();
  }

  /* ============================================================== loop */
  var fps = { n: 0, t0: 0, el: null };
  /* One clock for everything: effects are stamped with performance.now() when
     an event plays, so the loop reads the same clock rather than the rAF
     timestamp, which some browsers (and headless capture) run on another base. */
  function loop() {
    var t = now();
    S.raf = 0;
    if (S.fallback) { clearTimeout(S.fallback); S.fallback = 0; }
    var dt = Math.min(64, t - (S.lastT || t));
    S.lastT = t;
    if (S.tween) advanceTween(t);
    if (S.camMove) advanceCam(t);
    if (S.colorTweens.length) stepColors(t);
    if (S.dirty || S.tween) { renderAll(); S.dirty = false; }
    if (!reduced || S.pkDirty) { drawPlankton(t, dt); S.pkDirty = false; }
    drawFx(t);
    if (SHOW_FPS) fpsTick(t);
    var busy = !reduced || S.tween || S.colorTweens.length || S.fx.length || S.glowing.size || S.shimmers.size || S.fxPainted;
    if (busy) kick();
  }
  function stepColors(t) {
    S.colorTweens = S.colorTweens.filter(function (c) {
      var u = (t - c.t0) / c.ms;
      if (u < 0) return true;
      c.apply(rgbStr(mixRgb(c.a, c.b, easeInOut(Math.min(1, u)))));
      return u < 1;
    });
  }
  function fpsTick(t) {
    if (!fps.el) {
      fps.el = document.createElement("div");
      fps.el.style.cssText = "position:fixed;z-index:60;left:50%;bottom:8px;transform:translateX(-50%);font:500 11px var(--font-data);color:#8aa4bf;background:rgba(0,0,0,.5);padding:3px 8px;border-radius:6px";
      document.body.appendChild(fps.el);
      fps.t0 = t;
    }
    fps.n++;
    if (t - fps.t0 >= 1000) {
      var leaves = 0;
      S.items.forEach(function (it) { if (it.type === "leaf") leaves++; });
      fps.el.textContent = Math.round(fps.n * 1000 / (t - fps.t0)) + " fps · " + leaves + " leaves";
      fps.n = 0; fps.t0 = t;
    }
  }
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { if (S.raf) cancelAnimationFrame(S.raf); S.raf = 0; }
    else { S.lastT = 0; kick(); }
  });

  /* ============================================================= bloom */
  /* The load, at most 2.5 s, from CSS keyframes with per-element delays (map.css):
     the soma ignites on a spring, workstream links draw out 0.08 s apart, nodes
     pop, precedent links draw, leaves ripple in by angle, arcs sweep. With the
     class removed the page is exactly its rest state. */
  function bloom() {
    if (reduced) return Promise.resolve();
    svgRoot.classList.add("is-blooming");
    var setD = function (el, d) { el.style.setProperty("--d", d.toFixed(3) + "s"); };
    var wsOrder = new Map();
    S.items.forEach(function (it) {
      if (it.type !== "ws") return;
      var i = it.order || 0;
      wsOrder.set(it.id, i);
      it.link.classList.add("draw"); setD(it.link, 0.22 + i * 0.08);
      setD(it.inner, 0.5 + i * 0.08);
      it.arcTrack.classList.add("arc-sweep"); setD(it.arcTrack, 1.5 + i * 0.08);
      it.arcSegs.forEach(function (s) { s.classList.add("arc-sweep"); setD(s, 1.6 + i * 0.08); });
      it.label.classList.add("fade"); setD(it.label, 1.45 + i * 0.07);
    });
    var kidIdx = new Map();
    S.items.forEach(function (it) {
      if (it.type !== "prec" && it.type !== "wait") return;
      var j = kidIdx.get(it.wsId) || 0;
      kidIdx.set(it.wsId, j + 1);
      var d = 0.74 + (wsOrder.get(it.wsId) || 0) * 0.08 + j * 0.06;
      it.link.classList.add("draw"); setD(it.link, d);
      setD(it.inner, d + 0.34);
    });
    S.items.forEach(function (it) {
      if (it.type !== "leaf") return;
      var frac = (((it.tgt.a - START) % TAU) + TAU) % TAU / TAU;
      var d = 1.18 + frac * 0.78;
      setD(it.el, d);
      it.hair.classList.add("hair"); setD(it.hair, d - 0.05);
    });
    var s = ensureSoma();
    if (M) {
      s.inner.style.opacity = "0";
      M.animate(0.15, 1, { type: "spring", stiffness: 190, damping: 12, mass: 0.9,
        onUpdate: function (v) { s.inner.setAttribute("transform", "scale(" + v.toFixed(4) + ")"); s.inner.style.opacity = String(clamp(v * 1.4, 0, 1)); },
        onComplete: function () { s.inner.removeAttribute("transform"); s.inner.style.opacity = ""; } });
    }
    flash(0, 0, COL.decide, 150, 1100, 60);
    ripple(0, 0, COL.plankton, RING.soma, RING.r1 * 0.95, 1300, 1.6, 120);
    return new Promise(function (res) {
      later(2550, function () {
        svgRoot.classList.remove("is-blooming");
        ["draw", "arc-sweep", "fade"].forEach(function (c) {
          Array.prototype.forEach.call(svgRoot.querySelectorAll("." + c), function (el) { el.classList.remove(c); });
        });
        res();
      });
    });
  }

  /* ======================================================= choreography */
  /* Events play in order. Each returns how long it holds the stage, so a burst
     reads as a sequence; a long queue plays faster to keep up with the stream. */
  function enqueue(ev) {
    S.queue.push(ev);
    if (!S.playing) pump();
  }
  function pump() {
    if (!S.queue.length) {
      S.playing = false;
      if (S.needRefetch) { S.needRefetch = false; later(1600, refetch); }
      return;
    }
    S.playing = true;
    var ev = S.queue.shift(), hold = 0;
    try { hold = play(ev) || 0; } catch (err) { if (window.console) console.error("map event failed", ev, err); }
    var speed = S.queue.length > 10 ? 0.25 : S.queue.length > 4 ? 0.55 : 1;
    later(reduced ? 0 : hold * speed, pump);
  }
  function play(ev) {
    if (DEBUG) debugLog("play " + ev.type + " " + (ev.case_ref || ev.decision_id || ev.precedent_id || ev.workstream || "") + " @" + Math.round(now()));
    switch (ev.type) {
      case "decision.started": return onStarted(ev);
      case "model.call": return onModelCall(ev);
      case "decision.finished":
        if (ev.resolves) return onResolved(ev);
        return ev.verdict === "follow" ? onFollow(ev) : onAsk(ev);
      case "precedent.decided": return onDecided(ev);
      case "precedent.overruled": return onOverruled(ev);
      case "workstream.planted": return onPlanted(ev);
      case "case.answered": return onAnswered(ev);
      case "outcome.reported": return onOutcome(ev);
      default: return 0;
    }
  }
  function wsItem(id) { return S.items.get("ws:" + id); }
  function sectorMid(id) { var it = wsItem(id); return it ? (it.sec.a0 + it.sec.a1) / 2 : 0; }

  function onStarted(ev) {
    var ws = wsById(ev.workstream), it = wsItem(ev.workstream);
    if (!ws || !it) return 0;
    var a = sectorMid(ws.id), hex = hueOf(ws.id);
    var land = polar(a, RING.inlet);
    comet(route([arcD(a - 0.0001, a, RING.arc + 70) + "L" + pt(land)]), { color: hex, dur: 420, size: 10, tail: 12, ease: easeOut,
      onArrive: function () { flash(land[0], land[1], hex, 34, 650); ripple(land[0], land[1], hex, 3, 26, 700, 1.2); } });
    if (ws.reader) {
      var sh = S.shimmers.get(ws.id) || { wsId: ws.id, t0: now(), cases: new Set() };
      sh.cases.add(ev.case_ref);
      sh.until = now() + 15000;
      S.shimmers.set(ws.id, sh);
    }
    return 160;
  }
  function endShimmer(wsId, ref) {
    var sh = S.shimmers.get(wsId);
    if (!sh) return;
    sh.cases.delete(ref);
    if (!sh.cases.size) S.shimmers.delete(wsId);
  }
  function onModelCall(ev) {
    var ws = wsById(ev.workstream), it = wsItem(ev.workstream);
    if (ws) ws.counts.model_calls = (+ws.counts.model_calls || 0) + 1;
    bump("model_calls");
    if (it && ws && ws.reader) {
      /* A call the gateway answered with an error still went through Mubit and
         still counts; its spark is dimmer and warm instead of violet. */
      var failed = +ev.status >= 400;
      comet(route([{ el: it.arcTrack }]), { color: failed ? COL.overrule : MUBIT_LIT, dur: 1000, size: failed ? 8 : 11, tail: 20, flare: !failed });
    }
    return 110;
  }
  function onFollow(ev) {
    var ws = wsById(ev.workstream);
    if (!ws) { S.needRefetch = !FIXTURE; return 0; }
    endShimmer(ws.id, ev.case_ref);
    var p = precById(ws, ev.precedent_id) || activePrec(ws);
    var wIt = wsItem(ws.id), pIt = p && S.items.get("p:" + p.id), hex = hueOf(ws.id);
    var apply = function () {
      var key = leafKey(ws.id, ev.case_ref);
      if (p) {
        p.cases.push({ case_ref: ev.case_ref, decision_id: ev.decision_id, verdict: "follow", at: ev.at, via: ev.caller });
        dropWaiting(ws, ev.case_ref);
      }
      ws.counts.follow = (+ws.counts.follow || 0) + 1;
      sync({ duration: 0.6, hold: [key] });
      var lf = S.items.get(key);
      if (lf) { replay(lf.el, "ignite"); glowLeaf(key); }
      if (pIt) { flash(pIt.x, pIt.y, hex, 60, 700); pulseNode(pIt); }
      bump("repeated");
      ticker(ev);
      announce(ev.case_ref + " followed precedent v" + (p ? p.version : "?") + " in " + ws.label + ".");
    };
    if (!wIt || !pIt) { apply(); return 300; }
    var a = sectorMid(ws.id);
    comet(route([diveD(a, wIt.cur), { el: pIt.link }]), { color: hex, dur: 1000, size: 13, onArrive: apply });
    return 520;
  }
  function onAsk(ev) {
    var ws = wsById(ev.workstream);
    if (!ws) { S.needRefetch = !FIXTURE; return 0; }
    endShimmer(ws.id, ev.case_ref);
    var wIt = wsItem(ws.id);
    var apply = function () {
      if (!ws.waiting.some(function (w) { return w.case_ref === ev.case_ref; })) {
        ws.waiting.push({ case_ref: ev.case_ref, decision_id: ev.decision_id, reasons: ev.reasons || [], at: ev.at, facts: ev.facts || null, text: ev.text || null });
      }
      ws.counts.ask = (+ws.counts.ask || 0) + 1;
      ripple(0, 0, COL.ask, RING.soma, RING.soma + 70, 1100, 2.4);
      flash(0, 0, COL.ask, 90, 800);
      var key = leafKey(ws.id, ev.case_ref);
      sync({ duration: 0.6, hold: [key] });
      var lf = S.items.get(key), wNode = S.items.get("w:" + ws.id);
      if (lf) { replay(lf.el, "ignite"); glowLeaf(key, 350); }
      if (wNode) { pulseNode(wNode, 1.45); ripple(wNode.x, wNode.y, COL.ask, 8, 46, 900, 1.6, 120); }
      bump("asked");
      ticker(ev);
      announce(ev.case_ref + " in " + ws.label + " asks a person" + (ev.reasons && ev.reasons[0] ? ": " + ev.reasons[0] : "") + ".");
    };
    if (!wIt) { apply(); return 300; }
    comet(route([diveD(sectorMid(ws.id), wIt.cur), { el: wIt.link, reverse: true }]), { color: COL.ask, dur: 1250, size: 14, flare: true, onArrive: apply });
    return 600;
  }
  function dropWaiting(ws, ref) {
    ws.waiting = ws.waiting.filter(function (w) { return w.case_ref !== ref; });
    ws.answered = ws.answered.filter(function (w) { return w.case_ref !== ref; });
  }
  /* A waiting case the new precedent now covers: it lifts off its rim slot,
     runs in to the amber node, hops along ring 2 to the precedent, changes from
     amber to the workstream's hue, and lands as a new leaf. One at a time. */
  function onResolved(ev) {
    var ws = wsById(ev.workstream);
    if (!ws) { S.needRefetch = !FIXTURE; return 0; }
    endShimmer(ws.id, ev.case_ref);
    var p = precById(ws, ev.precedent_id) || activePrec(ws);
    var waitingItem = ws.waiting.filter(function (w) { return w.decision_id === ev.resolves || w.case_ref === ev.case_ref; })[0];
    var ref = (waitingItem && waitingItem.case_ref) || ev.case_ref, key = leafKey(ws.id, ref);
    var lf = S.items.get(key), wNode = S.items.get("w:" + ws.id), pIt = p && S.items.get("p:" + p.id), hex = hueOf(ws.id);
    var apply = function () {
      dropWaiting(ws, ref);
      if (p) p.cases.push({ case_ref: ref, decision_id: ev.decision_id, verdict: "follow", at: ev.at, via: ev.caller || "resolved" });
      ws.counts.follow = (+ws.counts.follow || 0) + 1;
      sync({ duration: 0.55, teleport: [key], hold: [key] });
      var it = S.items.get(key);
      if (it) {
        it.el.style.opacity = ""; it.hair.style.opacity = "";
        replay(it.el, "ignite"); glowLeaf(key);
        if (pIt) comet(route([{ el: it.hair }]), { color: hex, dur: 380, size: 9, tail: 10 });
      }
      if (pIt) { flash(pIt.x, pIt.y, hex, 70, 700); pulseNode(pIt, 1.3); }
      var wn = S.items.get("w:" + ws.id);
      if (wn) pulseNode(wn, 0.82);
      bump("repeated");
      ticker(ev);
      announce(ref + " was waiting and now follows precedent v" + (p ? p.version : "?") + ".");
    };
    if (!lf || !wNode || !pIt || reduced) { apply(); return 250; }
    lf.el.style.opacity = "0";
    lf.hair.style.opacity = "0";
    flash(lf.x, lf.y, COL.ask, 30, 500);
    comet(route([{ el: lf.hair, reverse: true }, hopD(wNode.cur, pIt.cur)]), { color: COL.ask, color2: hex, dur: 1150, size: 14, tail: 26, flare: true, onArrive: apply });
    return 520;
  }
  /* Decide once, the money shot: a white shockwave from the soma, a pulse out
     along the workstream's dendrite, a new dendrite grows to ring 2 while the
     sector opens room, and the precedent blooms from white-hot into the hue.
     The waiting cases it covers then arrive as their own decision.finished
     events (resolves: <ask id>) and fly over one by one (onResolved). */
  function onDecided(ev) {
    var ws = wsById(ev.workstream);
    if (!ws) { S.needRefetch = !FIXTURE; return 0; }
    S.needRefetch = !FIXTURE;
    var wIt = wsItem(ws.id), hex = hueOf(ws.id), key = "p:" + ev.precedent_id;
    var apply = function () {
      if (!precById(ws, ev.precedent_id)) {
        ws.precedents.push({ id: ev.precedent_id, version: ev.version, status: "active", text: ev.text || "",
          decided_by: ev.decided_by, decided_at: ev.at, origin_case: ev.origin_case, cases: [] });
      }
      if (ev.origin_case) dropWaiting(ws, ev.origin_case);
      ws.counts.answered = (+ws.counts.answered || 0);
      var created = sync({ duration: 0.95, born: key, hold: [key] });
      var it = S.items.get(key);
      if (!it) return;
      var isNew = created.indexOf(it) >= 0;
      if (!isNew) { setNodeColor(it, hex, 1); return; }
      it.inner.style.opacity = "0";
      it.link.setAttribute("stroke", COL.decide);
      grow(it, 760, COL.decide, 0, function () {
        it.inner.style.opacity = "";
        replay(it.inner, "popin");
        flash(it.x, it.y, COL.decide, 120, 900);
        burst(it.x, it.y, hex, 26, 70, 1300);
        ripple(it.x, it.y, hex, 6, 64, 1200, 2.2, 80);
        ripple(it.x, it.y, COL.decide, 4, 40, 800, 1.4);
        it.born = false;
        colorTween(COL.decide, hex, 1000, function (c) { it.core.setAttribute("fill", c); it.ring.setAttribute("stroke", c); it.link.setAttribute("stroke", c); }, 120);
        later(1150, function () { setNodeColor(it, hex, 1); it.link.setAttribute("stroke", hex); });
        if (ev.origin_case) glowLeaf(leafKey(ws.id, ev.origin_case));
      });
    };
    bump("answered_once");
    ticker(ev);
    announce("Decided once: " + personLabel(ev.decided_by) + " made precedent v" + ev.version + " in " + ws.label + ".");
    if (reduced || !wIt) { apply(); return 200; }
    pulseSoma();
    flash(0, 0, COL.decide, 220, 1100);
    ripple(0, 0, COL.decide, RING.soma, RING.arc + 60, 1700, 4.5);
    ripple(0, 0, COL.decide, RING.soma, RING.r2 + 40, 1300, 2.2, 160);
    comet(route([{ el: wIt.link }]), { color: COL.decide, dur: 560, size: 17, tail: 22, flare: true, delay: 120,
      onArrive: function () { flash(wIt.x, wIt.y, COL.decide, 80, 600); apply(); } });
    return 2200;
  }
  function onOverruled(ev) {
    var hit = findPrecAnywhere(ev.precedent_id);
    S.needRefetch = !FIXTURE;
    if (!hit) return 0;
    var ws = hit.ws, p = hit.p, key = "p:" + p.id, pIt = S.items.get(key), wIt = wsItem(ws.id);
    var apply = function () {
      p.status = "retired";
      p.retired_at = ev.at; p.retired_by = ev.by; p.retire_reason = ev.reason;
      if (pIt) {
        flash(pIt.x, pIt.y, COL.overrule, 110, 900);
        ripple(pIt.x, pIt.y, COL.overrule, 6, 70, 1100, 2.4);
        burst(pIt.x, pIt.y, COL.overrule, 12, 40, 800);
        var from = hueOf(ws.id);
        colorTween(from, COL.slate, 1200, function (c) { pIt.core.setAttribute("fill", c); pIt.ring.setAttribute("stroke", c); pIt.link.setAttribute("stroke", c); }, 150);
      }
      sync({ duration: 0.5, colorMs: 1200, keepColor: new Set([key]) });
      later(reduced ? 0 : 1400, function () { if (S.items.get(key)) updateKidContent(S.items.get(key), { kind: "prec", p: p, wsId: ws.id }); });
      if (pIt) pIt.link.classList.add("is-retired");
      ticker(ev);
      announce("Precedent v" + p.version + " in " + ws.label + " was overruled. Its kind of question asks again.");
    };
    if (reduced || !pIt || !wIt) { apply(); return 300; }
    comet(route([{ el: wIt.link }, { el: pIt.link }]), { color: COL.overrule, dur: 900, size: 14, flare: true, onArrive: apply });
    return 1700;
  }
  function onPlanted(ev) {
    S.needRefetch = !FIXTURE;
    if (wsById(ev.workstream)) return 0;
    var ws = normWs({ id: ev.workstream, label: ev.label || ev.workstream, kind: "planted", audience: ev.audience || "team",
      reader: ev.reader !== false, mubit: null, fields: ev.fields || [] });
    S.model.ws.push(ws);
    var created = sync({ spring: true });
    var it = wsItem(ws.id);
    ticker(ev);
    announce("New workstream planted: " + ws.label + ".");
    if (it && created.indexOf(it) >= 0 && !reduced) {
      it.sec = { a0: it.secTgt.a0 + (it.secTgt.a1 - it.secTgt.a0) / 2, a1: it.secTgt.a0 + (it.secTgt.a1 - it.secTgt.a0) / 2 };
      it.secFrom = { a0: it.sec.a0, a1: it.sec.a1 };
      later(650, function () { flash(it.x, it.y, it.hex, 90, 900); ripple(it.x, it.y, it.hex, 8, 60, 1100, 2); });
      flash(0, 0, it.hex, 120, 900);
    }
    return 1500;
  }
  function onAnswered(ev) {
    var hit = findDecision(ev.decision_id, ev.case_ref);
    if (!hit) { S.needRefetch = !FIXTURE; ticker(ev); return 0; }
    var ws = hit.ws, ref = hit.item.case_ref, key = leafKey(ws.id, ref);
    var lf = S.items.get(key), wIt = wsItem(ws.id), wNode = S.items.get("w:" + ws.id);
    var apply = function () {
      if (hit.kind === "waiting") {
        dropWaiting(ws, ref);
        ws.answered.push({ case_ref: ref, decision_id: ev.decision_id, answer: ev.answer, at: ev.at });
      }
      ws.counts.answered = (+ws.counts.answered || 0) + 1;
      sync({ duration: 0.5, colorMs: 500 });
      if (lf) { flash(lf.x, lf.y, COL.decide, 46, 700); glowLeaf(key); }
      ticker(ev);
      announce(personLabel((S.model.people[0] || {}).id) + " answered " + ref + ": " + ev.answer + (ev.remembered ? ", and remembered it." : "."));
    };
    if (reduced || !lf || !wIt || !wNode) { apply(); return 300; }
    pulseSoma(1.08);
    comet(route([{ el: wIt.link }, { el: wNode.link }, { el: lf.hair }]), { color: COL.decide, dur: 1100, size: 12, tail: 22, onArrive: apply });
    return ev.remembered ? 1250 : 700;
  }
  function onOutcome(ev) {
    var hit = findDecision(ev.decision_id);
    ticker(ev);
    if (!hit) return 0;
    var it = S.items.get(leafKey(hit.ws.id, hit.item.case_ref));
    if (it) flash(it.x, it.y, ev.status === "failed" ? COL.overrule : COL.decide, 26, 600);
    return 120;
  }
  /* Motion picks its own transform origin for SVG groups. Nodes and the soma are
     drawn around their local origin, so a pulse writes scale() to the transform
     attribute and stays centered. */
  function pulseScale(el, peak, dur) {
    if (reduced || !M || !el) return;
    if (el._pulse) { try { el._pulse.stop(); } catch (e) { /* done */ } }
    el._pulse = M.animate(0, 1, { duration: dur, ease: "linear",
      onUpdate: function (u) {
        var s = u < 0.3 ? lerp(1, peak, easeOut(u / 0.3)) : lerp(peak, 1, easeInOut((u - 0.3) / 0.7));
        el.setAttribute("transform", "scale(" + s.toFixed(4) + ")");
      },
      onComplete: function () { el.removeAttribute("transform"); el._pulse = null; } });
  }
  function pulseNode(it, scale) { if (it && it.inner) pulseScale(it.inner, scale || 1.35, 0.6); }
  function pulseSoma(scale) { pulseScale(ensureSoma().inner, scale || 1.22, 0.9); }

  /* =============================================================== HUD */
  var counters = {};
  Array.prototype.forEach.call(document.querySelectorAll(".counter"), function (el) {
    counters[el.getAttribute("data-total")] = { root: el, num: el.querySelector("[data-num]"), v: 0, anim: null, bt: 0 };
  });
  function setCount(key, value, opts) {
    var c = counters[key];
    if (!c) return;
    var old = c.v;
    c.v = value;
    if (c.anim) { try { c.anim.stop(); } catch (e) { /* done */ } c.anim = null; }
    if (reduced || !M || old === value) { c.num.textContent = fmtInt(value); return; }
    c.anim = M.animate(old, value, { duration: (opts && opts.duration) || 0.7, ease: [0.22, 1, 0.36, 1],
      onUpdate: function (x) { c.num.textContent = fmtInt(x); } });
    if (value > old && !(opts && opts.quiet)) {
      c.root.classList.add("is-bumped");
      clearTimeout(c.bt);
      c.bt = setTimeout(function () { c.root.classList.remove("is-bumped"); }, 800);
    }
  }
  function setTotals(totals, opts) { Object.keys(counters).forEach(function (k) { setCount(k, +totals[k] || 0, opts); }); }
  function bump(key) {
    var t = S.model.totals;
    t[key] = (+t[key] || 0) + 1;
    setCount(key, t[key]);
  }

  function tickerParts(ev) {
    var ws = wsById(ev.workstream) || (findDecision(ev.decision_id) || {}).ws || null;
    var ref = '<span class="ref">' + esc(ev.case_ref || "") + "</span>";
    switch (ev.type) {
      case "decision.finished": {
        if (ev.verdict === "follow") {
          var p = ws && (precById(ws, ev.precedent_id) || activePrec(ws));
          var by = p ? " (" + esc(personShort(p.decided_by)) + ", " + esc(timeOf(p.decided_at)) + ")" : "";
          return ["follow", "FOLLOW", ref + " ← precedent v" + esc(p ? p.version : "?") + by + (ev.resolves ? " · was waiting" : "")];
        }
        var who = S.model.people[0] ? personShort(S.model.people[0].id) : "a person";
        return ["ask", "ASK", ref + " → " + esc(who) + (ev.reasons && ev.reasons[0] ? " · " + esc(ev.reasons[0]) : "")];
      }
      case "precedent.decided":
        return ["decide", "DECIDE", "precedent v" + esc(ev.version) + " · " + esc(ws ? ws.label : ev.workstream) + " (" + esc(personShort(ev.decided_by)) + ")"];
      case "precedent.overruled": {
        var hit = findPrecAnywhere(ev.precedent_id);
        return ["overrule", "OVERRULE", "precedent v" + esc(hit ? hit.p.version : "?") + " · " + esc(hit ? hit.ws.label : "") + (ev.reason ? " · " + esc(ev.reason) : "")];
      }
      case "workstream.planted":
        return ["plant", "PLANT", esc(ev.label || ev.workstream) + " · " + esc(ev.audience === "individual" ? "for one person" : "for a team")];
      case "case.answered": {
        var d = findDecision(ev.decision_id);
        return ["answer", "ANSWER", '<span class="ref">' + esc(d ? d.item.case_ref : ev.decision_id) + "</span> · " + esc(ev.answer) + (ev.remembered ? " · remembered" : "")];
      }
      case "outcome.reported": {
        var o = findDecision(ev.decision_id);
        return ["outcome", "OUTCOME", '<span class="ref">' + esc(o ? o.item.case_ref : ev.decision_id) + "</span> · " + esc(ev.status)];
      }
      default: return null;
    }
  }
  function ticker(ev, opts) {
    var parts = tickerParts(ev);
    if (!parts) return;
    var li = document.createElement("li");
    li.innerHTML = "<time>" + esc(timeOf(ev.at, true)) + '</time><span class="v v-' + parts[0] + '">' + parts[1] + "</span>" + parts[2];
    tickerEl.appendChild(li);
    while (tickerEl.children.length > 5) tickerEl.removeChild(tickerEl.firstElementChild);
    if (!(opts && opts.quiet)) li.className = "tick-in";
  }
  /* The ticker starts with the five latest real decisions already in the snapshot. */
  function seedTicker() {
    var evs = [];
    S.model.ws.forEach(function (ws) {
      ws.precedents.forEach(function (p) {
        evs.push({ type: "precedent.decided", at: p.decided_at, workstream: ws.id, version: p.version, decided_by: p.decided_by, precedent_id: p.id });
        p.cases.forEach(function (c) {
          evs.push({ type: "decision.finished", at: c.at, workstream: ws.id, case_ref: c.case_ref, verdict: "follow", precedent_id: p.id });
        });
      });
      ws.waiting.forEach(function (w) {
        evs.push({ type: "decision.finished", at: w.at, workstream: ws.id, case_ref: w.case_ref, verdict: "ask", reasons: w.reasons });
      });
    });
    evs.sort(byAt).slice(-5).forEach(function (ev) { ticker(ev, { quiet: true }); });
  }
  var announcer = $("#announcer");
  function announce(text) {
    announcer.textContent = "";
    later(30, function () { announcer.textContent = text; });
  }
  function setConn(state, text) {
    var el = $("#conn");
    el.setAttribute("data-state", state);
    el.querySelector("span").textContent = text;
  }
  function updateTable() {
    var body = $("#map-table-body");
    if (!body || !S.model) return;
    body.innerHTML = S.model.ws.map(function (ws) {
      var precs = ws.precedents.map(function (p) {
        return "v" + p.version + " (" + (p.status === "active" ? "active" : "retired") + ", " + p.cases.length + " cases): " + (p.text || "");
      }).join("; ") || "none yet";
      return "<tr><th scope=\"row\">" + esc(ws.label) + "</th><td>" + esc(precs) + "</td><td>" + ws.waiting.length + "</td><td>" +
        (+ws.counts.follow || 0) + "</td><td>" + (+ws.counts.ask || 0) + "</td><td>" + (+ws.counts.answered || 0) + "</td><td>" +
        (ws.reader ? (+ws.counts.model_calls || 0) : "rules only") + "</td></tr>";
    }).join("");
  }
  function showNotice(html) {
    var n = $("#map-notice");
    n.innerHTML = html;
    n.hidden = false;
  }

  /* ============================================================ camera */
  /* Camera moves: ~1.2 s on Motion's ease-in-out curve, zoom interpolated in
     log space so it feels even. Driven by the frame loop; the promise settles
     on a timer at the end, so a capture script can await it reliably. */
  function camTo(target, dur) {
    return new Promise(function (resolve) {
      /* A move that interrupts another keeps heading for the other's target in
         whatever it does not set itself (open a drawer mid-zoom: still zooms). */
      var base = S.camMove ? S.camMove.to : S.cam;
      var from = Object.assign({}, S.cam), to = Object.assign({}, base, target);
      if (S.camMove) { var old = S.camMove; S.camMove = null; old.done(); }   // settle it where it is, no jump
      var ms = (dur == null ? 1.2 : dur) * 1000;
      if (reduced || ms <= 0) { S.cam = to; applyCam(); resolve(); return; }
      var mv = { t0: now(), ms: ms, from: from, to: to, lk0: Math.log(from.k), lk1: Math.log(to.k), settled: false };
      mv.done = function () {
        if (mv.settled) return;
        mv.settled = true;
        clearTimeout(mv.timer);
        if (S.camMove === mv) { S.cam = Object.assign({}, to); applyCam(); S.camMove = null; }
        resolve();
      };
      mv.timer = later(ms + 20, mv.done);
      S.camMove = mv;
      kick();
    });
  }
  function advanceCam(t) {
    var mv = S.camMove, u = clamp((t - mv.t0) / mv.ms, 0, 1), e = EASE_CAMERA(u);
    S.cam = { x: lerp(mv.from.x, mv.to.x, e), y: lerp(mv.from.y, mv.to.y, e), k: Math.exp(lerp(mv.lk0, mv.lk1, e)), ox: lerp(mv.from.ox, mv.to.ox, e) };
    applyCam();
    if (u >= 1) mv.done();
  }
  function drawerShift() {
    if (S.view.narrow || drawerEl.hidden) return 0;
    var r = drawerEl.getBoundingClientRect();
    return -(r.width + 16) / 2;
  }

  /* ============================================================ drawer */
  function openDrawer(html, opts) {
    opts = opts || {};
    if (drawerEl.hidden) S.drawerReturn = document.activeElement;
    drawerBody.innerHTML = html;
    drawerBody.scrollTop = 0;
    var wasHidden = drawerEl.hidden || drawerEl.classList.contains("is-leaving");
    clearTimeout(S.drawerTimer);
    drawerEl.classList.remove("is-leaving");
    drawerEl.hidden = false;
    document.documentElement.classList.add("has-drawer");
    /* CSS keyframes (map.css) rather than a scripted spring: they keep running
       when frames are throttled, so the drawer can never be stuck invisible. */
    if (wasHidden && !reduced) replay(drawerEl, "is-entering");
    S.items.forEach(function (it) { if (it.g) it.g.classList.toggle("is-selected", it.key === opts.key); });
    if (opts.wire) opts.wire(drawerBody);
    var p = camTo({ ox: drawerShift() }, 0.7);
    if (opts.focus !== false) drawerEl.focus({ preventScroll: true });
    return p;
  }
  function closeDrawer() {
    if (drawerEl.hidden || drawerEl.classList.contains("is-leaving")) return Promise.resolve();
    S.items.forEach(function (it) { if (it.g) it.g.classList.remove("is-selected"); });
    var finish = function () {
      drawerEl.classList.remove("is-leaving", "is-entering");
      drawerEl.hidden = true;
      document.documentElement.classList.remove("has-drawer");
      drawerBody.innerHTML = "";
      if (S.drawerReturn && S.drawerReturn.focus && document.contains(S.drawerReturn)) {
        try { S.drawerReturn.focus({ preventScroll: true }); } catch (e) { /* gone */ }
      }
    };
    var cam = camTo({ ox: 0 }, 0.7);
    if (reduced) { finish(); return cam; }
    drawerEl.classList.remove("is-entering");
    drawerEl.classList.add("is-leaving");
    var gone = new Promise(function (res) { S.drawerTimer = later(230, function () { finish(); res(); }); });
    return Promise.all([gone, cam]);
  }
  $("#drawer-close").addEventListener("click", function () { closeDrawer(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !drawerEl.hidden) { e.preventDefault(); closeDrawer(); }
  });

  function verdictPill(v, hex) {
    if (v === "follow") return '<span class="pill v-follow" style="--pill:' + esc(hex) + '">Follow</span>';
    if (v === "ask") return '<span class="pill v-ask">Ask</span>';
    if (v === "answered") return '<span class="pill v-answered">Answered by a person</span>';
    return '<span class="pill">' + esc(v || "decision") + "</span>";
  }
  var TICK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7"/></svg>';
  var CROSS = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></svg>';
  function precBlock(p, hex) {
    if (!p) return "";
    return '<div class="quote" style="--qc:' + esc(hex) + '">' + esc(p.text || "") + "</div>" +
      '<p class="byline">Precedent <b>v' + esc(p.version) + "</b> · decided by <b>" + esc(personLabel(p.decided_by)) + "</b> at " +
      esc(timeOf(p.decided_at)) + (p.origin_case ? " · from <b>" + esc(p.origin_case) + "</b>" : "") + "</p>";
  }
  function kvRows(rows) {
    return '<dl class="kv">' + rows.filter(function (r) { return r[1] != null && r[1] !== ""; }).map(function (r) {
      return "<dt>" + esc(r[0]) + "</dt><dd" + (r[2] ? ' title="' + esc(r[2]) + '"' : "") + ">" + esc(r[1]) + "</dd>";
    }).join("") + "</dl>";
  }
  function shortHash(h) { h = String(h || ""); return h.length > 22 ? h.slice(0, 18) + "…" : h; }
  function receiptHtml(rc, ws) {
    var hex = ws ? hueOf(ws.id) : COL.ink, out = "";
    if (rc.precedent) out += "<h3>Precedent</h3>" + precBlock(rc.precedent, hex);
    if (rc.reasons && rc.reasons.length) out += "<h3>Why it asked</h3><ul class=\"reasons\">" + rc.reasons.map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") + "</ul>";
    if (rc.actions && rc.actions.length) {
      out += "<h3>What the caller may execute</h3>" + kvRows(rc.actions.map(function (a) {
        var rest = Object.keys(a).filter(function (k) { return k !== "type"; }).map(function (k) { return k + ": " + a[k]; }).join(", ");
        return [a.type || "action", rest || "—"];
      }));
    }
    if (rc.checks && rc.checks.length) {
      out += "<h3>Checks</h3><ul class=\"checks\">" + rc.checks.map(function (c) {
        return '<li class="' + (c.ok ? "ok" : "no") + '">' + (c.ok ? TICK : CROSS) + "<span>" + esc(c.name) +
          (c.detail ? "<small>" + esc(c.detail) + "</small>" : "") + "</span></li>";
      }).join("") + "</ul>";
    }
    out += "<h3>Model calls via Mubit</h3>";
    if (rc.model_calls && rc.model_calls.length) {
      out += '<ul class="calls">' + rc.model_calls.map(function (m) {
        return "<li>" + esc(m.request_id || "no request ID returned") + "<span>" + esc(m.requested_model || "") + (m.ms != null ? " · " + esc(m.ms) + " ms" : "") + "</span></li>";
      }).join("") + "</ul>";
    } else {
      out += '<p class="note">' + (ws && !ws.reader ? "None: this workstream is rules only." : "None recorded for this decision.") + "</p>";
    }
    out += "<h3>Receipt</h3>" + kvRows([
      ["Decision", rc.decision_id],
      ["Task key", shortHash(rc.task_key), rc.task_key],
      ["Serving identity", rc.serving_identity],
      ["Input hash", shortHash(rc.input_hash), rc.input_hash],
      ["Source revision", rc.source_revision],
      ["Decided at", rc.created_at ? new Date(rc.created_at).toLocaleString() : ""],
      ["Expires", rc.expires_at ? new Date(rc.expires_at).toLocaleString() : ""],
    ]);
    if (rc.answers && rc.answers.length) {
      out += "<h3>Answers</h3>" + kvRows(rc.answers.map(function (a) { return [personLabel(a.actor) || "person", a.answer + (a.remembered ? " · remembered" : "")]; }));
    }
    if (rc.outcomes && rc.outcomes.length) {
      out += "<h3>Outcomes reported by the caller</h3>" + kvRows(rc.outcomes.map(function (o) { return [o.status, (o.actor || "") + (o.at ? " · " + timeOf(o.at, true) : "")]; }));
    }
    if (rc.resolved_by) {
      var rb = typeof rc.resolved_by === "object" ? (rc.resolved_by.decision_id || JSON.stringify(rc.resolved_by)) : rc.resolved_by;
      out += "<h3>Resolved by</h3>" + kvRows([["Decision", rb]]);
    }
    return out;
  }
  function wsDrawerHtml(ws) {
    var hex = hueOf(ws.id), act = activePrec(ws);
    var html = '<p class="kicker">Workstream · ' + esc(ws.kind) + " · " + esc(ws.audience === "individual" ? "for one person" : "for a team") + "</p>" +
      '<h2 id="drawer-title">' + esc(ws.label) + "</h2>" +
      '<div class="pills"><span class="pill v-follow" style="--pill:' + esc(hex) + '">' + (+ws.counts.follow || 0) + " repeated</span>" +
      '<span class="pill v-ask">' + ws.waiting.length + " waiting</span>" +
      '<span class="pill">' + (ws.reader ? (+ws.counts.model_calls || 0) + " model calls" : "rules only") + "</span></div>";
    html += act ? "<h3>Active precedent</h3>" + precBlock(act, hex) : '<p class="note">No active precedent: every case asks a person until someone decides once.</p>';
    html += "<h3>Mubit loop</h3>";
    if (!ws.reader) html += '<p class="note">No model step: this workstream runs on rules only, so there is no Mubit task.</p>';
    else if (!ws.mubit) html += '<p class="note">No console snapshot for this task yet. The map shows only what the Mubit console reports.</p>';
    else html += kvRows([["Stage", ws.mubit.stage], ["Detail", ws.mubit.detail], ["Source", ws.mubit.source], ["As of", ws.mubit.as_of ? new Date(ws.mubit.as_of).toLocaleString() : ""]]);
    if (ws.reader) html += kvRows([["Task key", shortHash(ws.task_key) || "not computed yet", ws.task_key], ["Task key changed", ws.task_key_changed ? "yes: examples may be stranded" : "no"]]);
    if (ws.repeatable_answer) html += "<h3>The one answer a precedent may repeat</h3>" + kvRows([["Repeatable", ws.repeatable_answer], ["Never repeated", ws.other_answers.join(", ") || "—"]]);
    if (ws.fields.length) html += "<h3>Fields</h3>" + kvRows(ws.fields.map(function (f) { return [f.label || f.name, f.kind + (f.ceiling != null ? " · ceiling " + fmtNum(f.ceiling) : "")]; }));
    html += "<h3>Precedents</h3>" + (ws.precedents.length ? '<ul class="list">' + ws.precedents.slice().sort(function (a, b) { return b.version - a.version; }).map(function (p) {
      return '<li><button type="button" data-open="p:' + esc(p.id) + '"><span class="ref"><i class="sw" style="background:' + (p.status === "active" ? esc(hex) : COL.slate) + '"></i>v' + esc(p.version) +
        '</span><span class="why">' + esc(p.text) + '</span><span class="when">' + esc(p.status === "active" ? p.cases.length + " cases" : "retired") + "</span></button></li>";
    }).join("") + "</ul>" : '<p class="note">None yet.</p>');
    if (ws.waiting.length) html += '<h3>Waiting for a person</h3><button type="button" class="btn btn-ghost" data-open="w:' + esc(ws.id) + '">Open the ' + ws.waiting.length + " waiting</button>";
    return html;
  }
  function precDrawerHtml(ws, p) {
    var hex = hueOf(ws.id), retired = p.status !== "active";
    var html = '<p class="kicker">Precedent · ' + esc(ws.label) + "</p>" +
      '<h2 id="drawer-title">Precedent v' + esc(p.version) + "</h2>" +
      '<div class="pills"><span class="pill ' + (retired ? "v-retired" : "v-active") + '">' + (retired ? "Overruled" : "Active") + "</span>" +
      '<span class="pill">' + p.cases.length + " cases followed</span></div>" + precBlock(p, retired ? COL.slate : hex);
    if (retired) {
      html += '<p class="note">Overruled' + (p.retired_by ? " by " + esc(personLabel(p.retired_by)) : "") + (p.retired_at ? " at " + esc(timeOf(p.retired_at)) : "") +
        (p.retire_reason ? ": “" + esc(p.retire_reason) + "”" : "") + ". Nothing replaced it: its kind of question asks a person again.</p>";
    }
    if (p.cases.length) {
      html += "<h3>Cases it decided</h3><ul class=\"list\">" + p.cases.slice().sort(byAt).reverse().slice(0, 12).map(function (c) {
        return '<li><button type="button" data-decision="' + esc(c.decision_id) + '" data-ref="' + esc(c.case_ref) + '"><span class="ref">' + esc(c.case_ref) +
          '</span><span class="why">via ' + esc(c.via || "api") + '</span><span class="when">' + esc(timeOf(c.at, true)) + "</span></button></li>";
      }).join("") + "</ul>" + (p.cases.length > 12 ? '<p class="note">and ' + (p.cases.length - 12) + " earlier.</p>" : "");
    }
    if (!retired) {
      html += "<h3>Overrule</h3><p class=\"note\">Stops this precedent at once. Nothing replaces it; its kind of question asks a person again.</p>" +
        '<form class="form" id="overrule-form" novalidate>' +
        '<label class="field"><span>Reason</span><textarea name="reason" required placeholder="What changed, in one line"></textarea></label>' +
        tokenField() +
        '<button type="submit" class="btn btn-overrule">Overrule precedent v' + esc(p.version) + "</button>" +
        '<div class="form-msg" role="status" hidden></div></form>';
    }
    return html;
  }
  function waitDrawerHtml(ws) {
    var html = '<p class="kicker">Waiting for a person · ' + esc(ws.label) + "</p>" +
      '<h2 id="drawer-title">' + ws.waiting.length + " waiting</h2>" +
      '<p class="note">Each one asked because it differs from every precedent or could not be checked. Answer one and remember it, and the ones it covers resolve on their own.</p>';
    if (ws.waiting.length) {
      html += '<ul class="list">' + ws.waiting.slice().sort(byAt).map(function (w) {
        return '<li><button type="button" data-decision="' + esc(w.decision_id) + '" data-ref="' + esc(w.case_ref) + '"><span class="ref"><i class="sw" style="background:' + COL.ask + '"></i>' + esc(w.case_ref) +
          '</span><span class="why">' + esc((w.reasons || [])[0] || "") + '</span><span class="when">' + esc(timeOf(w.at)) + "</span></button></li>";
      }).join("") + "</ul>";
    }
    if (ws.answered.length) {
      html += "<h3>Answered without remembering</h3>" + kvRows(ws.answered.map(function (a) { return [a.case_ref, a.answer || "answered"]; }));
    }
    return html;
  }
  function tokenField() {
    var t = CONFIG.demo_approval_token;
    return '<label class="field"><span>Approval token</span><input name="approval_token" class="mono" autocomplete="off" spellcheck="false" ' +
      (t ? 'type="text" value="' + esc(t) + '"' : 'type="password" placeholder="Required for person-only actions"') + "></label>" +
      (t ? '<p class="note">Prefilled with the demo token, because the shipped default is still in use.</p>' : "");
  }
  function caseDrawerHtml(ws, hit) {
    var hex = hueOf(ws.id), item = hit.item, waiting = hit.kind === "waiting";
    var verdict = waiting ? "ask" : hit.kind === "answered" || hit.kind === "origin" ? "answered" : (hit.p && hit.p.status !== "active" ? "follow" : "follow");
    var html = '<p class="kicker">' + (waiting ? "Waiting for a person" : "Receipt") + " · " + esc(ws.label) + "</p>" +
      '<h2 id="drawer-title">' + esc(item.case_ref) + "</h2>" +
      '<div class="pills">' + verdictPill(verdict, hex) + (item.via ? '<span class="pill">via ' + esc(item.via) + "</span>" : "") +
      (item.at ? '<span class="pill">' + esc(timeOf(item.at, true)) + "</span>" : "") +
      (hit.p && hit.p.status !== "active" ? '<span class="pill v-retired">precedent overruled</span>' : "") + "</div>";
    if (waiting) {
      if (item.text) html += '<p class="case-text">' + esc(item.text) + "</p>";
      if (item.facts && Object.keys(item.facts).length) {
        html += '<dl class="facts">' + Object.keys(item.facts).map(function (k) {
          var f = ws.fields.filter(function (x) { return x.name === k; })[0];
          return "<dt>" + esc(f ? f.label : k) + "</dt><dd>" + esc(fmtFact(item.facts[k])) + "</dd>";
        }).join("") + "</dl>";
      }
      if (item.reasons && item.reasons.length) html += "<h3>Why it asked</h3><ul class=\"reasons\">" + item.reasons.map(function (r) { return "<li>" + esc(r) + "</li>"; }).join("") + "</ul>";
      if (ws.repeatable_answer) html += "<h3>Your answer</h3>" + answerFormHtml(ws, item);
    }
    if (hit.kind === "origin") html += '<p class="note">Answered by ' + esc(personLabel(hit.p.decided_by)) + " and remembered: this case is where precedent v" + esc(hit.p.version) + " came from.</p>";
    html += '<div id="receipt-slot"><p class="note">Loading the receipt…</p></div>';
    return html;
  }
  function fmtFact(v) { return v === true ? "yes" : v === false ? "no" : v == null ? "—" : typeof v === "number" ? fmtNum(v) : String(v); }
  function answerFormHtml(ws, item) {
    var answers = [ws.repeatable_answer].concat(ws.other_answers || []);
    var amountFields = ws.fields.filter(function (f) { return item.facts && item.facts[f.name] !== undefined; });
    var rows = amountFields.map(function (f) {
      var v = item.facts[f.name];
      var upto = f.kind === "amount"
        ? '<label class="upto">Up to <input type="number" step="any" name="upto:' + esc(f.name) + '" min="' + esc(v) + '"' +
          (f.ceiling != null ? ' max="' + esc(f.ceiling) + '"' : "") + ' placeholder="' + esc(fmtNum(v)) + '">' +
          (f.ceiling != null ? "<span>ceiling " + esc(fmtNum(f.ceiling)) + "</span>" : "") + "</label>"
        : "";
      return '<div class="fieldrow" data-field="' + esc(f.name) + '"><span class="fname">' + esc(f.label || f.name) +
        ' <span class="fobs">' + esc(fmtFact(v)) + "</span></span>" +
        '<label class="toggle sm"><input type="checkbox" name="ignore" value="' + esc(f.name) + '"> Doesn’t matter</label>' + upto + "</div>";
    }).join("");
    return '<form class="form" id="answer-form" novalidate>' +
      '<label class="field"><span>Answer</span><select name="answer">' + answers.map(function (a) { return '<option value="' + esc(a) + '">' + esc(cap(a)) + "</option>"; }).join("") + "</select></label>" +
      '<label class="toggle"><input type="checkbox" name="remember" checked> Remember this: decide once</label>' +
      '<p class="note" data-remember-note hidden>Only “' + esc(ws.repeatable_answer) + "” can be repeated. This answer applies to this case only.</p>" +
      '<div class="fieldset" data-remember-only>' +
      (rows ? '<span class="fieldset-title">What the precedent covers</span>' + rows : '<span class="fieldset-title">The precedent is drafted from this case’s facts</span>') +
      '<div class="preview" data-preview></div></div>' +
      '<label class="field"><span>Note (optional)</span><textarea name="note" placeholder="Why, for the audit trail"></textarea></label>' +
      tokenField() +
      '<button type="submit" class="btn btn-decide">Answer and remember</button>' +
      '<div class="form-msg" role="status" hidden></div></form>';
  }
  /* The draft follows plan 5.3: flags and categories match the observed value,
     amounts stay at or below the larger of the observed value and "up to",
     counts match, and "doesn't matter" drops the field. */
  function draftPreview(ws, item, form) {
    var ignore = new Set(Array.prototype.map.call(form.querySelectorAll('input[name="ignore"]:checked'), function (i) { return i.value; }));
    var parts = [];
    ws.fields.forEach(function (f) {
      if (!item.facts || item.facts[f.name] === undefined || ignore.has(f.name)) return;
      var v = item.facts[f.name];
      if (f.kind === "amount") {
        var u = parseFloat((form.querySelector('[name="upto:' + f.name + '"]') || {}).value);
        parts.push((f.label || f.name) + " ≤ " + fmtNum(Math.max(+v, isFinite(u) ? u : -Infinity)));
      } else if (f.kind === "flag") parts.push((f.label || f.name) + ": " + (v ? "yes" : "no"));
      else parts.push((f.label || f.name) + " = " + fmtFact(v));
    });
    return cap(ws.repeatable_answer) + (parts.length ? " when " + parts.join(", ") : " for cases like this one") + ". Always-ask rules still apply.";
  }
  function formMsg(form, kind, text) {
    var m = form.querySelector(".form-msg");
    m.className = "form-msg is-" + kind;
    m.textContent = text;
    m.hidden = false;
  }
  function wireAnswerForm(root, ws, item) {
    var form = root.querySelector("#answer-form");
    if (!form) return;
    var sel = form.querySelector('[name="answer"]'), rem = form.querySelector('[name="remember"]');
    var set = form.querySelector("[data-remember-only]"), note = form.querySelector("[data-remember-note]");
    var preview = form.querySelector("[data-preview]"), btn = form.querySelector('button[type="submit"]');
    var refresh = function () {
      var repeatable = sel.value === ws.repeatable_answer;
      rem.disabled = !repeatable;
      if (!repeatable) rem.checked = false;
      note.hidden = repeatable;
      set.hidden = !rem.checked;
      Array.prototype.forEach.call(form.querySelectorAll(".fieldrow"), function (row) {
        var ig = row.querySelector('input[name="ignore"]');
        row.classList.toggle("is-ignored", ig.checked);
        var up = row.querySelector(".upto input");
        if (up) up.disabled = ig.checked;
      });
      preview.textContent = draftPreview(ws, item, form);
      btn.textContent = rem.checked ? "Answer and remember" : "Answer this case only";
    };
    form.addEventListener("input", refresh);
    form.addEventListener("change", refresh);
    refresh();
    if (FIXTURE && !SIMULATE) {
      btn.disabled = true;
      formMsg(form, "info", "Fixture mode: nothing is sent. Run the live map to answer.");
    }
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (btn.disabled) return;
      var body = { answer: sel.value, remember: rem.checked, up_to: {}, ignore: [], note: form.querySelector('[name="note"]').value.trim(),
        approval_token: form.querySelector('[name="approval_token"]').value.trim() };
      Array.prototype.forEach.call(form.querySelectorAll('input[name="ignore"]:checked'), function (i) { body.ignore.push(i.value); });
      Array.prototype.forEach.call(form.querySelectorAll('.upto input'), function (i) {
        var v = parseFloat(i.value), name = i.name.replace("upto:", "");
        if (isFinite(v) && body.ignore.indexOf(name) < 0) body.up_to[name] = v;
      });
      if (!body.approval_token) { formMsg(form, "error", "The approval token is required."); return; }
      if (SIMULATE) { simulateAnswer(ws, item, body); formMsg(form, "ok", "Simulated: playing the answer locally."); later(900, closeDrawer); return; }
      btn.disabled = true;
      formMsg(form, "info", "Sending…");
      postJSON("/map/answer/" + encodeURIComponent(item.decision_id), body).then(function (r) {
        btn.disabled = false;
        if (r.status === 403) return formMsg(form, "error", "The approval token was not accepted.");
        if (r.status === 409) return formMsg(form, "error", detailOf(r.data) || "That answer conflicts with the current state.");
        if (!r.ok) return formMsg(form, "error", "Could not answer (HTTP " + r.status + ")" + (detailOf(r.data) ? ": " + detailOf(r.data) : "."));
        formMsg(form, "ok", answerSummary(r.data));
        btn.disabled = true;
      }).catch(function () { btn.disabled = false; formMsg(form, "error", "Network error: nothing was recorded."); });
    });
  }
  function detailOf(d) {
    if (!d) return "";
    if (typeof d.detail === "string") return d.detail;
    if (Array.isArray(d.detail)) return d.detail.map(function (x) { return x.msg || JSON.stringify(x); }).join("; ");
    return "";
  }
  function answerSummary(d) {
    d = d || {};
    var out = [];
    if (d.precedent && typeof d.precedent === "object") out.push("Remembered as precedent v" + (d.precedent.version != null ? d.precedent.version : "?") + ".");
    else out.push("Answered for this case only.");
    if (d.replay && typeof d.replay === "object") {
      var r = d.replay, bits = [];
      ["matched", "total", "agreed", "cases", "false_actions", "false_auto_resolves"].forEach(function (k) { if (r[k] != null) bits.push(k.replace(/_/g, " ") + " " + r[k]); });
      if (bits.length) out.push("Replay: " + bits.join(", ") + ".");
    }
    var n = Array.isArray(d.resolved) ? d.resolved.length : typeof d.resolved === "number" ? d.resolved : 0;
    if (n) out.push(n + " waiting " + (n === 1 ? "question resolves" : "questions resolve") + " on its own; watch the map.");
    return out.join(" ");
  }
  function wireOverruleForm(root, ws, p) {
    var form = root.querySelector("#overrule-form");
    if (!form) return;
    var btn = form.querySelector('button[type="submit"]');
    if (FIXTURE && !SIMULATE) { btn.disabled = true; formMsg(form, "info", "Fixture mode: nothing is sent. Run the live map to overrule."); }
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (btn.disabled) return;
      var reason = form.querySelector('[name="reason"]').value.trim(), token = form.querySelector('[name="approval_token"]').value.trim();
      if (!reason) { formMsg(form, "error", "Say why, in one line: it goes on the audit trail."); return; }
      if (!token) { formMsg(form, "error", "The approval token is required."); return; }
      if (SIMULATE) {
        enqueue(simEvent("precedent.overruled", { precedent_id: p.id, workstream: ws.id, by: (S.model.people[0] || {}).id, reason: reason }));
        later(300, closeDrawer);
        return;
      }
      btn.disabled = true;
      formMsg(form, "info", "Sending…");
      postJSON("/map/overrule/" + encodeURIComponent(p.id), { reason: reason, approval_token: token }).then(function (r) {
        if (r.status === 403) { btn.disabled = false; return formMsg(form, "error", "The approval token was not accepted."); }
        if (r.status === 409) { btn.disabled = false; return formMsg(form, "error", detailOf(r.data) || "It can no longer be overruled."); }
        if (!r.ok) { btn.disabled = false; return formMsg(form, "error", "Could not overrule (HTTP " + r.status + ")."); }
        formMsg(form, "ok", "Overruled. Its kind of question asks a person again.");
      }).catch(function () { btn.disabled = false; formMsg(form, "error", "Network error: nothing was recorded."); });
    });
  }
  function postJSON(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().catch(function () { return null; }).then(function (data) { return { ok: r.ok, status: r.status, data: data }; }); });
  }
  function getReceipt(id) {
    if (!id) return Promise.resolve(null);
    if (FIXTURE) return Promise.resolve(S.model.receipts[id] || null);
    return fetch("/map/receipt/" + encodeURIComponent(id), { headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
  }
  function wireLists(root) {
    Array.prototype.forEach.call(root.querySelectorAll("[data-open]"), function (b) {
      b.addEventListener("click", function () { openKey(b.getAttribute("data-open")); });
    });
    Array.prototype.forEach.call(root.querySelectorAll("[data-decision]"), function (b) {
      b.addEventListener("click", function () { openCase(b.getAttribute("data-decision"), b.getAttribute("data-ref")); });
    });
  }

  function openKey(key) {
    if (!S.model) return Promise.resolve(false);
    if (key.indexOf("ws:") === 0) {
      var ws = wsById(key.slice(3));
      if (!ws) return Promise.resolve(false);
      return openDrawer(wsDrawerHtml(ws), { key: key, wire: wireLists });
    }
    if (key.indexOf("p:") === 0) {
      var hit = findPrecAnywhere(key.slice(2));
      if (!hit) return Promise.resolve(false);
      return openDrawer(precDrawerHtml(hit.ws, hit.p), { key: key, wire: function (root) { wireLists(root); wireOverruleForm(root, hit.ws, hit.p); } });
    }
    if (key.indexOf("w:") === 0) {
      var w = wsById(key.slice(2));
      if (!w) return Promise.resolve(false);
      return openDrawer(waitDrawerHtml(w), { key: key, wire: wireLists });
    }
    if (key.indexOf("lf:") === 0) {
      var it = S.items.get(key);
      if (!it || !it.data) return Promise.resolve(false);
      return openCase(it.data.decision_id, it.data.ref, it.wsId);
    }
    return Promise.resolve(false);
  }
  function openCase(decisionId, ref, wsId) {
    var hit = findDecision(decisionId, ref, wsId);
    if (!hit) return Promise.resolve(false);
    var ws = hit.ws, item = hit.item;
    var p = openDrawer(caseDrawerHtml(ws, hit), { key: hit.kind === "waiting" ? "w:" + ws.id : hit.p ? "p:" + hit.p.id : null,
      wire: function (root) { wireAnswerForm(root, ws, item); } });
    var did = item.decision_id;
    getReceipt(did).then(function (rc) {
      var slot = $("#receipt-slot", drawerBody);
      if (!slot) return;
      if (!rc) {
        rc = { decision_id: did, verdict: hit.kind === "waiting" ? "ask" : "follow", precedent: hit.p || null,
          reasons: item.reasons || [], checks: [], model_calls: [] };
        slot.innerHTML = receiptHtml(rc, ws) + '<p class="note">' + (FIXTURE ? "The fixture carries full receipts for a few cases only." : "No receipt was returned for this case.") + "</p>";
        return;
      }
      slot.innerHTML = receiptHtml(rc, ws);
    }).catch(function (err) {
      var slot = $("#receipt-slot", drawerBody);
      if (slot) slot.innerHTML = '<p class="note">The receipt could not be loaded (' + esc(err.message) + ").</p>";
    });
    return p;
  }

  /* Pointer and keyboard on the map. Leaves are small, so a click picks the
     nearest leaf within a few pixels rather than demanding a direct hit. */
  svgRoot.addEventListener("click", function (e) {
    var node = e.target.closest && e.target.closest("[data-key]");
    if (node) { openKey(node.getAttribute("data-key")); return; }
    var lf = nearestLeaf(e.clientX, e.clientY, 11);
    if (lf) { openKey(lf.key); return; }
    if (!drawerEl.hidden) closeDrawer();
  });
  svgRoot.addEventListener("keydown", function (e) {
    var node = e.target.closest && e.target.closest("[data-key]");
    if (!node) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openKey(node.getAttribute("data-key")); }
  });
  function nearestLeaf(cx, cy, px) {
    var t = S.camT, rect = svgRoot.getBoundingClientRect();
    var wx = (cx - rect.left - t.tx) / t.k, wy = (cy - rect.top - t.ty) / t.k, best = null, bd = (px / t.k) * (px / t.k);
    S.items.forEach(function (it) {
      if (it.type !== "leaf") return;
      var dx = it.x - wx, dy = it.y - wy, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = it; }
    });
    return best;
  }

  /* ============================================================== live */
  function loadSnapshot() {
    var url = FIXTURE ? "/static/pages/map.fixture.json?v=" + encodeURIComponent(CONFIG.asset_version || "") : "/map/data";
    return fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" }).then(function (r) {
      if (!r.ok) { var e = new Error("HTTP " + r.status); e.status = r.status; throw e; }
      return r.json();
    });
  }
  function connect() {
    if (!window.EventSource) { setConn("error", "No live stream"); return; }
    var es = new EventSource("/map/events" + (S.lastSeq ? "?last_event_id=" + S.lastSeq : ""));
    es.onopen = function () { setConn("live", "Live"); };
    es.onerror = function () { setConn("retry", "Reconnecting"); };
    es.onmessage = function (m) {
      var ev;
      try { ev = JSON.parse(m.data); } catch (e) { return; }
      if (!ev || !ev.type) return;
      var seq = +ev.seq || +m.lastEventId || 0;
      if (seq && seq <= S.lastSeq) return;
      if (seq && S.lastSeq && seq > S.lastSeq + 1) S.needRefetch = true;
      if (seq) S.lastSeq = seq;
      enqueue(ev);
    };
    S.es = es;
  }
  /* After structural events the snapshot is the truth: fetch it and tween to it. */
  function refetch() {
    if (FIXTURE) return;
    if (S.playing) { S.needRefetch = true; return; }
    loadSnapshot().then(function (snap) {
      if (S.playing) { S.needRefetch = true; return; }
      var m = normalize(snap);
      m.ws.forEach(function (ws) { hueOf(ws.id); });
      S.model = m;
      S.lastSeq = Math.max(S.lastSeq, m.lastSeq);
      renderSomaText();
      sync({ duration: 0.8 });
      setTotals(m.totals, { quiet: true });
    }).catch(function () { /* the next structural event retries */ });
  }

  /* ========================================================= simulate */
  function simEvent(type, fields) {
    return Object.assign({ seq: ++S.simSeq, at: new Date().toISOString(), type: type }, fields);
  }
  var simIds = 0;
  function rid() { var s = "mreq_"; for (var i = 0; i < 12; i++) s += "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"[Math.floor(Math.random() * 57)]; return s; }
  function did() { simIds++; return "dec_sim" + String(Date.now()).slice(-6) + simIds; }
  function waitingId(wsId, ref) {
    var ws = wsById(wsId);
    var w = ws && ws.waiting.filter(function (x) { return x.case_ref === ref; })[0];
    return w ? w.decision_id : null;
  }
  /* Development only, under the SIMULATED badge: a scripted stream that walks
     every choreography, then resets to the fixture and plays again. */
  function runSimulation() {
    var R = "returns.missing_accessory", AP = "ap.invoice_variance", EX = "expenses.meal_claim";
    var dana = "dana.r@wharf-canary-optics.example";
    var steps = [
      [0, function () { return simEvent("decision.started", { workstream: R, case_ref: "CASE-2019", caller: "agent" }); }],
      [350, function () { return simEvent("model.call", { workstream: R, case_ref: "CASE-2019", request_id: rid(), requested_model: "gpt-6-luna", ms: 1840 }); }],
      [700, function () { return simEvent("model.call", { workstream: R, case_ref: "CASE-2019", request_id: rid(), requested_model: "gpt-6-luna", ms: 960 }); }],
      [1300, function () { return simEvent("decision.finished", { decision_id: did(), workstream: R, case_ref: "CASE-2019", verdict: "follow", precedent_id: "pol_9c1e2a40", reasons: [], resolves: null, caller: "agent" }); }],
      [2600, function () { return simEvent("decision.started", { workstream: EX, case_ref: "EXP-1051", caller: "cli" }); }],
      [2900, function () { return simEvent("model.call", { workstream: EX, case_ref: "EXP-1051", request_id: rid(), requested_model: "gpt-6-luna", ms: 1210 }); }],
      [3500, function () { return simEvent("decision.finished", { decision_id: did(), workstream: EX, case_ref: "EXP-1051", verdict: "follow", precedent_id: "pol_e7c2f913", reasons: [], resolves: null, caller: "cli" }); }],
      [4700, function () { return simEvent("decision.started", { workstream: EX, case_ref: "EXP-1052", caller: "api" }); }],
      [5000, function () { return simEvent("model.call", { workstream: EX, case_ref: "EXP-1052", request_id: rid(), requested_model: "gpt-6-luna", ms: 1530 }); }],
      [5600, function () { return simEvent("decision.finished", { decision_id: did(), workstream: EX, case_ref: "EXP-1052", verdict: "ask", precedent_id: null, reasons: ["amount_usd 118.00 is above the precedent's 75.00"], resolves: null, caller: "api" }); }],
      [7000, function () { return simEvent("decision.started", { workstream: AP, case_ref: "AP-2008", caller: "agent" }); }],
      [7400, function () { return simEvent("decision.finished", { decision_id: did(), workstream: AP, case_ref: "AP-2008", verdict: "ask", precedent_id: null, reasons: ["no precedent yet: a person decides once"], resolves: null, caller: "agent", facts: { variance_pct: 1.2, vendor_risk: "low", goods_received: true } }); }],
      [9400, function () { return simEvent("case.answered", { decision_id: waitingId(AP, "AP-2001"), answer: "approve the variance", remembered: true }); }],
      [9500, function () { return simEvent("precedent.decided", { precedent_id: "pol_a1f04d2b", workstream: AP, version: 1, decided_by: dana, origin_case: "AP-2001", text: "Approve invoice variances up to 2.0% when the goods were received in full and the vendor is low risk." }); }],
      [9600, function () { return simEvent("decision.finished", { decision_id: did(), workstream: AP, case_ref: "AP-2002", verdict: "follow", precedent_id: "pol_a1f04d2b", reasons: [], resolves: waitingId(AP, "AP-2002"), caller: "replay" }); }],
      [9650, function () { return simEvent("decision.finished", { decision_id: did(), workstream: AP, case_ref: "AP-2006", verdict: "follow", precedent_id: "pol_a1f04d2b", reasons: [], resolves: waitingId(AP, "AP-2006"), caller: "replay" }); }],
      [9700, function () { return simEvent("decision.finished", { decision_id: did(), workstream: AP, case_ref: "AP-2008", verdict: "follow", precedent_id: "pol_a1f04d2b", reasons: [], resolves: waitingId(AP, "AP-2008"), caller: "replay" }); }],
      [16500, function () { return simEvent("decision.started", { workstream: R, case_ref: "CASE-2020", caller: "api" }); }],
      [16800, function () { return simEvent("model.call", { workstream: R, case_ref: "CASE-2020", request_id: rid(), requested_model: "gpt-6-luna", ms: 2020 }); }],
      [17500, function () { return simEvent("decision.finished", { decision_id: did(), workstream: R, case_ref: "CASE-2020", verdict: "ask", precedent_id: null, reasons: ["Banyan Sapling could not confirm the evidence (insufficient information)"], resolves: null, caller: "api" }); }],
      [19500, function () { return simEvent("workstream.planted", { workstream: "it.access_request", label: "IT · access requests", audience: "team", reader: true }); }],
      [21500, function () { return simEvent("decision.started", { workstream: "it.access_request", case_ref: "ACC-0001", caller: "agent" }); }],
      [21800, function () { return simEvent("model.call", { workstream: "it.access_request", case_ref: "ACC-0001", request_id: rid(), requested_model: "gpt-6-luna", ms: 1400 }); }],
      [22500, function () { return simEvent("decision.finished", { decision_id: did(), workstream: "it.access_request", case_ref: "ACC-0001", verdict: "ask", precedent_id: null, reasons: ["no precedent yet: a person decides once"], resolves: null, caller: "agent" }); }],
      [25000, function () { return simEvent("precedent.overruled", { precedent_id: "pol_e7c2f913", workstream: EX, by: dana, reason: "Finance lowered the meal limit to $60." }); }],
      [27500, function () { return simEvent("decision.started", { workstream: EX, case_ref: "EXP-1053", caller: "api" }); }],
      [27800, function () { return simEvent("model.call", { workstream: EX, case_ref: "EXP-1053", request_id: rid(), requested_model: "gpt-6-luna", ms: 1330 }); }],
      [28400, function () { return simEvent("decision.finished", { decision_id: did(), workstream: EX, case_ref: "EXP-1053", verdict: "ask", precedent_id: null, reasons: ["no active precedent: a person decides once"], resolves: null, caller: "api" }); }],
    ];
    S.simTimers.forEach(clearTimeout);
    S.simTimers = steps.map(function (s) { return later(s[0], function () { enqueue(s[1]()); }); });
    S.simTimers.push(later(36000, function () {
      if (S.playing || S.queue.length) { S.simTimers.push(later(3000, runSimulation)); return; }
      S.model = normalize(S.fixtureSnap);
      sync({ duration: 1.1 });
      setTotals(S.model.totals, { quiet: true });
      later(2500, runSimulation);
    }));
  }
  /* The answer form in simulate mode plays what the server would emit, judged
     against the waiting cases' facts with the same draft rules as the preview. */
  function simulateAnswer(ws, item, body) {
    var dana = (S.model.people[0] || {}).id;
    enqueue(simEvent("case.answered", { decision_id: item.decision_id, answer: body.answer, remembered: body.remember }));
    if (!body.remember || body.answer !== ws.repeatable_answer) return;
    var pid = "pol_sim" + (++simIds), version = ws.precedents.reduce(function (m, p) { return Math.max(m, +p.version || 0); }, 0) + 1;
    var covers = function (w) {
      if (!w.facts || w.case_ref === item.case_ref) return false;
      return ws.fields.every(function (f) {
        if (body.ignore.indexOf(f.name) >= 0 || item.facts[f.name] === undefined) return true;
        var mine = item.facts[f.name], theirs = w.facts[f.name];
        if (f.kind === "amount") return +theirs <= Math.max(+mine, body.up_to[f.name] || -Infinity);
        return theirs === mine;
      });
    };
    enqueue(simEvent("precedent.decided", { precedent_id: pid, workstream: ws.id, version: version, decided_by: dana, origin_case: item.case_ref, text: draftPreview(ws, item, $("#answer-form") || document.createElement("form")) }));
    ws.waiting.filter(covers).forEach(function (w) {
      enqueue(simEvent("decision.finished", { decision_id: did(), workstream: ws.id, case_ref: w.case_ref, verdict: "follow", precedent_id: pid, reasons: [], resolves: w.decision_id, caller: "replay" }));
    });
  }
  function padForStress(model, n) {
    var have = 0;
    model.ws.forEach(function (ws) { ws.precedents.forEach(function (p) { have += p.cases.length + 1; }); have += ws.waiting.length; });
    var i = 0, base = Date.parse(model.generatedAt || new Date().toISOString()) - 3600e3;
    while (have < n && model.ws.length) {
      var ws = model.ws[i % model.ws.length], p = activePrec(ws);
      var at = new Date(base + i * 7000).toISOString();
      if (p) p.cases.push({ case_ref: "SIM-" + (5000 + i), decision_id: "dec_stress_" + i, verdict: "follow", at: at, via: "api" });
      else ws.waiting.push({ case_ref: "SIM-" + (5000 + i), decision_id: "dec_stress_" + i, reasons: ["stress test"], at: at, facts: {}, text: "" });
      have++; i++;
    }
  }

  /* ============================================================= boot */
  window.precedenzMap = {
    ready: ready,
    focus: function (workstreamId, opts) {
      opts = opts || {};
      var it = wsItem(workstreamId);
      if (!it) return Promise.resolve(false);
      var a = (it.secTgt.a0 + it.secTgt.a1) / 2, p = polar(a, (RING.r2 + RING.arc) / 2);
      return camTo({ x: p[0], y: p[1], k: opts.zoom || 1.9 }, opts.duration).then(function () { return true; });
    },
    reset: function (opts) { return camTo({ x: 0, y: 0, k: 1 }, opts && opts.duration).then(function () { return true; }); },
    openReceipt: function (decisionId) { return Promise.resolve(openCase(decisionId)).then(function (r) { return r !== false; }); },
    openWaiting: function (workstreamId) { return Promise.resolve(openKey("w:" + workstreamId)).then(function (r) { return r !== false; }); },
    openPrecedent: function (precedentId) { return Promise.resolve(openKey("p:" + precedentId)).then(function (r) { return r !== false; }); },
    closeDrawer: function () { return closeDrawer().then(function () { return true; }); },
  };

  function boot() {
    if (PRESENT) document.documentElement.classList.add("is-present");
    if (SIMULATE || STRESS) $("#sim-badge").hidden = false;
    resize();
    initPlankton();
    kick();
    loadSnapshot().then(function (snap) {
      S.fixtureSnap = snap;
      S.model = normalize(snap);
      if (STRESS) padForStress(S.model, STRESS);
      S.model.ws.forEach(function (ws) { hueOf(ws.id); });
      S.lastSeq = S.model.lastSeq;
      renderSomaText();
      sync({ instant: true, bloom: true });
      renderAll();
      setTotals(S.model.totals, { duration: 1.8, quiet: true });
      seedTicker();
      if (FIXTURE) setConn("fixture", SIMULATE ? "Simulated" : STRESS ? "Stress test" : "Fixture");
      else connect();
      return bloom();
    }).then(function () {
      readyResolve(true);
      if (SIMULATE) later(1200, runSimulation);
      /* Development: drive the recording API from the URL, for screenshots. */
      var api = window.precedenzMap;
      if (qs.get("focus")) api.focus(qs.get("focus"), { zoom: parseFloat(qs.get("zoom")) || undefined });
      if (qs.get("receipt")) api.openReceipt(qs.get("receipt"));
      else if (qs.get("open")) openKey(qs.get("open"));
    }).catch(function (err) {
      setConn("error", "Offline");
      showNotice("<h2>The live map data is not available</h2><p>GET <code>" + (FIXTURE ? "map.fixture.json" : "/map/data") + "</code> answered " +
        esc(err && err.status ? "HTTP " + err.status : "with a network error") + ". Nothing is shown rather than something made up.</p>" +
        "<p>To see the layout with synthetic data, open <a href=\"/map?fixture=1\"><code>/map?fixture=1</code></a>.</p>");
      readyResolve(false);
    });
  }
  /* Size from the stage itself: a ResizeObserver also catches the viewport
     settling after load, which a window resize event can miss. */
  function onStageResize() {
    var stage = $("#stage");
    if (stage.clientWidth === S.view.w && stage.clientHeight === S.view.h) return;
    var wasNarrow = S.view.narrow;
    resize();
    if (S.model && wasNarrow !== S.view.narrow) sync({ instant: true });
    if (!drawerEl.hidden) camTo({ ox: drawerShift() }, 0.3);
    S.dirty = true;
    kick();
  }
  if (window.ResizeObserver) new ResizeObserver(onStageResize).observe($("#stage"));
  else window.addEventListener("resize", onStageResize);
  if (mqReduced && mqReduced.addEventListener) {
    mqReduced.addEventListener("change", function (e) { reduced = e.matches; S.pkDirty = true; kick(); });
  }
  boot();
})();
