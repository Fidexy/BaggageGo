/* Audience page. The card is already on screen (server-rendered). This script's
   job is the CHOICE: how many of each bag, then Confirm — and Confirm is the
   check-in.

   The scan is recorded only on confirm, so a JS-less GET — a link preview, a
   crawl — can never inflate the board. Everything the server can do without JS,
   it does: the panel is a real form of numbered fields, so with JS off you type
   the counts and post them. With JS the steppers drive those same fields and we
   stay on the page, because the venue wifi is a single point of failure and a
   second round trip is one it can lose. */
(function () {
  "use strict";

  var script = document.currentScript;
  var code = script && script.dataset ? script.dataset.code : null;
  var status = document.getElementById("status");
  if (!code || !status) return;

  var picker = document.getElementById("picker");
  var form = document.getElementById("picker-form");
  var pickerError = document.getElementById("picker-error");
  var card = document.querySelector(".card");
  var fact = document.querySelector(".bag-fact");
  var totalEl = document.getElementById("total");
  var confirmBtn = document.getElementById("confirm");
  var tiles = picker ? picker.querySelectorAll(".bag") : [];

  /* The scenario's other ceiling: however the pieces are spread across the four
     bags, the mix is at most `data-max-items` items. The server enforces it; this
     only stops the panel offering a press (or accepting a typed number) it would
     refuse, and it reads the number off the page rather than repeating it. */
  var maxItems = picker ? parseInt(picker.dataset.maxItems, 10) : NaN;
  if (!(maxItems > 0)) maxItems = Infinity;

  /* The stowage block. The server writes its copy (so it is the same sentence
     whoever prints it); recording a bag changes the load in this passenger's own
     compartment, so the advice is re-painted from the scan response. */
  var stow = document.getElementById("stow");
  var stowLabel = document.getElementById("stow-label");
  var stowBin = document.getElementById("stow-bin");
  var stowLevel = document.getElementById("stow-level");
  var stowNote = document.getElementById("stow-note");

  function showStow(advice) {
    if (!stow || !advice) return;
    stow.dataset.state = advice.state;
    stow.dataset.level = advice.level || "none";
    if (stowLabel) stowLabel.textContent = advice.label;
    if (stowBin) stowBin.textContent = advice.bin;
    if (stowLevel) {
      stowLevel.textContent = advice.level_text;
      stowLevel.hidden = !advice.level;
    }
    if (stowNote) stowNote.textContent = advice.note;
  }

  /* The cabin on the card is the board's own drawing, so a zone is found the way
     `board.js` finds it: by what the state says about it. A check-in can move the
     advised zone (your own one fills up) and always changes its load, so this
     re-points the dashed ring and re-colours that one rectangle from the
     response. No threshold is re-derived here: the level arrives spelled out
     ("green"/"yellow"/"red") and that word is the whole of the colour. */
  var cabinMap = document.getElementById("cabin-map");

  function showCabin(advice, label) {
    if (!cabinMap || !advice) return;
    if (label) cabinMap.setAttribute("aria-label", label);
    var zones = cabinMap.querySelectorAll(".cabin-bin");
    var target = null;
    for (var i = 0; i < zones.length; i++) {
      var zone = zones[i];
      var mine = !!advice.level && zone.dataset.name === advice.bin &&
                 zone.dataset.side === advice.side;
      zone.classList.toggle("is-advised", mine);
      if (mine) target = zone;
    }
    if (!target) return;
    target.dataset.load = advice.units;
    target.dataset.level = advice.level;
    ["green", "yellow", "red"].forEach(function (level) {
      target.classList.toggle("load-" + level, level === advice.level);
    });
  }

  function say(text, state) {
    status.textContent = text;
    if (state) status.dataset.state = state;
  }

  function complain(text) {
    if (!pickerError) return;
    pickerError.textContent = text;
    pickerError.hidden = false;
  }

  /* show/hide the overlay and keep the card behind it out of the tab order.
     Nothing is focused on open: this overlay only ever appears unprompted (the
     card is rendered with it when no bag is on file), and a ring on first paint
     reads as "already chosen". */
  function open(show) {
    if (!picker) return;
    picker.hidden = !show;
    if (card) card.inert = show;
  }

  /* ---- the steppers: the counts live in the inputs, the total is derived ---- */

  function counts() {
    var out = {};
    Array.prototype.forEach.call(tiles, function (tile) {
      var input = tile.querySelector(".qty");
      var qty = parseInt(input.value, 10);
      if (isNaN(qty) || qty < 0) qty = 0;
      out[input.name] = qty;
    });
    return out;
  }

  function itemCount(countsObj, exceptKey) {
    return Object.keys(countsObj).reduce(function (sum, key) {
      return sum + (key === exceptKey ? 0 : countsObj[key]);
    }, 0);
  }

  /* repaint what the server would have rendered, from the current inputs */
  function refresh() {
    var current = counts();
    var items = itemCount(current);
    Array.prototype.forEach.call(tiles, function (tile) {
      var input = tile.querySelector(".qty");
      var qty = current[input.name] || 0;
      tile.classList.toggle("active", qty > 0);
      // show the floor and both ceilings, so a stepper never just does nothing:
      // the bag's own cap, and the scenario's total
      var minus = tile.querySelector(".minus");
      var plus = tile.querySelector(".plus");
      if (minus) minus.disabled = qty <= 0;
      if (plus) plus.disabled = qty >= parseInt(input.max, 10) || items >= maxItems;
    });
    if (totalEl) totalEl.textContent = items > 0 ? items + (items === 1 ? " item" : " items") : "no items yet";
    if (confirmBtn) confirmBtn.disabled = items <= 0;
  }

  function bump(input, delta) {
    var max = parseInt(input.max, 10);
    var next = (parseInt(input.value, 10) || 0) + delta;
    if (next < 0) next = 0;
    if (next > max) next = max;
    // a disabled plus cannot be clicked, but a guard keeps the rule in one place
    if (delta > 0 && itemCount(counts()) >= maxItems) return;
    input.value = next;
    refresh();
  }

  Array.prototype.forEach.call(tiles, function (tile) {
    var input = tile.querySelector(".qty");
    var minus = tile.querySelector(".minus");
    var plus = tile.querySelector(".plus");
    if (minus) minus.addEventListener("click", function () { bump(input, -1); });
    if (plus) plus.addEventListener("click", function () { bump(input, 1); });
    // typing a number is allowed too (and is the whole path with JS off)
    input.addEventListener("change", function () {
      // typing must respect both caps too: the bag's own, and what is left of the
      // scenario's total after the other tiles
      var current = counts();
      var room = maxItems - itemCount(current, input.name);
      var max = Math.min(parseInt(input.max, 10), room);
      var qty = parseInt(input.value, 10);
      if (isNaN(qty) || qty < 0) qty = 0;
      if (qty > max) qty = max;
      input.value = qty;
      refresh();
    });
  });

  /* ---- writing it down ---- */

  function record(bags) {
    return fetch("/bggo/api/scan", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(bags ? { code: code, bags: bags } : { code: code }),
      credentials: "same-origin",
      cache: "no-store"
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  /* the card has to stop asking: move the choice into the facts row, using the
     icons already in the tiles that were counted */
  function showChosen(bag) {
    if (fact) {
      fact.textContent = "";
      var icons = document.createElement("span");
      icons.className = "bag-icon bag-icon--inline";
      bag.mix.forEach(function (part) {
        var svg = picker.querySelector('.bag[data-key="' + part.key + '"] .bag-icon svg');
        if (svg) icons.appendChild(svg.cloneNode(true));
      });
      fact.appendChild(icons);
      var line = document.createElement("span");
      line.textContent = bag.mix_text;
      fact.appendChild(line);
    }
    say("Checked in. " + bag.mix_text + ".", "recorded");
  }

  if (!picker) {
    /* unallocated ticket: logged for the crew, deliberately never on the board */
    record(null)
      .then(function (data) {
        say(
          data.status === "ok"
            ? "Scan recorded. You are on the board."
            : "Ticket not recognised. Logged for the crew."
        );
      })
      .catch(function () {
        say("Could not reach the gate, so this scan was not recorded.", "failed");
      });
    return;
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault(); // with JS off the counts post as a normal form
      if (confirmBtn) confirmBtn.disabled = true;
      record(counts())
        .then(function (data) {
          if (data.status === "ok" && data.bag) {
            showChosen(data.bag);
            showStow(data.stow);
            showCabin(data.stow, data.cabin_label);
            open(false);
          } else {
            complain("That ticket is not on the manifest. Ask the gate agent.");
            refresh();
          }
        })
        .catch(function () {
          complain("Could not reach the gate. Confirm again.");
          refresh();
        });
    });
  }

  refresh(); // starts from the counts the server rendered (0, or the last choice)
  if (!picker.hidden) open(true);
})();
