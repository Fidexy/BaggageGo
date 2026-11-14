/* BaggageGO controls — the operator console.
 *
 * Plain, synchronous-feeling DOM: the rows are already server-rendered, so this
 * script only (a) calls the control API and repaints the one row it changed,
 * (b) drives the shared bag dialog, and (c) runs the client-side "start
 * boarding" trickle. No polling — the board polls itself; this page is an
 * editor, not a view.
 *
 * The trickle lives in the browser on purpose: it is a demo beat, and if the
 * operator closes the tab it should stop. It needs no server state and no
 * schema (the demo-group layer is a separate, unbuilt piece of work).
 */
(function () {
  "use strict";

  var token = window.BGGO_CTL_TOKEN || "";
  var body = document.getElementById("ctl-body");
  if (!body) return;

  var statusEl = document.getElementById("ctl-status");
  var inEl = document.getElementById("ctl-in");
  var picker = document.getElementById("ctl-picker");

  function status(text, isError) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle("is-error", !!isError);
  }

  function api(name, payload) {
    return fetch("/bggo/api/ctl/" + name, {
      method: "POST",
      headers: { "content-type": "application/json", "X-Control-Token": token },
      body: JSON.stringify(payload || {}),
      credentials: "same-origin",
      cache: "no-store"
    }).then(function (r) {
      if (!r.ok) {
        return r.text().then(function (t) { throw new Error("HTTP " + r.status + " " + t); });
      }
      return r.json();
    });
  }

  function refreshCount() {
    if (inEl) inEl.textContent = String(document.querySelectorAll(".ctl-row.is-in").length);
  }

  function setRow(p) {
    if (!p) return;
    var tr = body.querySelector('tr[data-code="' + p.code + '"]');
    if (!tr) return;
    tr.classList.toggle("is-in", p.checked_in);
    tr.querySelector(".c-bag").textContent = p.baggage_text;
    var stowCell = tr.querySelector(".c-stow");
    if (stowCell) {
      if (p.stow) {
        stowCell.textContent = p.stow.state === "own"
          ? p.stow.bin
          : p.stow.bin + " \u2190 " + (p.stow.own_bin || "?");
        stowCell.title = p.stow.note || "";
        stowCell.classList.toggle("is-redirect", p.stow.state !== "own");
      } else {
        stowCell.textContent = "\u2014";
        stowCell.title = "";
        stowCell.classList.remove("is-redirect");
      }
    }
    tr.querySelector(".c-hits").textContent =
      p.hits > 1 ? "\u00d7" + p.hits : (p.checked_in ? "\u00d71" : "\u2014");
    tr.querySelector(".c-state").textContent = p.checked_in ? "in" : "out";
    tr.querySelector('[data-act="checkin"]').disabled = p.checked_in;
    tr.querySelector('[data-act="checkout"]').disabled = !p.checked_in;
    try { tr.dataset.mix = JSON.stringify(p.mix || {}); } catch (e) {}
    refreshCount();
  }

  /* ------------------------------------------------------------ row actions */

  body.addEventListener("click", function (event) {
    var btn = event.target.closest ? event.target.closest("button[data-act]") : null;
    if (!btn) return;
    var tr = btn.closest("tr");
    var code = tr.dataset.code;
    var act = btn.dataset.act;
    if (act === "bag") {
      var mix = {};
      try { mix = JSON.parse(tr.dataset.mix || "{}"); } catch (e) { mix = {}; }
      openPicker(code, tr.querySelector(".c-name").textContent, mix);
      return;
    }
    api(act, { code: code })
      .then(function (res) { setRow(res.passenger); status(act + " " + code); })
      .catch(function (err) { status(act + " failed: " + err.message, true); });
  });

  /* ------------------------------------------------------------- filter */

  var search = document.getElementById("ctl-search");
  if (search) {
    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      Array.prototype.forEach.call(body.querySelectorAll(".ctl-row"), function (tr) {
        var hay = tr.textContent.toLowerCase();
        tr.hidden = q !== "" && hay.indexOf(q) === -1;
      });
    });
  }

  /* -------------------------------------------------------- bulk actions */

  function parseCodes() {
    var area = document.getElementById("ctl-codes");
    if (!area) return [];
    return area.value.split(/\s+/).filter(function (c) { return c.length > 0; });
  }

  document.querySelectorAll("[data-bulk]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var action = btn.dataset.bulk;
      var payload = { action: action };
      if (action === "preboard") payload.codes = parseCodes();
      status(action + "\u2026");
      api("bulk", payload).then(function (res) {
        status(action + ": " + res.affected + " affected \u00b7 reloading");
        window.setTimeout(function () { window.location.reload(); }, 500);
      }).catch(function (err) {
        status(action + " failed: " + err.message, true);
      });
    });
  });

  var reload = document.getElementById("ctl-reload");
  if (reload) reload.addEventListener("click", function () { window.location.reload(); });

  /* --------------------------------------------------- state snapshots */

  var exportBtn = document.getElementById("ctl-export");
  if (exportBtn) {
    exportBtn.addEventListener("click", function () {
      status("exporting\u2026");
      fetch("/bggo/api/ctl/export", {
        headers: { "X-Control-Token": token },
        credentials: "same-origin",
        cache: "no-store"
      }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        var name = "baggagego-state.json";
        var m = /filename="?([^";]+)"?/.exec(r.headers.get("content-disposition") || "");
        if (m) name = m[1];
        return r.blob().then(function (blob) { return { blob: blob, name: name }; });
      }).then(function (x) {
        var url = URL.createObjectURL(x.blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = x.name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        status("exported " + x.name);
      }).catch(function (err) { status("export failed: " + err.message, true); });
    });
  }

  var importInput = document.getElementById("ctl-import");
  if (importInput) {
    importInput.addEventListener("change", function () {
      var file = importInput.files && importInput.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        var snapshot;
        try {
          snapshot = JSON.parse(reader.result);
        } catch (e) {
          status("that file is not valid JSON", true);
          importInput.value = "";
          return;
        }
        if (!window.confirm("Replace the passengers and scans on the board with " + file.name + "?")) {
          importInput.value = "";
          return;
        }
        status("importing " + file.name + "\u2026");
        api("import", snapshot).then(function (res) {
          status("imported " + res.counts.passengers + " passengers, " + res.counts.scans +
                 " scans \u00b7 reloading");
          window.setTimeout(function () { window.location.reload(); }, 600);
        }).catch(function (err) {
          status("import failed: " + err.message, true);
          importInput.value = "";
        });
      };
      reader.readAsText(file);
    });
  }

  /* -------------------------------------------- start boarding (trickle) */

  var trickleTimers = [];
  var trickleStop = document.getElementById("ctl-trickle-stop");
  var trickleStart = document.getElementById("ctl-trickle");

  function stopTrickle(message) {
    trickleTimers.forEach(function (id) { window.clearTimeout(id); });
    trickleTimers = [];
    if (trickleStop) trickleStop.hidden = true;
    if (trickleStart) trickleStart.disabled = false;
    if (message) status(message);
  }

  /* Every arrival gets its own moment inside the window, drawn at random and then
     sorted — so the spacing is organic rather than a metronome, the last lands at
     or before `totalMs`, and the first is immediate so the click has feedback. */
  function trickleSchedule(totalMs, n) {
    var times = [0];
    for (var i = 1; i < n; i++) times.push(Math.random() * totalMs);
    times.sort(function (a, b) { return a - b; });
    return times;
  }

  if (trickleStart) {
    trickleStart.addEventListener("click", function () {
      var codes = parseCodes();
      if (!codes.length) { status("Paste some codes in the box first.", true); return; }
      // shuffle so the order is not the printed order
      for (var i = codes.length - 1; i > 0; i--) {
        var j = Math.floor(Math.random() * (i + 1));
        var tmp = codes[i]; codes[i] = codes[j]; codes[j] = tmp;
      }
      var secs = parseInt(document.getElementById("ctl-trickle-secs").value, 10);
      if (!secs || secs < 5) secs = 5;
      var minEl = document.getElementById("ctl-trickle-min");
      var maxEl = document.getElementById("ctl-trickle-max");
      var minU = minEl ? parseFloat(minEl.value) : NaN;
      var maxU = maxEl ? parseFloat(maxEl.value) : NaN;
      var carry = !isNaN(minU) && !isNaN(maxU) && maxU > 0;   // 0 units = no bag

      var times = trickleSchedule(secs * 1000, codes.length);
      trickleStart.disabled = true;
      if (trickleStop) trickleStop.hidden = false;
      var done = 0;
      status("Boarding " + codes.length + " over " + secs + "s" +
             (carry ? ", " + minU + "\u2013" + maxU + " units each" : "") + "\u2026");
      codes.forEach(function (code, i) {
        trickleTimers.push(window.setTimeout(function () {
          var payload = { code: code };
          if (carry) { payload.min_units = minU; payload.max_units = maxU; }
          api("checkin", payload)
            .then(function (res) { setRow(res.passenger); })
            .catch(function () { /* unknown code: still counts as an attempt */ })
            .then(function () {
              done += 1;
              if (done >= codes.length) {
                stopTrickle("Trickle complete: " + codes.length + " checked in.");
              } else {
                status("Trickle " + done + " / " + codes.length + " boarded\u2026");
              }
            });
        }, times[i]));
      });
    });
  }
  if (trickleStop) {
    trickleStop.addEventListener("click", function () { stopTrickle("Trickle stopped."); });
  }

  /* -------------------------------------------- the shared bag dialogue */

  var tiles = picker ? picker.querySelectorAll(".bag") : [];
  var totalEl = document.getElementById("ctl-total");
  var confirmBtn = document.getElementById("confirm");
  var pickerError = document.getElementById("ctl-picker-error");
  var currentCode = null;

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

  function refreshPicker() {
    var current = counts();
    var items = itemCount(current);
    var total = picker ? parseInt(picker.dataset.maxItems, 10) : NaN;
    if (!(total > 0)) total = Infinity;
    Array.prototype.forEach.call(tiles, function (tile) {
      var input = tile.querySelector(".qty");
      var qty = current[input.name] || 0;
      tile.classList.toggle("active", qty > 0);
      tile.querySelector(".minus").disabled = qty <= 0;
      tile.querySelector(".plus").disabled =
        qty >= parseInt(input.max, 10) || items >= total;
    });
    // The same words the passenger's card uses: this is the shared dialogue, and
    // the total is a count of pieces (the operator's units live in the table).
    if (totalEl) {
      totalEl.textContent = items > 0 ? items + (items === 1 ? " item" : " items") : "no items yet";
    }
    if (confirmBtn) confirmBtn.disabled = sum <= 0;
  }

  function openPicker(code, name, mix) {
    if (!picker) return;
    currentCode = code;
    document.getElementById("ctl-picker-who").textContent = name + " (" + code + ")";
    Array.prototype.forEach.call(tiles, function (tile) {
      var input = tile.querySelector(".qty");
      input.value = mix[input.name] || 0;
    });
    if (pickerError) pickerError.hidden = true;
    refreshPicker();
    picker.hidden = false;
    var first = picker.querySelector(".plus");
    if (first) first.focus({ preventScroll: true });
  }

  function closePicker() {
    if (picker) picker.hidden = true;
    currentCode = null;
  }

  Array.prototype.forEach.call(tiles, function (tile) {
    var input = tile.querySelector(".qty");
    var minus = tile.querySelector(".minus");
    var plus = tile.querySelector(".plus");
    function bump(delta) {
      var max = parseInt(input.max, 10);
      var next = (parseInt(input.value, 10) || 0) + delta;
      if (next < 0) next = 0;
      if (next > max) next = max;
      var total = parseInt(picker.dataset.maxItems, 10);
      if (!(total > 0)) total = Infinity;
      if (delta > 0 && itemCount(counts()) >= total) return;
      input.value = next;
      refreshPicker();
    }
    if (minus) minus.addEventListener("click", function () { bump(-1); });
    if (plus) plus.addEventListener("click", function () { bump(1); });
    input.addEventListener("change", function () {
      var current = counts();
      var total = parseInt(picker.dataset.maxItems, 10);
      if (!(total > 0)) total = Infinity;
      var room = total - itemCount(current, input.name);
      var max = Math.min(parseInt(input.max, 10), room);
      var qty = parseInt(input.value, 10);
      if (isNaN(qty) || qty < 0) qty = 0;
      if (qty > max) qty = max;
      input.value = qty;
      refreshPicker();
    });
  });

  var form = document.getElementById("ctl-picker-form");
  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var chosen = {};
      var current = counts();
      Object.keys(current).forEach(function (key) {
        if (current[key] > 0) chosen[key] = current[key];
      });
      if (!currentCode) return;
      if (Object.keys(chosen).length === 0) {
        if (pickerError) {
          pickerError.textContent = "Choose at least one bag.";
          pickerError.hidden = false;
        }
        return;
      }
      var code = currentCode;
      api("bag", { code: code, bags: chosen })
        .then(function (res) { setRow(res.passenger); closePicker(); status("bags saved for " + code); })
        .catch(function (err) { status("bag save failed: " + err.message, true); });
    });
  }

  if (picker) {
    var veil = picker.querySelector(".veil");
    if (veil) veil.addEventListener("click", closePicker);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closePicker();
    });
  }
})();
