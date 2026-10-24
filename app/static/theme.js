/* Theme toggle. Light is the default; dark only when the person picks it.

   The choice is kept in localStorage ("pz-theme") and applied before first
   paint by the inline script in base.html, so page loads and cross-document
   view transitions never flash the other theme. Switching uses a same-document
   view transition: the new theme is revealed in a circle growing from the
   toggle. Without view transitions the colours cross-fade; with reduced motion
   the switch is instant. */
(function () {
  "use strict";

  var KEY = "pz-theme";
  var COLORS = { light: "#f7f7f4", dark: "#0a0a0a" };
  var root = document.documentElement;

  function current() {
    return root.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function reduced() {
    return !window.matchMedia || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function sync(btn, theme) {
    var dark = theme === "dark";
    btn.setAttribute("aria-pressed", dark ? "true" : "false");
    btn.setAttribute("title", dark ? "Switch to light theme" : "Switch to dark theme");
  }

  function apply(theme) {
    if (theme === "dark") root.setAttribute("data-theme", "dark");
    else root.removeAttribute("data-theme");
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", COLORS[theme]);
    var btns = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < btns.length; i++) sync(btns[i], theme);
  }

  function save(theme) {
    try { localStorage.setItem(KEY, theme); } catch (e) { /* storage blocked: this page only */ }
  }

  function toggle(btn) {
    var next = current() === "dark" ? "light" : "dark";
    save(next);

    if (reduced()) { apply(next); return; }

    if (!document.startViewTransition) {
      root.classList.add("theme-fade");
      apply(next);
      setTimeout(function () { root.classList.remove("theme-fade"); }, 320);
      return;
    }

    var r = btn.getBoundingClientRect();
    var x = r.left + r.width / 2;
    var y = r.top + r.height / 2;
    var radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));

    root.classList.add("theme-vt");
    var vt;
    try {
      vt = document.startViewTransition(function () { apply(next); });
    } catch (e) {
      root.classList.remove("theme-vt");
      apply(next);
      return;
    }
    vt.ready.then(function () {
      root.animate(
        { clipPath: ["circle(0px at " + x + "px " + y + "px)", "circle(" + radius + "px at " + x + "px " + y + "px)"] },
        { duration: 640, easing: "cubic-bezier(.65, 0, .35, 1)", pseudoElement: "::view-transition-new(root)" }
      );
    }).catch(function () { /* skipped: the theme is already applied */ });
    vt.finished.then(done, done);
    function done() { root.classList.remove("theme-vt"); }
  }

  function init() {
    var btns = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < btns.length; i++) {
      sync(btns[i], current());
      btns[i].addEventListener("click", function (ev) { toggle(ev.currentTarget); });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
