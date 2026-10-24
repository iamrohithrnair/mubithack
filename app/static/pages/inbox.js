/* Exception inbox behaviour, on top of motion.js (window.PZMotion).

   1. Board cards enter lane by lane once the board scrolls into view.
   2. After a check-in sends the browser back here, the case that was just
      handled is announced above the board, marked "Just now", and pulsed in its
      status color. The case id is remembered at submit time in sessionStorage
      (the server answers a self-resolving check-in with plain "/").
   3. Coming back from a case's page pulses that case's card once.
   4. A soft pointer spotlight on dock rows and cards.

   Progressive: without JS, or under reduced motion, the page is complete and
   static. Any failure here reveals everything. */
(function () {
  "use strict";

  var KEY = "pz:inbox:checkin";
  var FRESH_MS = 2 * 60 * 1000;
  var Z = window.PZMotion;
  var M = window.Motion;
  var active = !!(Z && Z.active && M && M.inView);
  var board = document.querySelector(".ib-board");

  // ------------------------------------------- remember the check-in sent
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.matches || !form.matches("form[data-wait]")) return;
    try {
      sessionStorage.setItem(KEY, JSON.stringify({ id: form.getAttribute("data-wait"), t: Date.now() }));
    } catch (e) { /* storage blocked; the arrival notice is a nicety */ }
    var row = form.closest(".ib-dock-row");
    if (row) row.classList.add("is-sending");
  }, true);

  function takeCheckin() {
    var raw = null;
    try {
      raw = sessionStorage.getItem(KEY);
      sessionStorage.removeItem(KEY);
    } catch (e) { return null; }
    if (!raw) return null;
    var data;
    try { data = JSON.parse(raw); } catch (e) { return null; }
    if (!data || !data.id || Date.now() - data.t > FRESH_MS) return null;
    // Only right after the check-in: waiting.js reloads this page, and a plain
    // form post is redirected here from it. A later visit is not an arrival.
    var nav = performance.getEntriesByType && performance.getEntriesByType("navigation")[0];
    var reloaded = nav && nav.type === "reload";
    var fromHere = false;
    try { fromHere = new URL(document.referrer).pathname === "/"; } catch (e) { /* no referrer */ }
    return reloaded || fromHere ? data.id : null;
  }

  // ---------------------------------------------------------------- utils
  function statusColor(el, alpha) {
    var c = getComputedStyle(el).getPropertyValue("--c").trim();
    var m = /^#([0-9a-f]{6})$/i.exec(c);
    if (!m) return (Z && Z.tokenColor && Z.tokenColor("--accent-mark", alpha)) || "rgba(255, 223, 43, " + alpha + ")";
    var n = parseInt(m[1], 16);
    return "rgba(" + (n >> 16) + ", " + ((n >> 8) & 255) + ", " + (n & 255) + ", " + alpha + ")";
  }

  function pulse(card, times) {
    if (Z) Z.highlight(card, { color: statusColor(card, 0.6), times: times || 2 });
  }

  function whenVisible(el, fn) {
    if (!active) { fn(); return; }
    var stop = M.inView(el, function () { fn(); if (stop) stop(); }, { amount: 0.6 });
  }

  function el(tag, cls, html) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
    });
  }

  // ------------------------------------------------------- arrival notice
  var COPY = {
    auto: ["Resolved by policy", "matched the active policy and resolved itself. Nobody had to touch it."],
    resolved: ["Resolved", "was checked in and is resolved."],
    escalated: ["Escalated", "needs a person. The agent escalated it rather than act alone; the reasons are on its card."],
    waiting_decision: ["Waiting for decision", "is ready for your decision."],
    investigating: ["Investigating", "is still being investigated."],
    "new": ["Checked in", "was checked in."],
  };

  function announce(card) {
    var status = card.getAttribute("data-status");
    var key = status === "resolved" && card.hasAttribute("data-auto") ? "auto" : status;
    var copy = COPY[key] || COPY["new"];
    var id = card.getAttribute("data-case");

    var note = el("div", "ib-arrival status-" + status);
    note.setAttribute("role", "status");
    note.appendChild(el("span", "ib-arrival-mark"));
    note.appendChild(el("div", "ib-arrival-text",
      "<b>" + esc(copy[0]) + "</b><strong>" + esc(id) + "</strong> " + esc(copy[1]) +
      ' <a href="#case-' + esc(id) + '" class="ib-arrival-show">Show it</a>'));
    var close = el("button", "ib-arrival-close", "&times;");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss");
    note.appendChild(close);

    var anchor = document.querySelector(".ib-policy");
    if (!anchor) return;
    anchor.parentNode.insertBefore(note, anchor);

    if (active) {
      Z.animate(note, { opacity: [0, 1], y: [-10, 0], scale: [0.98, 1] },
        { type: "spring", stiffness: 380, damping: 30, delay: 0.35 });
    }

    note.querySelector(".ib-arrival-show").addEventListener("click", function (event) {
      event.preventDefault();
      card.scrollIntoView({ behavior: active ? "smooth" : "auto", block: "center" });
      setTimeout(function () { pulse(card, 1); }, active ? 450 : 0);
    });
    close.addEventListener("click", function () {
      if (!active) { note.remove(); return; }
      var h = note.offsetHeight;
      Z.animate(note, { opacity: 0, y: -6, scale: 0.98 }, { duration: 0.18, ease: [0.55, 0, 1, 0.45] })
        .then(function () {
          note.style.overflow = "hidden";
          return Z.animate(note, { height: [h + "px", "0px"], marginBottom: ["24px", "0px"], paddingTop: 0, paddingBottom: 0, borderWidth: 0 },
            { duration: 0.28, ease: Z.ease.out });
        })
        .then(function () { note.remove(); });
    });
  }

  function markFresh(card) {
    card.classList.add("is-fresh");
    var head = card.querySelector(".ib-card-head");
    var tag = el("span", "ib-fresh", "Just now");
    var pill = head && head.querySelector(".pill");
    if (head) head.insertBefore(tag, pill || null);
  }

  // ------------------------------------------------------- card entrances
  function enterCards(fresh) {
    if (!board) return;
    var lanes = board.querySelectorAll(".ib-lane");
    var others = [];
    Array.prototype.forEach.call(lanes, function (lane, i) {
      Array.prototype.forEach.call(lane.querySelectorAll(".ib-card"), function (card, j) {
        if (card === fresh) return;
        others.push(card);
        Z.enter([card], "fade-up", { delay: 0.22 + i * 0.06 + Math.min(j * 0.06, 0.4), duration: 0.6 });
      });
    });
    if (fresh) {
      // The handled case lands last, a touch bigger, and then rings.
      var delay = 0.55;
      Z.animate(fresh,
        { opacity: [0, 1], y: [-18, 0], scale: [0.92, 1], filter: ["blur(6px)", "blur(0px)"] },
        { duration: 0.75, ease: [0.34, 1.45, 0.5, 1], delay: delay })
        .then(function () {
          fresh.classList.add("is-in");
          fresh.style.opacity = ""; fresh.style.transform = ""; fresh.style.filter = "";
          pulse(fresh, 2);
        });
    }
  }

  // -------------------------------------------------------- the spotlight
  function wireSpotlight() {
    document.addEventListener("pointermove", function (event) {
      if (event.pointerType !== "mouse") return;
      var target = event.target.closest && event.target.closest(".ib-card, .ib-dock-row");
      if (!target) return;
      var r = target.getBoundingClientRect();
      target.style.setProperty("--mx", (event.clientX - r.left) + "px");
      target.style.setProperty("--my", (event.clientY - r.top) + "px");
    }, { passive: true });
  }

  // ------------------------------------------------------------------ run
  try {
    wireSpotlight();

    var freshId = takeCheckin();
    var fresh = freshId && board && board.querySelector('.ib-card[data-case="' + CSS.escape(freshId) + '"]');
    if (fresh) {
      markFresh(fresh);
      announce(fresh);
    }

    // Back from a case page: pulse the card that was open.
    var back = null;
    try {
      var ref = new URL(document.referrer);
      if (ref.origin === location.origin && /^\/exceptions\//.test(ref.pathname) && board) {
        back = board.querySelector('.ib-card[href="' + CSS.escape(ref.pathname) + '"]');
      }
    } catch (e) { /* no referrer */ }

    if (active && board) {
      // Cards wait (hidden) inside lanes that are themselves still hidden by
      // data-reveal; whatever happens, the gate lifts once the board is seen.
      board.classList.add("ib-cards-pending");
      var stop = M.inView(board, function () {
        if (stop) stop();
        setTimeout(function () { board.classList.remove("ib-cards-pending"); }, 2600);
        enterCards(fresh);
      }, { amount: 0.15 });
    }

    if (back && back !== fresh) {
      whenVisible(back, function () { setTimeout(function () { pulse(back, 1); }, 900); });
    }
  } catch (err) {
    if (board) board.classList.remove("ib-cards-pending");
    if (window.console) console.error(err);
  }
})();
