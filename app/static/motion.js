/* Precedenz motion primitives, on top of the vendored Motion library
   (static/vendor/motion-*.js, global `Motion`). Loaded on every page by
   base.html, after Motion and before waiting.js and {% block scripts %}, so a
   page script can use window.PZMotion directly.

   Declarative (data attributes, picked up on load):

     data-animate="fade-up"     Element enters on page load. Presets: fade,
                                fade-up, fade-down, fade-left, fade-right,
                                scale, blur. Empty value means fade-up. Every
                                data-animate / data-stagger on the page
                                cascades in document order.
     data-stagger="fade-up"     On a container: its direct children enter one
                                after another. data-stagger-step="0.06" sets
                                the gap in seconds (default 0.05, capped).
     data-reveal="fade-up"      Like data-animate, but waits until the element
                                scrolls into view. Combine with data-stagger
                                to stagger a container's children on reveal.
     data-delay="0.2"           Extra delay in seconds on any of the above.
     data-countup               Counts the first number in the element up from
                                0 when it comes into view, keeping its format:
                                "65.3K", "100%", "$14.00", "1,204". Optional
                                value is the duration in seconds.
     data-countup-from="40"     Start value instead of 0.
     data-highlight             Pulses an accent ring once after it appears
                                (e.g. the row that just changed). The element
                                named by the URL #hash is pulsed on load too.
     data-press                 Spring press feedback. Every .btn gets it.
     data-lift                  Rises 3px on hover with a spring (cards).

   Imperative (window.PZMotion):

     PZMotion.enter(els, "fade-up", {delay, stagger})  animate elements in
     PZMotion.scan(rootEl)          wire the data attributes inside new HTML
     PZMotion.countUp(el)           run a count-up now
     PZMotion.highlight(el, {color, times})  pulse a ring (any CSS color)
     PZMotion.flip(els, () => { ...move DOM... }, {spring})
                                    FLIP: measure, mutate, spring to place
     PZMotion.animate(el, keyframes, options)  raw Motion animate
     PZMotion.springs.{snappy,gentle,bouncy,press}, PZMotion.ease.out
     PZMotion.active                false under reduced motion or if Motion
                                    failed to load

   Rules: content is visible by default. CSS hides an element ahead of its
   entrance only under html.has-motion, which base.html sets when motion is
   welcome and removes again if this file never registers. Under
   prefers-reduced-motion every primitive is a no-op (flip still runs the
   mutation; animate jumps to the end). */
