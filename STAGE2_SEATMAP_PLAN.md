# BaggageGO passenger demo — Stage 2 plan: the seat-map view

> **Partly superseded 2026-11-10** (build log in `STAGE1_PLAN.md` §13, ruling
> `R17`). The view, the three seat states, the `?view=` switch and the
> `seat_map()` projection all shipped as planned. What has changed since:
> the **row-number gutter is gone** (numbers ride in the aisle, as on the supplied
> seat map), the letters now sit at **both ends**, both walls carry **exit doors**,
> and the cabin is **sized as one unit = one row pitch** instead of a square cell
> solved from `100vh`. The §0 non-goals ("no nose/tail marker, no exit rows")
> were revisited and the exit rows were added; a fuselage silhouette was also
> tried on 2026-11-10 and **removed again on request** — nose, cockpit, tail and
> wings are not part of the drawing. Read this file for the decisions, not for
> the current markup.

**Stage goal:** `/bggo/live` gains a second, switchable view — a cabin
seat map (3 + 3 squares per row, as many rows as there are allocated
passengers) that lights a passenger's **seat** as their ticket is scanned.
**Written 2026-11-07**, from this session's chat + `STAGE1_PLAN.md`.

Answers to the planning questions are recorded inline as **Q1–Q7** so the
next pass does not re-litigate them.

---

## 0. Scope

| # | Deliverable |
|---|---|
| 2.1 | `?view=seats` renders the cabin: row-number gutter + `A B C · D E F` header, all six squares per row, rows 1..max-occupied-seat |
| 2.2 | Three seat states (waiting / scanned / most-recent), driven by `scans` |
| 2.3 | A view switcher that works with JS on (no reload) and off (`?view=`, server-rendered) |
| 2.4 | A brief pulse when a seat *becomes* scanned |

**Non-goals (Q5):** no nose/tail marker, no dashed aisle line, no windows,
no exit rows, no zoom/pan, no seat↔passenger detail panel. The aisle is
expressed by the gap alone. `hkexpress-mobile/` is a **deprecated artifact** and
was not consulted for this work.

---

## 1. Decisions (locked this session)

| # | Decision | Why / source |
|---|---|---|
| Q1 | **Gutter + header labelling.** Row number in the left gutter, `A B C \| D E F` across the top; the **full seat id** (`4B`) is printed only on the lit/recent square | Literal ids on all 204 squares are illegible at projector distance; the gutter+header is how real seat maps (and the supplied mockup) label every position |
| Q2 | **Always render all six squares per row**; one row per occupied row number, `1..max` | Keeps the 3 + 3 cabin reading; "as many rows as needed (based on max passenger)" |
| Q2b | **Drop the "no allocated passenger" state.** A seat with no passenger looks exactly like an allocated-but-unscanned seat (waiting) | Explicit instruction: the map is about seats, not who owns them |
| Q3 | **The URL wins: `?view=list\|seats`, no `localStorage`** | A projector/bookmark URL must be deterministic; a stale local choice on the presenter laptop is a demo wart |
| Q4 | **Pulse on a newly scanned seat**, `prefers-reduced-motion` respected | The "as they scan" moment should be visible from the back of the room |
| Q6 | Team seats `1A–1E` are **ordinary seats** | They hold real codes and can scan their own passes |
| Q7 | Verified: the live db is **rows 1–4** (`20` allocated; id 12 = `2F`). The old TODO line "id 12 = 34A" is stale and is closed by this stage | Read-only query against `passengers.db`, 2026-11-07 |

Three seat states, and only one square is ever purple:

| Seat | Treatment | Mirrors the list |
|---|---|---|
| waiting (no scan, or no passenger) | quiet: `--hke-tint3` fill, muted label | dim row |
| scanned | `--hke-tint2` wash, ink, purple leading edge | lit row |
| **most recent** | brand-purple fill, white label | pinned row |

No literal hex in CSS, no cyan anywhere (brand contract).

---

## 2. Data model — no schema change

`scans` and `passengers` are untouched. The map is projected from the same
`build_state()` the list already uses (`seat_number`, `scanned`, `last_seen`).

New pure helpers in `server_v1.py`:

```
SEAT_LETTERS = "ABCDEF"      # 3 + 3; a test ties it to seed_passengers.SEAT_LETTERS
SEATS_PER_SIDE = 3

parse_seat("12A")            -> (12, "A") | None      # tolerates spaces/case
most_recent_code(passengers) -> code | None           # extracted from board_view
seat_map(passengers, recent_code) -> {
    "rows": 4,                       # max occupied row, 0 when nobody is seated
    "letters": ["A".."F"],
    "side": 3,
    "cells": [ {seat, row, letter, code, scanned, recent, state}, ... ],
}
```

`build_state()` gains two keys: `seats` (the map above) and `seat_rows`
(`seats["rows"]`). `/api/state` therefore carries the whole seat state, so
`board.js` **applies** seat classes rather than re-deriving the rule — the
server stays the single source of truth for what a square means (R9). The row
renderer and the poll payload come from the same `seat_map()`, so they cannot
disagree.

`seat_map` is defensive: a seat string the parser cannot read is ignored for the
row count (never a 500 from a hand-edited row).

---

## 3. The two views, one DOM

`board_html()` renders **both** views and never changes their shape:

```
<header class="band"> … </header>
<p class="counters"> … </p>
<nav class="views" aria-label="Board view">
  <a href="?view=list"  data-view="list"  aria-current="page">Passengers</a>
  <a href="?view=seats" data-view="seats">Seat map</a>
</nav>
<main class="board">   … the existing table … </main>
<main class="seatmap"> … the cabin grid … </main>
<p class="hint">Live, updating every second.</p>   <!-- moved out of .board -->
```

