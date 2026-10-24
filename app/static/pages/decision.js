/* Decision page choreography, on top of motion.js (window.PZMotion) and the
   vendored Motion global. Everything here is additive: the page is complete
   without it, and under reduced motion each step settles immediately.

   - the case ID enters letter by letter
   - the Banyan Sapling bars grow to their probabilities, the threshold marker
     drops in, and on a blocked verdict the note that caused it is pulsed
   - the timeline rail draws down as its events arrive
   - the replay coverage meter fills, and a passing replay's Activate button
     gets one accent pulse: it is the only call to action left */
(function () {
  "use strict";

  var PZ = window.PZMotion;
  // Pulse colours follow the theme (tokens are read when the pulse runs).
  function tone(name, alpha) { return (PZ && PZ.tokenColor && PZ.tokenColor(name, alpha)) || undefined; }
  var M = window.Motion;
  var page = document.querySelector(".dz");
  if (!page || !PZ) return;
  var active = PZ.active && M && M.inView;

  function once(el, fn, amount) {
    if (!active) { fn(); return; }
    var stop = M.inView(el, function () { fn(); if (stop) stop(); }, { amount: amount || 0.25 });
  }

  // ------------------------------------------------------------ case ID
  var title = page.querySelector(".dz-case");
  if (title) {
    if (active) {
      var text = title.textContent.trim();
      title.textContent = "";
      var chars = text.split("").map(function (ch) {
        var s = document.createElement("span");
        s.className = "dz-ch";
        s.setAttribute("aria-hidden", "true");
        s.textContent = ch;
        s.style.opacity = "0";
        title.appendChild(s);
        return s;
      });
      title.classList.add("is-split");
      chars.forEach(function (s, i) {
        M.animate(s, { opacity: [0, 1], y: ["0.35em", "0em"], filter: ["blur(10px)", "blur(0px)"] },
          { duration: 0.7, ease: PZ.ease.out, delay: 0.05 + i * 0.035 })
          .then(function () { s.style.filter = ""; s.style.transform = ""; });
      });
    } else {
      title.classList.add("is-split");
    }
  }

  var glow = page.querySelector(".dz-hero-glow");
  if (glow && active) M.animate(glow, { opacity: [0, 0.9], scale: [0.8, 1] }, { duration: 1.6, ease: PZ.ease.out });

  // ---------------------------------------------------- Banyan Sapling
  var sapling = page.querySelector(".dz-sapling");
  if (sapling) {
    var fills = Array.prototype.slice.call(sapling.querySelectorAll(".dz-bar-fill"));
    var marker = sapling.querySelector(".dz-threshold");
    if (!active) {
      sapling.classList.add("is-measured");
    } else {
      fills.forEach(function (f) { f.style.transform = "scaleX(0)"; });
      if (marker) marker.style.opacity = "0";
      sapling.classList.add("is-measured");
      once(sapling.querySelector(".dz-bars") || sapling, function () {
        var grown = fills.map(function (f, i) {
          return M.animate(f, { scaleX: [0, 1] }, { type: "spring", stiffness: 120, damping: 20, delay: 0.15 + i * 0.12 });
        });
        if (marker) {
          M.animate(marker, { opacity: [0, 1], scaleY: [2.2, 1] }, { duration: 0.5, ease: PZ.ease.out, delay: 0.9 });
        }
        var verdict = sapling.querySelector("[data-verdict]");
        Promise.all(grown).then(function () {
          if (verdict) {
            PZ.highlight(verdict, {
              color: sapling.classList.contains("is-blocked") ? tone("--status-escalated", 0.45) : tone("--semantic", 0.5),
              times: 1,
            });
          }
          if (sapling.classList.contains("is-blocked")) {
            var quote = sapling.querySelector(".dz-note-quote");
            if (quote) setTimeout(function () { PZ.highlight(quote, { color: tone("--semantic", 0.55), times: 1 }); }, 350);
          }
        });
      }, 0.3);
    }
  }

  // ------------------------------------------------------------ outcome
  var escalated = page.querySelector(".dz-escalated");
  if (escalated && active) {
    setTimeout(function () { PZ.highlight(escalated, { color: tone("--status-escalated", 0.35), times: 1 }); }, 700);
  }

  // ------------------------------------------------------------- replay
  var cov = page.querySelector(".dz-cov span");
  if (cov && active) {
    cov.style.transform = "scaleX(0)";
    once(cov.parentNode, function () {
      M.animate(cov, { scaleX: [0, 1] }, { duration: 1.2, ease: PZ.ease.out, delay: 0.3 });
    }, 0.5);
  }

  var activate = page.querySelector(".dz-activate .btn.primary");
  if (activate && active) {
    once(activate, function () {
      setTimeout(function () { PZ.highlight(activate, { times: 1 }); }, 900);
    }, 0.8);
  }

  // ----------------------------------------------------------- timeline
  var rail = page.querySelector("ol.timeline");
  if (rail) {
    if (!active) {
      rail.classList.add("is-drawn");
    } else {
      rail.setAttribute("data-reveal", "fade-left");
      rail.setAttribute("data-stagger", "fade-left");
      rail.setAttribute("data-stagger-step", "0.035");
      PZ.scan(rail.parentNode);
      once(rail, function () { rail.classList.add("is-drawn"); }, 0.05);
    }
  }
})();
