/* Dashboard motion and interaction, on top of window.Motion and PZMotion.

   - Bars, the hero arc, the outcome stack and the tile meters grow in with
     springs (staggered), the column chart rises when it scrolls into view.
   - The range tabs keep a sliding indicator. Choosing a range fetches the
     page for that range and swaps #dash-body in place: numbers count from
     their old value to the new one, bars and the arc spring between widths,
     and the chart regrows. Without fetch or with reduced motion the form
     submits normally.
   - One shared tooltip, anchored to the exact mark (bar end, column top,
     segment), for pointer hover and keyboard focus. Native titles are
     removed only once it is wired, so they stay as the no-JS fallback.

   Everything visible is server-rendered text; this file only animates it. */
(function () {
  "use strict";

  var root = document.documentElement;
  var M = window.Motion;
  var PZ = window.PZMotion;
  var active = !!(PZ && PZ.active && M && M.animate);
  window.PZDash = true;

  var dash = document.querySelector(".dash");
  var body = document.getElementById("dash-body");
  if (!dash || !body) { root.classList.remove("dash-pending"); return; }

  var SPRING_BAR = { type: "spring", stiffness: 170, damping: 22, mass: 1 };
  var SPRING_COL = { type: "spring", stiffness: 240, damping: 20, mass: 1 };
  var SPRING_ARC = { type: "spring", stiffness: 60, damping: 17, mass: 1 };
  var SNAPPY = (PZ && PZ.springs.snappy) || { type: "spring", stiffness: 520, damping: 38 };
  var GROW = ".hbar, .mix-part, .meter i";

  function all(sel, scope) { return Array.prototype.slice.call((scope || document).querySelectorAll(sel)); }
  function pVar(el, name) { return parseFloat(el.style.getPropertyValue(name || "--p")) || 0; }
  function numberIn(el) {
    var m = /-?\d[\d,]*(?:\.\d+)?/.exec(el.textContent || "");
    return m ? parseFloat(m[0].replace(/,/g, "")) : null;
  }
  function settleStyle(el, prop) { el.style[prop] = ""; }

  // ------------------------------------------------------------ chart fit
  /* The chart is an SVG scaled to its card; --k keeps its labels at a fixed
     on-screen size whatever the width. */
  function fitCharts(scope) {
    all(".column-chart svg", scope || body).forEach(function (svg) {
      var w = svg.getBoundingClientRect().width;
      var vb = svg.viewBox && svg.viewBox.baseVal;
      if (w && vb && vb.width) svg.style.setProperty("--k", (vb.width / w).toFixed(3));
    });
  }
  if (window.ResizeObserver) {
    new ResizeObserver(function () { fitCharts(); placeIndicator(false); }).observe(dash);
  } else {
    window.addEventListener("resize", function () { fitCharts(); placeIndicator(false); });
  }

  // -------------------------------------------------------------- growth
  /* Set the starting state of everything that grows, so nothing flashes at
     full size before its entrance. `from` maps data-k to old values. */
  function prime(scope, from) {
    if (!active) return;
    from = from || {};
    all(GROW, scope).forEach(function (el) {
      var old = from[el.getAttribute("data-k")];
      el.style.width = (old ? old.p : 0) + "%";
    });
    all(".ring-arc, .ring-glow", scope).forEach(function (el) {
      var old = from.ring;
      el.style.strokeDashoffset = String(old ? old.off : 100);
    });
    all(".column-chart .mark", scope).forEach(function (el) { el.style.transform = "scaleY(0)"; });
  }

  function grow(els, opts) {
    opts = opts || {};
    return Promise.all(els.map(function (el, i) {
      var to = pVar(el) + "%";
      var delay = (opts.delay || 0) + i * (opts.stagger == null ? 0.06 : opts.stagger);
      return M.animate(el, { width: to }, Object.assign({}, opts.spring || SPRING_BAR, { delay: delay }))
        .then(function () { settleStyle(el, "width"); });
    }));
  }

  function drawRing(scope, delay) {
    all(".ring-arc, .ring-glow", scope).forEach(function (el) {
      var to = pVar(el, "--off");
      M.animate(el, { strokeDashoffset: to }, Object.assign({}, SPRING_ARC, { delay: delay || 0 }))
        .then(function () { settleStyle(el, "strokeDashoffset"); });
    });
  }

  function raiseColumns(scope, delay, step) {
    var marks = all(".column-chart .mark", scope);
    marks.forEach(function (el, i) {
      M.animate(el, { transform: ["scaleY(0)", "scaleY(1)"] },
        Object.assign({}, SPRING_COL, { delay: (delay || 0) + Math.min(i * (step || 0.035), 0.5) }))
        .then(function () { settleStyle(el, "transform"); });
    });
    all(".column-chart .grid, .column-chart .axis", scope).forEach(function (el, i) {
      M.animate(el, { opacity: [0, 1] }, { duration: 0.6, delay: (delay || 0) + i * 0.08 });
    });
  }

  function whenSeen(el, fn) {
    if (!el) return;
    var stop = M.inView(el, function () { fn(); if (stop) stop(); }, { amount: 0.25 });
  }

  function intro() {
    prime(body);
    root.classList.remove("dash-pending");
    if (!active) return;
    // The hero lands first (motion.js fades it in), then its arc and stack.
    drawRing(body, 0.25);
    grow(all(".mix-part", body), { delay: 0.35, stagger: 0.08 });
    grow(all(".meter i", body), { delay: 0.55, stagger: 0.05 });
    all(".hbars, .steps", body).forEach(function (list) {
      whenSeen(list, function () { grow(all(".hbar", list), { delay: 0.12, stagger: 0.07 }); });
    });
    all(".column-chart", body).forEach(function (fig) {
      whenSeen(fig, function () { raiseColumns(fig, 0.15); });
    });
  }

  // ------------------------------------------------------------- tooltip
  var tip = document.createElement("div");
  tip.className = "dash-tip";
  tip.setAttribute("aria-hidden", "true");
  tip.innerHTML = '<span class="tip-title"></span><span class="tip-value"></span>';
  document.body.appendChild(tip);
  var tipTitle = tip.firstChild;
  var tipValue = tip.lastChild;
  var tipFor = null;
  var tipShown = false;
  var hideTimer = 0;

  function wireTips(scope) {
    all("[data-tip]", scope).forEach(function (el) {
      el.removeAttribute("title");
      var t = el.querySelector(":scope > title");
      if (t) t.parentNode.removeChild(t);
    });
  }

  /* The point the tooltip hangs from: the end of a bar, the top of a
     column or segment, the top of the ring. */
  function anchorOf(el) {
    var bar = el.querySelector(".hbar");
    if (bar) {
      var track = bar.parentElement.getBoundingClientRect();
      var b = bar.getBoundingClientRect();
      return { x: b.width > 1 ? b.right : track.left, y: track.top };
    }
    var mark = el.querySelector(".mark");
    var r = (mark || el.querySelector(".hit") || el).getBoundingClientRect();
    return { x: r.left + r.width / 2, y: mark ? r.top : r.top + r.height / 2 };
  }

  function showTip(el) {
    clearTimeout(hideTimer);
    tipTitle.textContent = el.getAttribute("data-tip") || "";
    tipValue.textContent = el.getAttribute("data-tip-value") || "";
    var a = anchorOf(el);
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var x = Math.max(12, Math.min(window.innerWidth - w - 12, a.x - w / 2));
    var y = a.y - h - 10;
    if (y < 70) y = a.y + 18;
    tip.classList.add("is-on");
    if (!active) {
      tip.style.transform = "translate(" + x + "px," + y + "px)";
      tip.style.opacity = "1";
    } else if (!tipShown) {
      M.animate(tip, { x: x, y: y }, { duration: 0 });
      M.animate(tip, { opacity: [0, 1], scale: [0.94, 1] }, { duration: 0.18, ease: [0.22, 1, 0.36, 1] });
    } else if (tipFor !== el) {
      M.animate(tip, { x: x, y: y }, SNAPPY);
    }
    tipFor = el;
    tipShown = true;
  }

  function hideTip() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () {
      if (!tipShown) return;
      tipShown = false;
      tipFor = null;
      if (!active) { tip.style.opacity = "0"; tip.classList.remove("is-on"); return; }
      M.animate(tip, { opacity: 0, scale: 0.97 }, { duration: 0.12 }).then(function () {
        if (!tipShown) tip.classList.remove("is-on");
      });
    }, 90);
  }

  function tipTarget(node) {
    var el = node && node.closest && node.closest("[data-tip]");
    return el && dash.contains(el) ? el : null;
  }
  dash.addEventListener("pointerover", function (e) {
    var el = tipTarget(e.target);
    if (el) showTip(el); else hideTip();
  });
  dash.addEventListener("pointerleave", hideTip);
  dash.addEventListener("focusin", function (e) {
    var el = tipTarget(e.target);
    if (el) showTip(el);
  });
  dash.addEventListener("focusout", hideTip);
  window.addEventListener("scroll", function () { if (tipShown) { tipShown = false; tipFor = null; tip.classList.remove("is-on"); tip.style.opacity = "0"; } }, { passive: true });

  // ---------------------------------------------------------- range tabs
  var form = dash.querySelector("form[data-range]");
  var seg = form && form.querySelector(".seg");
  var indicator = seg && seg.querySelector(".seg-indicator");

  function selectedBtn() { return seg && seg.querySelector(".seg-btn.selected"); }

  function placeIndicator(animate) {
    var btn = selectedBtn();
    if (!indicator || !btn) return;
    var to = { x: btn.offsetLeft, width: btn.offsetWidth + "px" };
    if (active) M.animate(indicator, to, animate ? SNAPPY : { duration: 0 });
    else { indicator.style.transform = "translateX(" + to.x + "px)"; indicator.style.width = to.width; }
  }

  function select(key) {
    all(".seg-btn", seg).forEach(function (b) {
      var on = b.value === key;
      b.classList.toggle("selected", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    placeIndicator(true);
    var btn = selectedBtn();
    if (btn && seg.scrollWidth > seg.clientWidth) {
      seg.scrollTo({ left: btn.offsetLeft - (seg.clientWidth - btn.offsetWidth) / 2, behavior: active ? "smooth" : "auto" });
    }
  }

  if (seg) {
    placeIndicator(false);
    seg.classList.add("is-ready");
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { placeIndicator(false); });
  }

  function snapshot(scope) {
    var map = {};
    all("[data-k]", scope).forEach(function (el) {
      map[el.getAttribute("data-k")] = { p: pVar(el), off: pVar(el, "--off"), n: numberIn(el) };
    });
    return map;
  }

  var loading = 0;
  function load(key, push) {
    if (key === body.getAttribute("data-range-key")) return;
    var ticket = ++loading;
    var url = "/dashboard?range=" + encodeURIComponent(key);
    select(key);
    body.setAttribute("aria-busy", "true");
    hideTip();
    fetch(url, { headers: { "Accept": "text/html" }, credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) {
        if (ticket !== loading) return;
        var doc = new DOMParser().parseFromString(html, "text/html");
        var fresh = doc.getElementById("dash-body");
        if (!fresh) throw new Error("no dashboard body");
        if (push) history.pushState({ range: key }, "", url);
        swap(fresh, key);
        var status = document.getElementById("dash-status");
        var next = doc.getElementById("dash-status");
        if (status && next) status.textContent = next.textContent;
      })
      .catch(function () { window.location.href = url; });
  }

  function swap(fresh, key) {
    var old = snapshot(body);
    // Entrances already happened; the swapped content morphs instead.
    all("[data-animate], [data-reveal], [data-stagger]", fresh).forEach(function (el) {
      el.removeAttribute("data-animate");
      el.removeAttribute("data-reveal");
      el.removeAttribute("data-stagger");
    });
    body.innerHTML = fresh.innerHTML;
    body.setAttribute("data-range-key", key);
    wireTips(body);
    fitCharts(body);
    prime(body, old);
    body.removeAttribute("aria-busy");

    all("[data-countup]", body).forEach(function (el) {
      var was = old[el.getAttribute("data-k")];
      var now = numberIn(el);
      if (now == null) return;
      var from = was && was.n != null ? was.n : 0;
      if (from === now) return;
      el.setAttribute("data-countup-from", String(from));
      el.setAttribute("data-countup", "0.9");
      PZ.countUp(el);
    });
    grow(all(GROW, body), { stagger: 0.025 });
    drawRing(body, 0);
    raiseColumns(body, 0.05, 0.02);
    PZ.enter(all(".recent tbody tr, ol.timeline > li", body).slice(0, 24), "fade-up", { stagger: 0.025 });
  }

  if (form && active && window.fetch && window.DOMParser && window.history && history.pushState) {
    form.addEventListener("submit", function (e) {
      var btn = e.submitter;
      if (!btn || !btn.value) return;
      e.preventDefault();
      load(btn.value, true);
    });
    history.replaceState({ range: body.getAttribute("data-range-key") }, "", window.location.href);
    window.addEventListener("popstate", function () {
      var key = new URLSearchParams(window.location.search).get("range") || "all";
      var known = all(".seg-btn", seg).some(function (b) { return b.value === key; });
      load(known ? key : "all", false);
    });
  }

  // ---------------------------------------------------------------- go
  try {
    wireTips(body);
    fitCharts(body);
    intro();
  } catch (err) {
    root.classList.remove("dash-pending");
    all(GROW + ", .ring-arc, .ring-glow, .column-chart .mark", body).forEach(function (el) {
      el.style.width = ""; el.style.strokeDashoffset = ""; el.style.transform = "";
    });
    if (window.console) console.error(err);
  }
})();