- `<body>` gets `view-list` or `view-seats` from `?view=` (default `list`).
  CSS shows one `<main>`, hides the other.
- **Why both in the DOM:** R9 — a JS-less load must tell the truth, and the
  existing `test_the_scripts_only_reach_for_elements_the_pages_render` requires
  every id/class `board.js` reaches for to be in the page. Rendering both keeps
  the switch instant and that contract intact.
- The switcher is a **real anchor**. With JS, `board.js` intercepts the click,
  flips the body class, updates `aria-current`, and `history.replaceState`s the
  URL — no reload, no flash. With JS off it is a normal navigation, and the
  server renders the chosen view. `?big=1` is preserved in the hrefs and composes
  with `?view=`.
- `.hint` moves outside both views so one node serves both.

### Seat grid markup

```
<section class="cabin" aria-label="Cabin seat map">
  <div class="cabin-row cabin-head">
    <span class="cabin-rno"></span>
    <span class="cabin-letters">A B C  ·  D E F</span>
  </div>
  <div class="cabin-row" data-row="1">
    <span class="cabin-rno">1</span>
    <span class="seat waiting" data-seat="1A" data-code="BGGO-…" title="1A"></span>
    …
  </div>
</section>
```

Each square: `data-seat`, optional `data-code`, `title`, an `aria-label` of the
seat id; no `data-index` (reserved for the table — a test asserts the row order).
`sizes` scale with `clamp()` against viewport height so 4 rows look generous and
34 rows still fit `1920×1080` under `?big=1`.

### `board.js` additions

1. On each poll, look up every `.seat[data-seat]` in the payload and set its
   state class (`waiting` / `scanned` / `recent`).
2. When a square changes **into** `scanned` between polls, add `just-scanned`
   for ~1.2 s (CSS animates it; reduced-motion disables the keyframes).
3. Intercept `.views a` clicks as above.

---

## 4. Files

| File | Change |
|---|---|
| `server_v1.py` | `SEAT_LETTERS`/`SEATS_PER_SIDE`, `parse_seat`, `most_recent_code`, `seat_map`; `build_state` gains `seats`/`seat_rows`; `board_view` reuses both; `board_html` renders the switcher + both mains; `?view=` parsing in `live` |
| `static/board.js` | seat-state application, pulse, view toggle |
| `static/style.css` | view visibility, cabin grid, seat states, pulse, `body.big` sizing — tokens only |
| `test_server_v1.py` | the new tests below |
| `README.md` | board section + `/api/state` shape + the seat view |
| `STAGE1_PLAN.md` | new ruling `R15`, stage-2 build log, old seat-map non-goal (D8) marked superseded |
| `TODO.md` | close the stale seat-row-cap line; note the view shipped |

No new dependencies. `.gitignore` unchanged (`deploy/`, db and codes stay
tracked).

---

## 5. Tests (added to `test_server_v1.py`)

1. `parse_seat` reads `12A`, lower case and padded; rejects junk and `12G`.
2. `server_v1.SEAT_LETTERS == seed_passengers.SEAT_LETTERS` (a duplicate that
   cannot drift).
3. `seat_map` row count == max occupied row; == 4 for the default deal.
4. `seat_map` has `6 × rows` cells, in row-major `1A..1F, 2A..` order.
5. Scanned passenger's seat is `scanned`; the most recent is `recent`; the rest
   `waiting`.
6. A seat with no allocated passenger is still `waiting` (never lit).
7. An unknown scan lights no seat.
8. `?view=seats` sets `view-seats`; default is `view-list`; `?view=seats&big=1`
   carries `big`.
9. Both mains render in one page; the switcher is real anchors with the right
   `aria-current`; the other view's href preserves `big`.
10. Seat map HTML: gutter numbers `1..rows`, header letters, `data-seat` for
    every cell, full id only on lit/recent.
11. `/api/state` carries `seats` + `seat_rows`, and `seat_rows` matches the HTML.
12. No seat square carries `data-index` (does not disturb the table-order test).
13. `board.js` element contract still holds (existing test; new classes must
    appear in the rendered page).
14. Brand: existing no-literal-hex tests cover `style.css`; the gate stays clean.

Run: `.venv/bin/python -m pytest -q` and `python3 brand/check.py pitch-demo`.

---

## 6. Build order

1. Write this plan; commit it alone.
2. Failing tests for the pure helpers (`parse_seat`, `seat_map`) → implement →
   green.
3. Failing tests for `?view=` + rendered grid → implement `board_html` + CSS →
   green (list untouched; `?big=1` from stage 1 still passes).
4. `board.js`: seat application, pulse, toggle; re-run the element-contract test.
5. Full suite + brand gate; screenshot the live board at 4 rows (`?view=seats`,
   and `?big=1`) to check the squares are legible on a projector.
6. Commit the feature separately; update `README.md`, `STAGE1_PLAN.md` (`R15`),
   `TODO.md`, and memory.

---

## 7. Risks

- **`?big=1` at 34 rows** makes squares small; the clamp keeps them square and
  on-screen, and the demo stays at 20 (4 rows). If the finals cabin is scaled up,
  re-measure by screenshot before the pitch.
- **Two renderings of the grid** (HTML + JSON) come from one function, so only
  the presenter's *view* can go stale, never the data.
- The pulse must not re-fire on every poll: transition detection lives in
  `board.js`, not CSS alone.