(function () {
  "use strict";

  var root = document.documentElement;
  var M = window.Motion;
  var reduced = !window.matchMedia || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var active = !reduced && !!(M && M.animate && M.inView);

  var EASE_OUT = [0.22, 1, 0.36, 1];
  var SPRINGS = {
    snappy: { type: "spring", stiffness: 520, damping: 38, mass: 1 },
    gentle: { type: "spring", stiffness: 220, damping: 26, mass: 1 },
    bouncy: { type: "spring", stiffness: 400, damping: 17, mass: 1 },
    press: { type: "spring", stiffness: 700, damping: 30, mass: 0.8 },
  };
  var PRESETS = {
    "fade": { opacity: [0, 1] },
    "fade-up": { opacity: [0, 1], y: [14, 0] },
    "fade-down": { opacity: [0, 1], y: [-14, 0] },
    "fade-left": { opacity: [0, 1], x: [18, 0] },
    "fade-right": { opacity: [0, 1], x: [-18, 0] },
    "scale": { opacity: [0, 1], scale: [0.96, 1] },
    "blur": { opacity: [0, 1], y: [10, 0], filter: ["blur(8px)", "blur(0px)"] },
  };
  var PRESS = ".btn, [data-press]";
  var DONE = Promise.resolve();

  function list(targets) {
    if (!targets) return [];
    if (typeof targets === "string") return Array.prototype.slice.call(document.querySelectorAll(targets));
    if (targets.nodeType === 1) return [targets];
    return Array.prototype.slice.call(targets);
  }

  function within(scope, selector) {
    var found = list(scope.querySelectorAll(selector));
    if (scope.nodeType === 1 && scope.matches(selector)) found.unshift(scope);
    return found;
  }

  function num(value, fallback) {
    var n = parseFloat(value);
    return isNaN(n) ? fallback : n;
  }

  /* Marks an element as arrived and hands its styles back to the stylesheet. */
  function settle(el) {
    el.classList.add("is-in");
    el.style.opacity = "";
    el.style.transform = "";
    el.style.filter = "";
  }

  function raw(el, keyframes, options) {
    if (!M || !M.animate) return DONE;
    return M.animate(el, keyframes, active ? options : { duration: 0 });
  }

  // ------------------------------------------------------------ entrances
  function enter(targets, preset, opts) {
    var els = list(targets).filter(function (el) { return !el.classList.contains("is-in"); });
    if (!active) { els.forEach(settle); return DONE; }
    opts = opts || {};
    var keyframes = PRESETS[preset] || PRESETS["fade-up"];
    var step = num(opts.stagger, 0.05);
    var cap = num(opts.maxDelay, 0.6);
    var base = num(opts.delay, 0);
    return Promise.all(els.map(function (el, i) {
      var delay = base + Math.min(i * step, cap) + num(el.getAttribute("data-delay"), 0);
      return M.animate(el, keyframes, { duration: num(opts.duration, 0.65), ease: EASE_OUT, delay: delay })
        .then(function () { settle(el); });
    }));
  }

  function enterUnit(el, delay) {
    if (el.hasAttribute("data-stagger")) {
      var preset = el.getAttribute("data-stagger") || el.getAttribute("data-reveal") || el.getAttribute("data-animate");
      return enter(el.children, preset, {
        delay: delay + num(el.getAttribute("data-delay"), 0),
        stagger: el.getAttribute("data-stagger-step"),
      });
    }
    var name = el.getAttribute("data-animate") || el.getAttribute("data-reveal");
    return enter([el], name, { delay: delay });
  }

  function onceInView(el, fn) {
    var stop = M.inView(el, function () {
      fn();
      if (stop) stop();
    }, { amount: 0.15 });
  }

  // -------------------------------------------------------------- count-up
  var NUMBER = /^([^\d-]*)(-?\d[\d,]*(?:\.\d+)?)([\s\S]*)$/;

  function numberNode(el) {
    var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    for (var node = walker.nextNode(); node; node = walker.nextNode()) {
      if (/\d/.test(node.nodeValue)) return node;
    }
    return null;
  }

  function parseCount(el) {
    var node = numberNode(el);
    var m = node && NUMBER.exec(node.nodeValue);
    if (!m) return null;
    var digits = m[2];
    return {
      node: node,
      text: node.nodeValue,
      prefix: m[1],
      suffix: m[3],
      target: parseFloat(digits.replace(/,/g, "")),
      decimals: (digits.split(".")[1] || "").length,
      grouped: digits.indexOf(",") !== -1,
      from: num(el.getAttribute("data-countup-from"), 0),
    };
  }

  function format(c, value) {
    var s = value.toFixed(c.decimals);
    if (c.grouped) {
      var parts = s.split(".");
      parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
      s = parts.join(".");
    }
    return c.prefix + s + c.suffix;
  }

  function countUp(el, parsed) {
    var c = parsed || parseCount(el);
    if (!c || !active) return DONE;
    var controls = M.animate(c.from, c.target, {
      duration: num(el.getAttribute("data-countup"), 1.2),
      ease: EASE_OUT,
      onUpdate: function (v) { c.node.nodeValue = format(c, v); },
    });
    return controls.then(function () { c.node.nodeValue = c.text; });
  }

  // ------------------------------------------------------------- highlight
  // A theme token ("#rrggbb") as rgba at the given alpha, read live so a pulse
  // matches whichever theme is showing.
  function tokenColor(name, alpha, el) {
    var v = getComputedStyle(el || document.documentElement).getPropertyValue(name).trim();
    var m = /^#([0-9a-f]{6})$/i.exec(v);
    if (!m) return null;
    var n = parseInt(m[1], 16);
    return "rgba(" + (n >> 16) + ", " + ((n >> 8) & 255) + ", " + (n & 255) + ", " + alpha + ")";
  }

  function highlight(el, opts) {
    if (!el || !active || !el.animate) return DONE;
    opts = opts || {};
    var color = opts.color || tokenColor("--accent-mark", 0.55) || "rgba(255, 223, 43, .55)";
    var ring = el.animate([
      { boxShadow: "0 0 0 0 " + color },
      { boxShadow: "0 0 0 14px transparent" },
    ], { duration: 1100, easing: "cubic-bezier(.22, 1, .36, 1)", iterations: opts.times || 2 });
    return ring.finished.catch(function () {});
  }

  // ------------------------------------------------------------------ FLIP
  function flip(targets, mutate, opts) {
    var els = list(targets);
    if (!active) { mutate(); return DONE; }
    var first = els.map(function (el) { return el.getBoundingClientRect(); });
    mutate();
    var spring = (opts && opts.spring) || SPRINGS.snappy;
    return Promise.all(els.map(function (el, i) {
      if (!el.isConnected) return DONE;
      var a = first[i];
      var b = el.getBoundingClientRect();
      var dx = a.left - b.left;
      var dy = a.top - b.top;
      var sx = b.width ? a.width / b.width : 1;
      var sy = b.height ? a.height / b.height : 1;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) return DONE;
      el.style.transformOrigin = "0 0";
      return M.animate(el, { x: [dx, 0], y: [dy, 0], scaleX: [sx, 1], scaleY: [sy, 1] }, spring)
        .then(function () { el.style.transform = ""; el.style.transformOrigin = ""; });
    }));
  }

  // ------------------------------------------------------------------ scan
  function scan(scope) {
    scope = scope || document;
    if (!active) {
      within(scope, "[data-animate], [data-reveal]").forEach(settle);
      within(scope, "[data-stagger]").forEach(function (c) { list(c.children).forEach(settle); });
      return;
    }

    // Page-load entrances cascade in document order.
    var units = within(scope, "[data-animate], [data-stagger]:not([data-reveal])").filter(function (el) {
      var parent = el.parentElement;
      return !el.hasAttribute("data-reveal") && !(parent && parent.hasAttribute("data-stagger"));
    });
    units.forEach(function (el, i) { enterUnit(el, Math.min(i * 0.07, 0.45)); });

    within(scope, "[data-reveal]").forEach(function (el) {
      var parent = el.parentElement;
      if (parent && parent.hasAttribute("data-stagger") && !el.hasAttribute("data-stagger")) return;
      onceInView(el, function () { enterUnit(el, 0); });
    });

    within(scope, "[data-countup]").forEach(function (el) {
      var c = parseCount(el);
      if (!c) return;
      c.node.nodeValue = format(c, c.from);
      onceInView(el, function () { countUp(el, c); });
    });

    within(scope, "[data-highlight]").forEach(function (el) {
      onceInView(el, function () { setTimeout(function () { highlight(el); }, 450); });
    });
  }

  // ------------------------------------------------- press, lift, and nav
  function wireGestures() {
    document.addEventListener("pointerdown", function (event) {
      var el = event.target.closest && event.target.closest(PRESS);
      if (!el || el.disabled || event.button > 0) return;
      M.animate(el, { scale: 0.96 }, SPRINGS.press);
      function release() {
        window.removeEventListener("pointerup", release);
        window.removeEventListener("pointercancel", release);
        M.animate(el, { scale: 1 }, SPRINGS.bouncy);
      }
      window.addEventListener("pointerup", release);
      window.addEventListener("pointercancel", release);
    });

    function lift(event, y) {
      if (event.pointerType !== "mouse") return;
      var el = event.target.closest && event.target.closest("[data-lift]");
      if (!el || el.contains(event.relatedTarget)) return;
      M.animate(el, { y: y }, SPRINGS.gentle);
    }
    document.addEventListener("pointerover", function (e) { lift(e, -3); });
    document.addEventListener("pointerout", function (e) { lift(e, 0); });
  }

  /* The indicator lives inside the current page's link (so the view
     transition can carry it between pages); on hover it glides to the
     hovered link and springs back when the pointer leaves the nav. */
  function wireNav() {
    var nav = document.querySelector(".nav");
    var indicator = nav && nav.querySelector(".nav-indicator");
    if (!indicator) return;
    var home = indicator.parentElement;
    var away = false;

    function moveTo(link) {
      if (link === home && !away) return;
      away = link !== home;
      var to = { x: link.offsetLeft - home.offsetLeft, width: link.offsetWidth + "px" };
      var run = M.animate(indicator, to, SPRINGS.snappy);
      if (!away) run.then(function () {
        if (away) return;
        indicator.style.transform = "";
        indicator.style.width = "";
      });
    }

    nav.addEventListener("pointerover", function (e) {
      var link = e.target.closest(".nav-link");
      if (link && e.pointerType === "mouse") moveTo(link);
    });
    nav.addEventListener("pointerleave", function () { moveTo(home); });
    nav.addEventListener("focusin", function (e) {
      var link = e.target.closest(".nav-link");
      if (link) moveTo(link);
    });
    nav.addEventListener("focusout", function (e) {
      if (!nav.contains(e.relatedTarget)) moveTo(home);
    });
  }

  function highlightTarget() {
    var id = window.location.hash.slice(1);
    var el = id && document.getElementById(decodeURIComponent(id));
    if (el) setTimeout(function () { highlight(el); }, 500);
  }

  window.PZMotion = {
    active: active,
    enter: enter,
    scan: scan,
    countUp: function (el) { return countUp(el); },
    highlight: highlight,
    tokenColor: tokenColor,
    pulse: highlight,
    flip: flip,
    animate: raw,
    springs: SPRINGS,
    presets: PRESETS,
    ease: { out: EASE_OUT },
  };

  try {
    scan(document);
    if (active) {
      wireGestures();
      wireNav();
      highlightTarget();
    }
  } catch (err) {
    // Never leave content hidden because an animation could not start.
    root.classList.remove("has-motion");
    if (window.console) console.error(err);
  }
  if (!active) root.classList.remove("has-motion");
})();
