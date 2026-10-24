/* Policies & Audit page behaviour (/policies).

   - Hovering an audit entry lights the entry it chains to (its prev_hash is
     the next row's entry_hash) and the rail segment between them.
   - Event-type filters for the audit log.
   - Motion (only when PZMotion.active): entries stream in as they scroll into
     view, the version rail and replay bars draw themselves, and the integrity
     badge replays its verification: ring fills while the entry count ticks
     up and a pulse runs down the chain, then the check draws in.

   Everything is rendered complete by the server; this file only arms elements
   (class .armed) right before it animates them. */
(function () {
  "use strict";

  var page = document.querySelector(".pz-policies");
  if (!page) return;

  var PZ = window.PZMotion;
  var M = window.Motion;
  var motion = !!(PZ && PZ.active && M && M.inView && "IntersectionObserver" in window);
  var chain = page.querySelector("[data-chain]");
  var entries = chain ? Array.prototype.slice.call(chain.querySelectorAll("li.entry")) : [];
  var traces = chain ? Array.prototype.slice.call(chain.querySelectorAll("li.trace")) : [];

  // ------------------------------------------------------------ hash links
  var byHash = {};
  entries.forEach(function (li) { byHash[li.getAttribute("data-hash")] = li; });
  var hovered = null;
  var linked = null;

  function link(li) {
    if (li === hovered) return;
    if (hovered) hovered.classList.remove("is-linked-from");
    if (linked) linked.classList.remove("is-linked");
    hovered = li;
    linked = li ? byHash[li.getAttribute("data-prev")] || null : null;
    if (hovered && linked && !linked.hidden) {
      hovered.classList.add("is-linked-from");
      linked.classList.add("is-linked");
    } else {
      linked = null;
    }
  }

  if (chain) {
    chain.addEventListener("pointerover", function (event) {
      link(event.target.closest ? event.target.closest("li.entry") : null);
    });
    chain.addEventListener("pointerleave", function () { link(null); });
  }

  // --------------------------------------------------------------- filters
  var bar = page.querySelector(".audit-filters");
  if (bar && chain) {
    bar.addEventListener("click", function (event) {
      var btn = event.target.closest && event.target.closest("button[data-filter]");
      if (!btn || btn.classList.contains("is-active")) return;
      var filter = btn.getAttribute("data-filter");
      Array.prototype.forEach.call(bar.querySelectorAll("button[data-filter]"), function (b) {
        var on = b === btn;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      link(null);
      var shown = [];
      entries.forEach(function (li) {
        var show = filter === "all" || li.getAttribute("data-family") === filter;
        li.hidden = !show;
        if (show) shown.push(li);
      });
      traces.forEach(function (t) { t.hidden = !t.querySelector("li.entry:not([hidden])"); });
      if (motion) {
        shown.filter(function (li) { return li.classList.contains("is-in"); }).slice(0, 24)
          .forEach(function (li, i) {
            PZ.animate(li, { opacity: [0, 1], y: [6, 0] },
              { duration: 0.4, delay: i * 0.02, ease: PZ.ease.out });
          });
      }
    });
  }

  if (!motion) return;

  // ------------------------------------------------- streaming audit entries
  if (chain) {
    chain.classList.add("armed");
    var queue = [];
    var scheduled = false;
    var flush = function () {
      scheduled = false;
      var batch = queue.splice(0).sort(function (a, b) {
        return a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
      });
      PZ.enter(batch, "fade-right", { stagger: 0.03, maxDelay: 0.45, duration: 0.5 });
    };
    var io = new IntersectionObserver(function (seen) {
      seen.forEach(function (s) {
        if (!s.isIntersecting) return;
        io.unobserve(s.target);
        if (s.target.classList.contains("trace-head")) {
          s.target.parentNode.classList.add("is-in");
        } else {
          queue.push(s.target);
        }
      });
      if (queue.length && !scheduled) { scheduled = true; requestAnimationFrame(flush); }
    }, { rootMargin: "0px 0px -4% 0px" });
    entries.forEach(function (li) { io.observe(li); });
    traces.forEach(function (t) {
      var head = t.querySelector(".trace-head");
      if (head) io.observe(head);
    });
  }

  // ------------------------------------------- rail and replay bars draw in
  Array.prototype.forEach.call(page.querySelectorAll("[data-draw]"), function (el) {
    el.classList.add("armed");
    var stop = M.inView(el, function () {
      setTimeout(function () { el.classList.add("is-drawn"); }, 220);
      if (stop) stop();
    }, { amount: 0.5 });
  });

  // ------------------------------------------------- integrity verification
  var badge = page.querySelector("[data-integrity]");
  if (!badge) return;
  var count = badge.querySelector("[data-chain-count]");
  var state = badge.querySelector("[data-integrity-state]");
  var ok = badge.classList.contains("is-ok");
  var finalState = state ? state.textContent : "";
  badge.classList.add("armed");
  if (state) state.textContent = "verifying…";

  function pulseDownTheChain() {
    if (!chain) return;
    var rect = chain.getBoundingClientRect();
    var reach = Math.max(0, Math.min(rect.height, window.innerHeight - Math.max(rect.top, 0)));
    if (reach < 40) return;
    var scan = document.createElement("span");
    scan.className = "chain-scan";
    scan.setAttribute("aria-hidden", "true");
    scan.style.cssText = "position:absolute;left:calc(var(--rail-x) - 1px);top:0;width:3px;height:90px;" +
      "border-radius:3px;pointer-events:none;z-index:1;" +
      "background:linear-gradient(180deg,transparent," + (ok ? "var(--status-resolved)" : "var(--status-escalated)") + ",transparent);" +
      "filter:drop-shadow(0 0 6px " + (ok ? "var(--status-resolved)" : "var(--status-escalated)") + ");";
    chain.appendChild(scan);
    PZ.animate(scan, { y: [-90, reach], opacity: [0, 1, 1, 0] }, { duration: 1.15, ease: [0.65, 0, 0.35, 1] })
      .then(function () { scan.remove(); });
  }

  var stopBadge = M.inView(badge, function () {
    if (stopBadge) stopBadge();
    // Let the badge's own scale entrance land first.
    setTimeout(function () {
      badge.classList.add("is-verifying");
      if (count) PZ.countUp(count);
      pulseDownTheChain();
      setTimeout(function () {
        badge.classList.remove("is-verifying");
        badge.classList.add("is-verified");
        if (state) state.textContent = ok ? "verified just now" : finalState;
      }, 1200);
    }, 280);
  }, { amount: 0.6 });
})();
