/* Live board — deliverable 1.3: header counters, most-recent highlight, ×N
   repeats, reset button — plus the stage-2 seat map (?view=seats). Polling once
   a second, not SSE (plan D4: NPM buffers SSE silently; polling self-heals).

   Rows and seat squares arrive from the server already in their correct state,
   so a JS-less load still tells the truth. This script only keeps them current,
   applies the server's per-seat verdict (data.seats), and turns the view links
   into an in-place switch.

   Ordering rule, mirrored from server_v1.board_view(): most recent scan pinned
   first, everything else by data-index. Keep the two in step, or the first poll
   will visibly reshuffle the board. */
(function () {
  "use strict";

  var tbody = document.querySelector(".board tbody");
  if (!tbody) return;

  var resetBtn = document.getElementById("reset");
  var token = resetBtn ? resetBtn.dataset.token : "";
  var hint = document.querySelector(".hint");
  var clock = document.getElementById("clock");
  var HKT = "Asia/Hong_Kong";

  var rows = {};
  Array.prototype.forEach.call(tbody.querySelectorAll("tr[data-code]"), function (tr) {
    rows[tr.dataset.code] = {
      tr: tr,
      hits: tr.querySelector(".cell-state .hits"),
      age: tr.querySelector(".cell-state .age"),
      bag: tr.querySelector(".cell-bag"),
      index: parseInt(tr.dataset.index, 10)
    };
  });

  function stateOf(el) {
    if (el.classList.contains("recent")) return "recent";
    if (el.classList.contains("scanned")) return "scanned";
    return "waiting";
  }

  /* Seat squares, keyed by seat id. The server sends the whole cabin state every
     poll (data.seats), so we apply its verdict instead of re-deriving the rule. */
  var seatCells = {};
  Array.prototype.forEach.call(document.querySelectorAll(".seat[data-seat]"), function (el) {
    seatCells[el.dataset.seat] = { el: el, state: stateOf(el) };
  });

  /* Overhead compartments, keyed by the ANCHOR ROW and the wall. That is the key
     `/api/state` sends in `bins`, so the join works and the poll can repaint the
     rectangle: the server sends the load, its level and its tooltip, and we apply
     them instead of re-deriving a threshold — the same contract as the seat
     squares above. */
  var binCells = {};
  Array.prototype.forEach.call(document.querySelectorAll(".cabin-bin[data-bin]"), function (el) {
    binCells[el.dataset.bin + "|" + el.dataset.side] = el;
  });

  /* The seatmap's accessible name is written by the server too (data.cabin). It
     describes the same compartments we just repainted, so it has to be refreshed
     with them: left alone, a reset re-dims the cabin while the words still claim
     that several bins are full. There is no legend under the cabin any more, so
     the name is the only place those counts are read out. */
  var cabinMap = document.querySelector(".seatmap");

  /* The boarding log, keyed by the scan row it came from. The server sends the
     sentence and the order; this only prints what is not on the page yet. */
  var logPanel = document.querySelector(".log");
  var logLines = logPanel ? logPanel.querySelector(".log-lines") : null;
  var logItems = {};
  if (logLines) {
    // the lines the server already printed, each keyed to the scan row it is
    Array.prototype.forEach.call(logLines.querySelectorAll("li[data-key]"), function (li) {
      logItems[li.dataset.key] = li;
    });
  }

  function fillLine(li, entry) {
    var seat = document.createElement("b");
    seat.className = "log-seat";
    seat.textContent = entry.seat;
    li.textContent = "";                 // the seat and the note are rebuilt together
    li.appendChild(seat);
    li.appendChild(document.createTextNode(" " + entry.note));
  }

  /* Newest last, appended at the bottom and scrolled to, so the column reads like
     a chat: the oldest line leaves the top of the window, the newest one floats up
     into the place the eye already is. Keys — not text — decide what is new, so a
     one-second poll never re-animates the lines it has already shown; a line the
     server revised is edited in place; a line that left the payload (a checkout,
     a reset, or the oldest falling off the log's cap) is removed. */
  var logFirst = true;
  var logSmooth = window.matchMedia("(prefers-reduced-motion: no-preference)").matches;

  function renderLog(entries) {
    if (!logLines) return;
    var keep = {};
    var added = false;
    entries.forEach(function (entry) {
      keep[entry.key] = true;
      var line = logItems[entry.key];
      if (!line) {
        line = document.createElement("li");
        line.className = "log-line";
        line.dataset.key = entry.key;
        fillLine(line, entry);
        logItems[entry.key] = line;
        logLines.appendChild(line);
        added = true;
      } else if (line.textContent !== entry.seat + " " + entry.note) {
        fillLine(line, entry);
      }
    });
    Object.keys(logItems).forEach(function (key) {
      if (keep[key]) return;
      var line = logItems[key];
      if (line.parentNode) line.parentNode.removeChild(line);
      delete logItems[key];
    });
    if (logPanel) logPanel.classList.toggle("is-empty", entries.length === 0);
    // A feed is read at its newest line, so the window is pinned to the bottom on
    // every poll — including the first paint, where the lines came down with the
    // page and nothing was "added" by this script. /api/state only revises the log
    // when somebody boards, so this does not fight a reader scrolling back.
    //
    // The first paint jumps; after that a boarding floats up, which is the whole
    // gesture of the element — the column drifts to the new line instead of
    // snapping at it. Reduced motion gets the jump.
    if (added && !logFirst && logSmooth) {
      logLines.scrollTo({ top: logLines.scrollHeight, behavior: "smooth" });
    } else {
      logLines.scrollTop = logLines.scrollHeight;
    }
    logFirst = false;
  }

  /* The view switch is a real pair of links; with JS we flip the body class in
     place and keep the URL shareable, without one we never get here and the
     browser does a normal navigation. */
  var viewLinks = document.querySelectorAll(".views a");

  function setView(name) {
    if (name !== "list" && name !== "seats") return;
    document.body.classList.remove("view-list", "view-seats");
    document.body.classList.add("view-" + name);
    Array.prototype.forEach.call(viewLinks, function (a) {
      if (a.dataset.view === name) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    var big = document.body.classList.contains("big") ? "&big=1" : "";
    history.replaceState(null, "", "?view=" + name + big);
  }

  Array.prototype.forEach.call(viewLinks, function (a) {
    a.addEventListener("click", function (e) {
      // a modified or middle click means "new tab", not "switch view"
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      e.preventDefault();
      setView(a.dataset.view);
    });
  });

  function hkt(moment) {
    try {
      return moment.toLocaleTimeString("en-GB", { timeZone: HKT, hour12: false });
    } catch (e) {
      return moment.toISOString().slice(11, 19);
    }
  }

  /* relative under a minute, HKT clock after — same rule as server_v1.age_text */
  function ageText(iso, now) {
    var seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
    return seconds < 60 ? seconds + "s ago" : hkt(new Date(iso));
  }

  function setText(el, text) {
    if (el && el.textContent !== text) el.textContent = text;
  }

  function mostRecent(passengers) {
    var recent = null;
    passengers.forEach(function (p) {
      if (!p.scanned || !p.last_seen) return;
      if (!recent || Date.parse(p.last_seen) > Date.parse(recent.last_seen)) recent = p;
    });
    return recent;
  }

  function render(data) {
    var now = Date.now();
    var recent = mostRecent(data.passengers);
    var recentCode = recent ? recent.code : null;

    var order = data.passengers.slice().sort(function (a, b) {
      var ra = a.code === recentCode ? 0 : 1;
      var rb = b.code === recentCode ? 0 : 1;
      if (ra !== rb) return ra - rb;
      return rows[a.code].index - rows[b.code].index;
    });

    // move only what is out of place, so the board does not flicker each second
    order.forEach(function (p, i) {
      var row = rows[p.code];
      if (row && tbody.children[i] !== row.tr) {
        tbody.insertBefore(row.tr, tbody.children[i] || null);
      }
    });

    data.passengers.forEach(function (p) {
      var row = rows[p.code];
      if (!row) return;
      row.tr.classList.toggle("scanned", p.scanned);
      row.tr.classList.toggle("recent", p.code === recentCode);
      setText(row.hits, p.hits > 1 ? "×" + p.hits : "");
      setText(row.age, p.scanned && p.last_seen ? ageText(p.last_seen, now) : "—");
      // the server decides what the Baggage column reads; we only mirror it
      setText(row.bag, p.baggage_text);
    });

    // the cabin: apply the server's per-seat verdict, and pulse a square the
    // moment it lights. data.seats is always present — /api/state builds it.
    var cells = data.seats && data.seats.cells ? data.seats.cells : [];
    cells.forEach(function (cell) {
      var seat = seatCells[cell.seat];
      if (!seat || seat.state === cell.state) return;
      seat.el.classList.remove("waiting", "scanned", "recent");
      seat.el.classList.add(cell.state);
      // The gutter and the header label every square, so the square itself never
      // prints its seat id: `1A` on a lit cell read as text over the fill at board
      // size and was illegible at card size. The fill is the whole mark; identity
      // stays on `data-seat`/`title` (see seat_cell_html()).
      seat.el.textContent = "";
      if (cell.state !== "waiting") {
        seat.el.classList.remove("just-scanned");
        void seat.el.offsetWidth;            // restart the keyframes
        seat.el.classList.add("just-scanned");
        window.setTimeout(function () { seat.el.classList.remove("just-scanned"); }, 1000);
      }
      seat.state = cell.state;
    });

    var bins = data.bins || [];
    bins.forEach(function (b) {
      var el = binCells[b.bin + "|" + b.side];
      if (!el) return;
      if (el.dataset.level !== b.level) {
        el.classList.remove("load-green", "load-yellow", "load-red");
        el.classList.add("load-" + b.level);
        el.dataset.level = b.level;
      }
      el.classList.toggle("is-over", !!b.over);
      el.dataset.load = String(b.units);
      el.dataset.bags = String(b.bags);
      // the copy lives on the server, like baggage_text: one definition, mirrored
      el.title = b.title;
    });

    var cabin = data.cabin;
    if (cabin && cabinMap && cabinMap.getAttribute("aria-label") !== cabin.label) {
      cabinMap.setAttribute("aria-label", cabin.label);
    }

    renderLog(data.log || []);

    if (hint) {
      setText(hint, "Live, updating every second.");
      hint.dataset.state = "live";
    }
  }

  function tick() {
    if (document.hidden) return Promise.resolve();
    return fetch("/bggo/api/state", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(render)
      .catch(function () {
        if (hint) {
          hint.textContent = "Can't reach the gate. Still showing the last board we saw.";
          hint.dataset.state = "down";
        }
      });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      if (!confirm("Clear every scan? Bags, bin loads and stow advice go back to " +
                   "nothing. Passengers and tickets stay.")) return;
      resetBtn.disabled = true;
      fetch("/bggo/api/reset", {
        method: "POST",
        headers: { "X-Board-Token": token },
        cache: "no-store"
      })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return tick();
        })
        .catch(function (e) {
          if (hint) {
            hint.textContent = "Reset failed (" + e.message + "). The board is unchanged.";
            hint.dataset.state = "down";
          }
        })
        .then(function () {
          resetBtn.disabled = false;
        });
    });
  }

  if (clock) {
    var setClock = function () {
      clock.textContent = hkt(new Date()) + " HKT";
    };
    setClock();
    setInterval(setClock, 1000);
  }

  tick();
  setInterval(tick, 1000);
})();
