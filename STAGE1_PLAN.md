# BaggageGO passenger demo — Stage 1 plan & handoff

**Stage goal:** audience scans a printed boarding-pass QR → their passenger page
opens → a live board on the presenter side highlights that passenger.
**Written 2026-11-06, from this session's chat only** (no external files or memory
consulted per request). Items marked **[verify]** were stated from earlier
in-session recollection and should be confirmed on the host before relying on them.

---

## 0. Scope

Deliverables for this stage:

| # | Deliverable |
|---|---|
| 1.1 | Live page showing a list of passenger info (the **board**, `/bggo/live`) |
| 1.2 | Scanning a printed boarding-pass QR opens a page with that passenger's details (`/bggo/<CODE>`) |
| 1.3 | A scan highlights the corresponding passenger on the live board + a **reset button** |

**Explicit non-goals for stage 1** (decided, do not build):
- No seat map anywhere — not on the audience page (Davis, this stage), board is a
  plain list. Seat distribution is a stage-2 join, not a stage-1 UI.
- No in-app camera scanner. The phone's built-in Camera app decodes the QR; the
  payload is a URL, so the "scan" is just Safari opening it.
- No baggage-volume writes. Column stays NULL; unit (litres assumed) still undecided.
- No name refresh tooling beyond what `seed_passengers.py` already has.

---

## 1. Context for whoever picks this up

**The pitch:** BaggageGO, Cathay Hackathon finals. Build + both pitches 14–15 Nov,
all members present. Pitch shape is "named BU + one job + one object"; the object
here is the printed boarding pass. Demo-failure resilience outranks polish.

**The passenger DB — exists and is stable** (`pitch-demo/`):
- `passengers.db` — SQLite, always exactly **200 rows**, id 1..200.
- `passenger_codes.txt` — **the lock**: 240 ticket codes (`BGGO-XXXX-XXXX`, base32
  minus `I L O U`), written once, never regenerated. All script modes reuse it;
  `--regen-codes` is the only escape hatch and invalidates every printed ticket.
- Slots 1..20 are allocated (name + seat), 21..200 hold code only. **Ids 1–5 are
  the fixed team roster** (`seed_passengers.py` → `TEAM_ROSTER`, seats 1A–1E): no
  re-deal, seed or `--refresh` can move them. The remaining allocated slots take
  seats from rows 1–34 × A–F, minus the team's five seats, **front to back in
  row-major order** (id 6 → 1F, then 2A, 2B, …). Seats are allocated, not
  randomised: scaling `--count` up or down only adds/removes passengers at the
  back of the cabin, and every already-seated passenger keeps their seat, so a
  printed pass cannot go stale. Baggage volume is NULL everywhere.
- `seed_passengers.py` flags: `--count N` (pins the team at 1–5, deals names+seats
  to slots 6..N, blanks the rest; N ≥ 5), `--refresh` (new random names;
  **codes and seats unchanged**), `--check` (read-only verify), `--rebuild` (recreate db from the
  locked codes file), `--regen-codes` (destructive). Default seed `114514`, so
  the default deal is reproducible. Known first rows of the default deal (team, then dealt):
  `BGGO-ZWY9-YCJS Au Bai Qiao 1A`, `BGGO-HPZH-WAJ7 Hui Long Wai 1B`,
  `BGGO-NZM2-1YQQ Lee Lok Sang 1C`, then `BGGO-YDM7-C7W5 Dominic Siu 1F` at id 6.
- Name refreshes never touch `baggage_volume_l` (scan data) — keep that invariant.

**Hosting facts [verify]:** webroot `/opt/stacks/web`, container `web-site-1`
(nginx:alpine), LAN `http://192.168.50.4:8090`, public `https://sophon1.dedyn.io`
via nginx-proxy-manager. The `personal-site` skill that documented this was **not
found on disk** during this session — re-derive the NPM proxy route on the host
instead of assuming. Also observed this session: an earlier `README.md` in the
app folder went missing while other files survived — re-check folder state
before assuming anything about what's on disk.

**Audience reality (researched this session):** Safari/iOS has no `BarcodeDetector`
(WebKit bug 281848; ~48% global support). A camera-scanner *inside* the web app
would need a vendored WASM decoder on every iPhone. Encoding a URL avoids the
problem entirely, and collapses the demo to one step: *scan ticket → personalized
page*. That is why the QR is a URL.

---

## 2. Decision log (locked this session)

| # | Decision | Why |
|---|---|---|
| D1 | QR payload = `https://sophon1.dedyn.io/bggo/<CODE>` | Native camera apps open it directly; works on ~all phones; no scanner lib, no permissions, no getUserMedia |
| D2 | The scan event is the **page load of the code URL**, logged server-side | "Who scanned" costs zero extra audience steps |
| D3 | One FastAPI + uvicorn process serving pages, API, and SQLite (WAL) | Rejected the earlier static-JSON idea — the board needs live shared state; also no CORS/build step/backend split to fail on stage |
| D4 | Board updates by **1s polling**, not SSE/WebSocket | SSE through nginx-proxy-manager silently dies without `proxy_buffering off`; polling has no such trap and self-heals |
| D5 | One `scans` row per ticket (`UNIQUE(code)`), `hits`/`last_seen` for repeats | Refresh ≠ a new person; re-scans show as activity (`×2`), not duplicates. Identity = the ticket |
| D6 | Unallocated codes (slots 21–240) → deliberate "no booking on this flight" page; counted as *unknown*, never shown on the board | Otherwise a curious judge scanning a blank ticket becomes a fake passenger live |
| D7 | Reset clears the `scans` table only | Passengers, codes, names, baggage are never touched by reset |
| D7a | Reset clears **every bag**, not just the scan rows | The bag choice lives on the scan row (`bag_units`/`bag_mix`) so `DELETE FROM scans` takes it, but `passengers.baggage_volume_l` is a bag figure no scan owns and the board prints it in the Baggage column — so reset nulls that too. Names, seats and tickets stay. **Amends D7; see R21.** |
| D8 | No seat map in stage 1 | Davis: not for this testing stage. **Superseded 2026-11-07: the seat-map view is built — `STAGE2_SEATMAP_PLAN.md`.** |
| D9 | Tickets: `segno` (pure-python, SVG), A6, `error='m'`, ~30mm QR, code in mono below; **print is the last step** — the URL is baked into the print | Printed QRs are immutable; any URL change reprints everything |
| D10 | Ticket front is spoiler-free: QR + code + wordmark, no name | The reveal is the demo; names live in the DB, and name refreshes after printing stay consistent because tickets only carry codes |
| D11 | Fonts: system font stack for stage 1, no Google Fonts | Known repo gotcha: venue wifi is a SPOF |
| D12 | Board target (laptop vs big screen) is **undecided** → build responsive + a `?big=1` theater mode (huge type, high contrast), decide at venue rehearsal | Do not block on this |

---

## 3. Pages & API

All routes under base path `/bggo/` (fixed forever, per D9).

### 3.1 Audience page — `GET /bggo/{code}`
- Server sets an httpOnly `sid` cookie (random, per device) if absent.
- Shell HTML + small JS: `POST /api/scan {code}` → render passenger card:
  name, seat, code, baggage ("—" / "not recorded yet"). Big type, thumb-reachable.
- Response `status:"unknown"` → render the **no-booking** screen instead.
  Unknown scans are logged separately and do **not** appear on the board (D6).
- Case-insensitive code; tolerate a trailing `/` and URL-encoded forms.

### 3.2 Board — `GET /bggo/live` (open — see R7)
- ~~Requires `?k=<token>` (token printed at server startup; keeps stray audience
  members out of the presenter view).~~ **Superseded by R7 (2026-11-06): open, no
  token, any `?k=` ignored.**
- Lists the 20 allocated passengers: name, seat, code, baggage, scan state.
- **Highlight rules (1.3):** rows start dim → scanned = lit → most recent scan =
  highlighted (accent + "3s ago", pinned to top). Earlier scans stay lit.
  Repeat scan of the same ticket: bump `×N`, move it back to "most recent".
- Header counters: `scanned 7/20 · repeat hits 12 · unknown 1 · last 2s ago`.
- **Reset button** (top-right, confirm-on-click): `POST /api/reset` → all rows dim,
  counters zero, board keeps polling. Rehearsal uses this between runs.
- Poll `GET /api/state` every 1s while the tab is visible; render HKT times
  ("14:32:05", relative for <60s). Store UTC, display UTC+8.

### 3.3 API

```
POST /api/scan   {code}                      → 200 {status:"ok", passenger:{code, full_name, seat_number, baggage_volume_l}}
                                                 | {status:"unknown"}
GET  /api/state                                → 200 {allocated:20, passengers:[{id, code, full_name, seat_number,
                                                      baggage_volume_l, scanned:bool, hits:int, last_seen:iso|null}],
                                                     counters:{scanned, hits, unknown, last_scan_age_s},
                                                     server_now:iso}
POST /api/reset  (board token in header)      → 204   # deletes all rows in `scans`
```

`/api/state` returns the full 20 rows every poll — deltas are not worth the
complexity at this size.

## 4. Schema (additive; created by the server on first run)

```sql
CREATE TABLE IF NOT EXISTS scans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL UNIQUE,      -- passengers.code; unallocated codes go here too, flagged
    known         INTEGER NOT NULL,          -- 1 allocated ticket, 0 unknown scan
    first_seen_at TEXT NOT NULL,            -- UTC ISO8601
    last_seen_at  TEXT NOT NULL,
    hits          INTEGER NOT NULL DEFAULT 1,
    session_id    TEXT,                      -- sid cookie; distinguishes refresh from new device
    ua            TEXT                       -- coarse: "iPhone" / "Android" / "Other"
);
```

Unknown scans (slots 21–240) are the `known=0` rows: counted in the header, never
rendered as passengers.

**Invariants:** `seed_passengers.py` never writes `scans`; the server never writes
`passengers` (stage 1). `--rebuild` deletes the whole db file and therefore wipes
`scans` — acceptable, but say so out loud if done near showtime.

---

## 5. File layout & naming

```
pitch-demo/
  seed_passengers.py        # exists — team roster 1-5 + random deal from id 6
  passengers.db            # exists
  passenger_codes.txt      # exists — THE LOCK, never edit
  server_v1.py             # FastAPI app: pages + API + scans table      (convention: <product>_<form>_v<N>.py)
  static/                  # app.js, board.js, style.css — plain, no build step
  make_tickets_v1.py       # segno → qr/<CODE>.svg + tickets.html (A6 @page) → print to tickets.pdf
  tickets.pdf              # generated; commit the final print run
  STAGE1_PLAN.md           # this file
  requirements.txt         # fastapi, uvicorn, segno — pinned
```

`server_v1.py` and `make_tickets_v1.py` bump versions on breaking change, per repo
naming convention. Server runs on `127.0.0.1:8091` (static site keeps :8090 [verify
port is free]) under a `.venv`, as systemd unit `baggagego.service`; NPM gets one
location `/bggo/` → `http://127.0.0.1:8091`. If NPM location-rules turn out to be
fiddly on the host, fall back to the nginx container config directly — the route
is one `proxy_pass` line plus `X-Forwarded-For`.

## 6. Build order

1. **Server skeleton:** routes, scans table, cookie, token, UTC clocks. Done when
   `curl POST /api/scan` returns Au Bai Qiao for `BGGO-ZWY9-YCJS` and `unknown`
   for a slot-21 code, and a second POST shows `hits:2`.
2. **Audience page:** card + no-booking screen. Done when it renders from the API
   response with JS and no framework.
3. **Board:** list, highlight rules, counters, reset. Done when two browser tabs
   (phone + laptop) show the highlight within 1s, and reset re-dims everything.
4. **Deploy behind NPM**, test **over 4G** (not LAN) from a real iPhone Camera app.
   Any TLS/proxy problem surfaces here, not at the venue.
5. **Print:** freeze the URL, run `make_tickets_v1.py`, print 20 A6 from Chrome.
   Rehearse with paper, not screens — matte paper under venue lighting is the
   real QR test.
6. **Venue rehearsal:** board on laptop and on projector (`?big=1`), pick the
   default (D12), record it here.

## 7. Acceptance gate for stage 1

- iPhone Camera app on a printed ticket → passenger page < 2s over 4G.
- Same moment, board (laptop, no refresh) shows that passenger highlighted within
  1s with "just now"; header counters correct.
- Re-scan same ticket → `×2`, single board row, not a duplicate person.
- Blank code (slots 21–240) → no-booking screen; board untouched; `unknown` +1.
- Reset → all rows dim, counters zero, polling continues; `passengers.db` rows
  1..200 and `passenger_codes.txt` byte-identical before/after.
- `python3 seed_passengers.py --check` still passes after all of the above.

## 8. Risks & gotchas

- **Printed URL is immutable** (D9). Any host/path rethink after printing = reprint.
  This is the single most expensive late mistake in the plan.
- **Two writers to SQLite** (server + seed script): WAL + tiny transactions; never
  run `--refresh` mid-demo (harmless but pointless), never `--rebuild` while live.
- **NPM route [verify]** — personal-site skill is missing from disk; derive from
  the running NPM config, don't trust memory.
- **Clocks:** store UTC, display HKT. A board showing UTC during a HK pitch looks
  broken.
- **4G test is mandatory** — audience phones will be on mobile data if venue wifi
  dies; that path must be proven, not assumed.
- Polling load (≤50 clients × 1 req/s) is trivial; do not "optimize" it away.
- `README.md` disappearance noted above — re-create it after the server lands so
  the folder is self-describing again.

## 9. Open questions (non-blocking)

1. Ticket front spoiler-free vs realistic — **default locked to spoiler-free (D10)**;
   flip before step 5 if overruled, printing is the deadline.
2. Board on laptop vs big screen — undecidable from the armchair; D12 defers to
   rehearsal.
3. Baggage-volume unit + who writes it — stage 2; column stays NULL.
4. Where `uvicorn` physically runs (same host as the web stack assumed) — confirm
   in step 4, first thing that breaks if wrong.

## 10. Stage 2 candidates (parked, in rough value order)

Seat-map view (join passengers ⨝ scans), baggage-volume writes + presenter button,
in-app scanner (vendored `qr-scanner`, ~60KB, only if the "scan with the app"
moment becomes part of the pitch), theater-mode polish, name refresh at the venue
(codes survive it by design).

---

## 11. Build log — 2026-11-06 (deliverables 1.1 + 1.2)

**Shipped.** 1.1 (`GET /bggo/live`) and 1.2 (`GET /bggo/<CODE>` + the scan that
records it). 1.3 was held back for a separate pass on 2026-11-06 and then built
the same day at the person's request: most-recent pin + accent, `×N`, four header
counters, reset button.

Files: `server_v1.py`, `static/{style.css,app.js,board.js}`, `test_server_v1.py`
(40 tests, green), `requirements.txt`, `README.md`, `deploy/baggagego.service`,
`.venv/` (Python 3.14; fastapi 0.162.0 / uvicorn 0.54.0 / segno 1.6.6).

### Rulings taken while building (these override the text above)

| # | Ruling | Why / invalidation condition |
|---|---|---|
| R1 | Board token is minted **once into `passengers.db`** (`settings.board_token`, `INSERT OR IGNORE`) and never rotates | Verbatim instruction, 2026-11-06. Overrides D-nothing but replaces "printed at server startup". Delete the row to rotate (void if you want rotation per restart). **Its role changed under R7: it is no longer a gate, only the reserved 1.3 reset header.** |
| R2 | `GET /bggo/<CODE>` is **server-rendered**; `POST /bggo/api/scan` only records | §3.1 (blank shell + JS render) contradicted D2. Reads work with JS off, and link-preview fetches can't inflate counts. Cost: "scanned" lands one frame later than the page. |
| R3 | API mounted **twice**: `/bggo/api/...` (canonical, what the HTML calls) and the plan's bare `/api/...` | The plan names `/api/*` but deploys one NPM location `/bggo/`. Void if two proxy locations get configured. |
| R4 | `scans.ua` keeps only the **last** UA; `session_id` the last sid (UPSERT overwrite) | D5 asks for coarse identity, not a log. Void if per-hit UA history is wanted. |
| R5 | Counters: `unknown` = distinct unknown tickets; `last_scan_age_s` = age of the newest scan **of any kind** | Not specified in §3.2. Board UI for counters is 1.3 anyway. |
| R6 | Docs/OpenAPI disabled; `sid` cookie is `Path=/bggo` | Host is shared with heimdall/stirling/etc.; a `sid` at `Path=/` could collide. |
| R7 | **`GET /bggo/live` is open — no token.** A supplied `?k=` is *ignored*, not rejected, so stale bookmarks keep working. The token survives only for 1.3's `POST /api/reset` header | Verbatim request, 2026-11-06: the person wants `https://sophon1.dedyn.io/bggo/live` and nothing else. **Reverts the §3.2 token gate** (§3.2 line 101 struck out). Rationale: `/bggo/api/state` already hands out the same 20 names + seats unauthenticated, so gating the page protected nothing. Void if `/api/state` gets gated too; a page gate *alone* is decoration. Cost: the board is world-readable — 20 synthetic names, no real PII. |
| R8 | The header's **"repeat hits" = `counters.hits` − `counters.scanned`** (scans past each ticket's first). The API field `hits` stays *total* scan events, per §3.3 | §3.2's wording and §3.3's field name disagree if read as the same number. Chosen so neither side breaks: the API keeps its documented contract, the label keeps its documented meaning, the arithmetic happens at the display layer (`repeat_hits()`). Void if the field is renamed or the label changes. |
| R9 | The board is **server-rendered in its current state** (lit/dim, pinning, counters, ages), not a dim shell that JS fills in. `board.js` only keeps it current | A dim-then-lit flash on a projector during the pitch is a demo wart, and a JS-less load should still tell the truth. Cost: the ordering + age rules now exist twice (Python and JS) — documented in both files, and the alternative (a `?k`-less board that lies when JS breaks) is worse. |
| R10 | **The app serves the brand kit from `../brand/`, never a copy of it.** `brand.css`, the Montserrat file and the lockup come over three explicit routes; `static/style.css` contains no literal hex, only `var(--hke-*)` | The kit's thesis is that `brand.json` is the only file holding a literal hex, and a copied `brand.css` is exactly the drift the gate exists to prevent. Three routes rather than mounting `brand/` so the provenance archives and the licence-restricted Pluto Sans trial stay off the wire. Cost: the deploy now needs the sibling `brand/` directory (`BGGO_BRAND_DIR` overrides). Void if the demo is ever shipped as a self-contained bundle, in which case copy *and* extend `check.py`'s scope to the copy. **Voided 2026-11-11**: the demo is now shipped as exactly that bundle — a container and a folder that can be copied out of the repo — so `pitch-demo/brand/` holds a vendored, byte-identical snapshot and `test_brand_vendored.py` fails on any drift from a `../brand/` that is present. No new gate scope was needed: `pitch-demo/` was already inside the scan, and `check.py pitch-demo` is clean (23 files) with the snapshot in place. |
| R11 | **The bag choice is the check-in, and it replaces recording the scan on page load.** A ticket URL with no bag on file opens the picker instead; `GET` alone now writes nothing | Verbatim request 2026-11-06: an overlay asks for one of four bags "instead of immediately checking in", replacing bag scanning. It also removes the surviving hole in D2/§3.1 (a JS-off GET, i.e. a link preview or crawler, used to write a scan row). Cost: a plain refresh of a card that already has a bag no longer counts a hit, so `×N` now counts *repeat choices* — re-picking a bag is what shows a repeat on the board. Void if the board's repeat count must reflect plain page loads, in which case record on load whenever a bag is already on file, and accept that previews inflate `hits`. |
| R12 | **The chosen bag lives on the `scans` row (`bag_units REAL`), in abstract "units", and `passengers.baggage_volume_l` stays NULL.** `COALESCE` on update means a bag-less re-scan keeps the choice; `init_db` ALTERs an old table instead of requiring a rebuild | Whoever owns the bag owns the volume: in stage 1 the bag is chosen per scan by the audience, while `passengers` belongs to `seed_passengers.py` and the server must never write it. Units (0.5/1/1.5/2) are a placeholder — the request was explicit that a real volume is not decided yet, so nothing converts them to litres. Void when a per-bag litre figure is agreed: then add it to `BAGS`, decide whether `baggage_volume_l` becomes the mirror, and convert at one point. |
| R13 | The four bag glyphs are **authored in the app** (`static/icons/`), derived from the kit's bag geometry, rather than added to `brand/icons/svg/` | `hke-values/export_icons.py` **clears `svg/` on every rebuild**, so a hand-added file there is deleted by the next export; and these are UI glyphs (four sizes of one object), not brand marks. They follow the supplied `baggagego-symbol.svg` conventions (64-unit box, `currentColor`, round caps) and the gate checks them, so they cannot drift off-livery. Void if the kit ever needs them: add a bag-size step to the exporter and keep the app reading from the kit instead. |
| R14 | **The picker counts bags; one Confirm button at the bottom writes it down.** `Σ units × how many`, up to `MAX_PER_BAG` (3) of each kind — **except a small trolley case, capped at 2** (`Bag.limit`) — and at most `MAX_ITEMS` (3) pieces in total, whatever the spread — and the choice is stored as **both** the counts (`scans.bag_mix`, JSON) and their total (`scans.bag_units`) | Verbatim request 2026-11-06: "instead of just a simple press, make it so there's a confirm button at the bottom … a +− qty picker for each bag, which adjusts the units accordingly." Storing both is deliberate: the total is what the board and the card display (so it is stored, not re-derived per poll), while the counts are what the person actually chose (so the card can say *Handbag, Backpack ×2* and Change bag can re-open pre-filled) — computed in one place from the counts, so they cannot disagree. Cost: one more column, added by in-place `ALTER TABLE`. Void if the total ever needs to disagree with the counts, which would mean one of them is wrong. |
| R15 | **The units are a ladder — 0.25 / 0.5 / 0.75 / 1 — and softness, not size, orders it**: duffel `0.75` below small trolley case `1.0` | Requested 2026-11-06 ("units should span from 0.25, 0.5, 0.75, 1.0 … swap trolley case — rename it small trolley case — and duffel"). A quarter-unit handbag to a whole-unit rigid case, and the odd-looking pair is the honest one: a duffel gives bin space back because it squashes, a hard trolley occupies exactly what it measures. One step per rung also makes the total legible at a glance (`1.25 units` reads as "a bag and a quarter"). A test pins the ladder, its order, and that the trolley is the whole unit, so the swap cannot silently revert. **Void if** a real per-bag litre figure arrives (R12's invalidation), since the ladder is a placeholder. The key stays `bag-roller` while the label moves: keys live inside every stored `bag_mix` row, so only a data migration renames one. |

| R15 | **`/bggo/live` has two views, one DOM.** `?view=list|seats` selects `view-list`/`view-seats` on `<body>`; both views are always server-rendered, and the switcher is a pair of real links that `board.js` upgrades to an in-place class flip + `history.replaceState`. The cabin is projected by one `seat_map()` used by both the rendered grid and `/api/state`, so the projector and the poll cannot disagree | R9 says a JS-less load must tell the truth, and the existing element-contract test requires every class `board.js` reaches for to be in the page. Both-in-DOM gives instant switching, a shareable URL (`?big=1` preserved) and no reload. A seat with no allocated passenger is a waiting square (explicit request: drop that state); rows are derived from the db, so the row-count TODO closes itself. Plan + answers: `STAGE2_SEATMAP_PLAN.md`. Void if the page must stop carrying both views, e.g. a 200-passenger cabin's DOM cost — then switch to server-rendered-only per `?view=` and make the JS toggle a real navigation. |
| R16 | **The brand kit had a self-referential role alias** — `build.py` emitted `--hke-hairline:var(--hke-hairline)` because the role and its primitive share a name. A custom-property self-cycle is invalid at computed-value time, so the token resolved to nothing and **every hairline in the repo was invisible**. `build.py` now skips a role whose name equals its target; regenerating loses exactly that one line (`brand.py`/`contrast.md` byte-identical) | Found via `var(--rule)` on the new seat squares; measured, not guessed (pixel-probed `#D6D6D8` absent before, present after). `check.py` compares colours and fonts, not cycles, so the gate never caught it. Void if `check.py` grows cycle detection — then this is covered by the gate and the note can shrink. |
| R17 | **The cabin is sized as one unit = one row pitch.** `--rows` comes from the server; the cabin is `--rows` + 1.9 units of chrome (the two letter strips), every band's height *is* the unit and every width is a multiple of it, so `--unit` is solved from the space the page leaves with both constraints stated: `min( (100vh − chrome) / units, (100vw − pads) / row-width, cap )`. The row-number gutter is gone — numbers ride in the aisle, as on the supplied seat map, so the letters sit at both ends and the cabin is symmetric | The previous rule solved a *square* cell from `100vh` and paid for the row gaps; hanging extra chrome off that would have added another constant to keep in step, and the heights would still have been guessed at (the 2026-11-09 note records one such guess being ~50 px wrong). Stating the constraint instead of the result means the drawing cannot overflow, and the aircraft's proportions now live in one rule. Cost: `style.css` holds the one structural constant (the page chrome, measured by screenshot), and a fraction-of-a-unit band height is not expressible — bands are whole units or a fixed multiple. Amended 2026-11-10 (same day, from a zoom complaint): `--unit` carries a **rem floor** (1.1rem, the size that fits 1080p) and the viewport only ever *grows* it, because a purely viewport-sized cabin fits every zoom level and so a browser zoom re-fits it instead of enlarging it; and **no `max-width` query may touch the cabin** — phone sizing keys off `(hover: none) and (pointer: coarse)`, since a width query fires at high zoom on a desktop and re-sizes the cabin out from under the reader. Cost: on a window shorter than a 1080p frame the normal view now scrolls rather than shrinking the seats (theater mode still fits it). Void if the cabin ever needs to fit a box that is not the viewport, in which case the `100vh`/`100vw` pair gets a container query instead. |

| R21 | **Reset means "nobody has a bag" — everywhere a bag can live.** `DELETE FROM scans` already takes `bag_units`/`bag_mix` with it, so the bin loads and the stow advice (both projected from those) go back to zero; the one bag a scan does not own, `passengers.baggage_volume_l`, is nulled in the same transaction. And the cabin's *copy* — the legend's three counts, `N compartments over capacity`, and the seatmap's `aria-label` — travels in `/api/state` (`cabin`), because `board.js` repaints the compartments every poll | Requested 2026-11-10: "reset board should reset the bins and baggage too". Bins and baggage are derived, so the data half was already true — but the legend and the `aria-label` were rendered once, server-side, so a reset re-dimmed the rectangles while the words kept claiming a full cabin until someone reloaded: the board could say "3 compartments over capacity" over an empty aircraft. One operation (`reset_world()`), two doors (`POST /api/reset` and the console's Clear), and one definition of the copy for the page and the payload. **Cost:** `server_v1` now writes one column of `passengers` — nothing else in the module does, and the control API still never does. **Void if** the demo ever needs a reset that keeps the bag volume (e.g. a pre-boarded world), in which case that world needs its own operation rather than a flag on this one. |


### Host facts re-derived (the `personal-site` skill is genuinely gone)

- NPM is a **host-network** container (`/opt/stacks/npm`, compose says
  `network_mode: host`) → a location rule pointing at `127.0.0.1:8091` reaches
  the app. NPM template: `proxy_pass <scheme>://<host>:<port><forward_path>` with
  no URI when the forward path is empty, so `/bggo/...` survives the hop.
- Proxy host **1** = `sophon1.dedyn.io` → `http://192.168.50.4:8090`, cert
  `npm-1` (Let's Encrypt, valid to **2027-01-22**), `ssl_forced: 0`, no
  locations, no advanced config. That is the host to add `/bggo` to.
- **`web-site-1` has been exited for ~31 h** (compose `/opt/stacks/web`,
  nginx:alpine on 8090). `https://sophon1.dedyn.io/` therefore returns **502**
  right now. Adding the `/bggo` location makes the printed URL work whether or
  not the static site is up.
- Port 8091 was free; `.venv` is on Python 3.14 (system fastapi absent, so the
  venv is required, not optional).

### 1.3 build log (same day)

`build_state` gained no fields; `board_view` owns order + display strings,
`age_text`/`counter_age_text` the clock rules, `repeat_hits` the derived counter.
`POST /bggo/api/reset` deletes `scans` and returns 204.

**One real bug found and fixed while implementing it:** scan timestamps were
written at `timespec="seconds"`, so two tickets scanned inside the same second
tied, `max()` kept the first, and the "most recent" pin silently fell back to
seat order. Now microseconds (`utcnow_iso()`), ordering compares *parsed*
datetimes rather than strings, and two regression tests hold it
(`test_same_second_scans_are_still_ordered_by_time`,
`test_scan_stamps_carry_sub_second_precision`). Observed live: the Jade→Jessica→
Jade sequence that used to tie came through 21 ms apart.

Verified live over TLS: clean board shows `0/20 · 0 · 0 · —`; after scans it
shows `2/20 · 1 repeat · 0 · 0s ago` with the repeat pinned first and
`class="row scanned recent"`; reset is 403 without the header / 403 wrong / 204
right, then the board returns to zero and `seed_passengers.py --check` still
passes.

### HK Express livery (same day, after 1.3)

All deliverables now wear the brand: `../brand/` is *served*, not copied (R10),
the board is an operations surface in brand purple on white, and the passenger
page is a boarding pass. `static/style.css` was rewritten to read only
`var(--hke-*)`, so it holds no literal hex at all — enforced by two new tests and
by the kit's own gate (`python3 brand/check.py pitch-demo` → clean).

One behaviour change while branding: **`×N` is now server-rendered too**, not
JS-only, so the projector never shows a stale repeat count between a scan and the
next poll. That is R9 applied consistently, not a new direction.

Sizes were measured with headless Chrome against the live URL rather than guessed:
the near-white `--hke-tint` wash on scanned rows was replaced with `--hke-tint2`
because it vanished on a projector, and `?big=1` was re-sized until all 20 rows
fit a 1920x1080 screen without scrolling.

Still failing the kit's gate, and **not touched because they are not mine**:
`hkexpress-mobile/` (the pre-existing mockup, triaged in `brand/README.md`) and
`#762A8F` in the two copies of the supplied `baggagego-symbol.svg` under
`icons/src/` — a near-miss purple in a reference-only file; the exported marks are
correct. Both are the client's call.

### The bag picker (next stage, requested 2026-11-06)

Built the same day: the ticket URL draws a translucent overlay asking which of
four bags you are bringing, and the choice is the check-in (R11–R13). Volume is
in abstract units — 0.25 / 0.5 / 0.75 / 1 — until a real per-bag litre figure is
agreed. `bag_units` was added to `scans` by an in-place `ALTER TABLE` migration,
with a test that builds a pre-picker table and asserts the old row survives.

The four icons went through three rounds of looking at them rather than reasoning
about them: they started on a loose 64-unit grid (so they read tiny next to the
picker's text) and were tightened onto a shared ink box — which also preserves the
size differences that make four bags legible as four bags — and the roller's
wheels were enlarged because they vanished at 46px. Stroke weight was matched to
the kit's own `icon-bag.svg` by eye, from a rendered comparison. The backpack was
redrawn twice: shoulder-strap arcs read as *ears*, and a small top loop read as a
knob on a hat, so it is now told by proportion (narrow and tall, lid seam) instead
of by extra parts.

The bags were then renamed from airline categories to **specific types** at the
person's request — a handbag, a backpack, a trolley case, a duffel bag: everything
in this demo is carried on, so "checked bag" was a lie, and a test now rejects
category words outright. The units and the icons did not move.

The JS click path cannot be clicked in this suite, so it was verified twice over:
by driving a real headless click through a temporary same-origin harness (the row
came back with `bag_units = 1.5`), and by a test that asserts every id and class
`app.js` / `board.js` reach for is actually rendered — so renaming an element
fails in CI instead of silently doing nothing on a phone.

### The picker grew quantities (requested 2026-11-06, same day)

"Instead of just a simple press, a confirm button at the bottom, and a +− qty
picker for each bag that adjusts the units accordingly." So a tap is no longer a
choice: each bag has a stepper, the total updates live, and **Confirm** is the
check-in (R14). The stored shape went from one number to counts + total, and
`bag_units` alone is still accepted by the API as shorthand for one of a bag.

Two things the screenshots caught, again: the card's fact row pushed "2.5 units"
to the right of its own line (the units are now inline in the same text flow as
the names, not a separate flex item), and the inline icons stacked vertically
because every icon SVG is `display: block`.

One flaw worth recording, because it was invisible with JS on: the Confirm button
was rendered `disabled` by the server when nothing was chosen yet. With JS that is
harmless (`refresh()` recomputes it), but with JS **off** the attribute could
never be lifted — the numbered fields would fill in and the button would stay dead
forever. The button is now always live, JS disables it on load, and an empty
submit redirects back to the card with "Choose at least one bag to check in."
instead of a wall of JSON. Counts that are out of range or unknown stay a hard 422.

### The units became a ladder (same day, 2026-11-06)

First cut was `0.5 / 1 / 1.5 / 2`. Replaced on request with a quarter-to-whole
ladder — `0.25 / 0.5 / 0.75 / 1` — and the trolley moved: it is now the **Small
trolley case** at a whole unit, with the soft **duffel below it at 0.75**. That
ordering is the interesting one (R15): a duffel gives bin space back because it
squashes; a rigid case occupies exactly what it measures. Renaming the label was
free, renaming the key was not — `bag-roller` stays, because that string is
inside every stored `bag_mix` row, so a key only moves with a data migration.

A test pins the ladder, its order, and the trolley being the whole unit, so the
swap cannot quietly revert; another pins `1 unit` (singular) against `1 units`.

### Open

- **Hosting may move to Google Cloud** (unconfirmed, 2026-11-06). The QR host
  must survive the move: keep `sophon1.dedyn.io` and repoint DNS/cert, or every
  printed ticket is reprint material (D9). `server_v1.py:PUBLIC_BASE` is the one
  knob; it is unused by the server itself, only by the ticket generator (step 5).
- ~~Deploy (step 4) is handed over~~: done 2026-11-06 (service + NPM location).
  `README.md` → Deploy keeps the steps for a rebuild or a move.
- Board token minted in the live `passengers.db` on 2026-11-06; read it with
  `.venv/bin/python server_v1.py --token`. Not needed to view the board (R7) —
  it guards `POST /api/reset`.
- **1.3 is done** (2026-11-06, this session), and the bag picker is in.
- **A real volume per bag is undecided** — the four bags carry abstract units
  (R12). Add the litre figure to `BAGS` when it is agreed.
- 1.1/1.2/1.3 and the picker are all shipped; what remains for the pitch is
  physical: the 4G/iPhone pass, the URL freeze and backup, and printing.
- **Deploy is live** (2026-11-06): `baggagego.service` active, NPM location
  `/bggo` → `127.0.0.1:8091` present in `/data/nginx/proxy_host/1.conf`, and
  `https://sophon1.dedyn.io/bggo/live` → 200 (~12 ms/request). Verified end to end
  over TLS: both pages, all three static assets, a real `POST /api/scan`, the
  no-booking screen, and the `?k=`-bearing legacy URL. Test scans were cleared
  afterwards via the new reset endpoint, so the live board starts empty.
  Remaining for the pitch: the physical 4G/iPhone pass (step 4), the URL freeze +
  backup of `passenger_codes.txt` (single copy on disk — see §11 and TODO.md),
  and printing (step 5).
- `web-site-1` is still exited, so the domain root still 502s while `/bggo/`
  works. That is now the *desired* decoupling, not a fault.


---

## 12. Build log — stage 2, the seat-map view (2026-11-07)

Plan and the answered questions: `STAGE2_SEATMAP_PLAN.md` (committed on its own,
before any code — the stage-1 mistake of bundling the plan with the feature is
not repeated). Rulings `R15`/`R16` above.

**Shipped:** `?view=list|seats` on `/bggo/live`. The cabin is 3 + 3 squares with
a row-number gutter and an `A B C | D E F` header, one row per occupied row
number (row 1 first), derived from the db — 4 rows for the default 20, up to 34
at `--count 200`. Three seat states mirroring the list (waiting / scanned /
most-recent), a promo-yellow pulse on a square that lights, and only a lit square
printing its full id. Both views are always rendered and `<body>` carries
`view-list`/`view-seats`; the switcher is a real pair of links that `board.js`
upgrades to an in-place class flip. `seat_map()` is the single projection behind
both the HTML grid and the new `seats`/`seat_rows` keys in `/api/state`.

**`R16` found while building it:** the brand kit's `--hke-hairline` was a
self-reference cycle, so every hairline in the repo had been invisible (board
rows, card borders, tear line). Fixed in `brand/build.py`, regenerated, verified
by pixel-probing the screenshot (`#D6D6D8` absent before, present after).

Verified: 95 tests green (`pytest -q`), `brand/check.py pitch-demo` clean, and
screenshots at 1440x1000 and 1920x1080 `?big=1` (all 4 rows fit, squares
legible). `node --check` on `board.js`.

**Note on history:** a prior session built this stage as commits `b4fff45`
(brand fix) + `6f20937` (feature), then the branch was hard-reset to `f82f19a`;
both commits are still in the object store but unreachable. This log describes the
rebuilt version, committed separately (plan, brand fix, feature).

**Follow-up bug (found live, same day):** the first deploy of the seat view
looked broken in a browser — the cabin fell out below the passenger list and the
view links appeared to do nothing. The code was correct (a fresh headless
browser hid `.seatmap` and the JS flipped the class), but `style.css`/`board.js`
are served by `StaticFiles`, which sends `last-modified` + ETag **and no
`Cache-Control`**, so the browser had heuristically cached the pre-change
stylesheet while the HTML (which is `no-store`) was fresh: new markup, old CSS.
Fixed by versioning every asset URL with `?v=<mtime_ns>` and adding
`Cache-Control: no-cache` for `/bggo/static/` and `/bggo/brand/`. Verified with a
fresh headless browser: list→seats→list flips `display`, the click is intercepted
(no navigation), and a full-height screenshot of the list has zero cabin pixels
below it. 96 tests.

---

## 13. Build log — the cabin redrawn (2026-11-10)

**Asked for:** the seat view should resemble the supplied seat map — a cabin
drawn as a fuselage, doors marked, still vertical, and still the same colours.
**Orientation did not move** (an earlier draft transposed it; that was rejected),
so `seat_map()`, `/api/state` and `board.js` are untouched: this is presentation.

**Shipped:** the row-number gutter is gone and the numbers ride **in the aisle**,
where the supplied map puts them — the axis label runs once along the cabin and
the other one at both ends, so the letters are now in a header *and* a footer
strip. Beside the door rows sit `EXIT` chips (white-backed, so a chip stays
legible over anything) with double chevrons pointing outward;
`cabin_doors()` clamps the door rows to the cabin we have — 1, 18, 19 and the
last row, HK Express' A321 layout at 40 rows and still a front and a rear door at
the 4-row test deal.

**No marker sits on a seat row** (explicit instruction, same day): every `EXIT`
chip lives in its own `.cabin-gap` band, because the space is what says "exit".
The front door is marked **in front of row 1**, the overwing pair in the gaps
either side of it, the rear door **behind the last row**, and every seat row's
gutter is an empty spacer. `cabin_markers()` returns `(band position, door row)`
for each — position `rows + 1` means behind the last row — so the placement, the
count and the height budget (`--exit-count`, with `--exit-gap` in `style.css` as
the band height) all come from one list instead of three code paths.

**Removed on request, the same day:** first the nose cone, the dark cockpit band,
the tail cone and the wing shading, then the fuselage walls and both bulkheads —
"no border enclosing the seats". What is left is the seat grid, its labels and
the exit marks. Two tests pin that: `test_the_cabin_is_drawn_as_a_fuselage`
asserts the silhouette classes are absent from the markup, and
`test_nothing_draws_a_border_around_the_seats` reads the `.cabin-seats` rule in
`style.css` and fails if a border comes back there.

**Sizing — `R17`.** The old rule solved a square cell from `100vh` and paid for
the row gaps; adding chrome on top of that was going to be another magic number.
It is now **one unit = one row pitch**: the cabin is `--rows` plus 1.9 units of
chrome (two letter strips), every band's height *is* that unit, and every width
is a multiple of it, so `--unit` is solved from the space the page leaves with
both constraints stated — `min( height, width, cap )`. Height binds on a
projector, width on a phone, and neither needs a breakpoint. The two constants
are page chrome, measured by screenshot (16rem normally, 14rem with `?big=1`).

**Mobile.** A 40-row cabin is taller than a phone whatever we do, so under 640px
the height clause is dropped on purpose: the map sizes by width, the squares stay
readable, and the page scrolls to the rest of the cabin. Fitting it whole on a
phone means 9 px seats and unreadable `EXIT` chips; the **list** is the view to
read on a phone. The band also wraps there — lockup and title on one line, clock
and Reset on the next — instead of clipping the Reset button off the edge.

**Verified:** 101 tests green, `brand/check.py pitch-demo` clean (10 files),
`node --check board.js`, and headless screenshots at 1366x768, 1440x900 and
1920x1080 (`?view=seats`, `?view=seats&big=1` — whole cabin plus the status line
inside the viewport, bottom rows pixel-clean) and at 390x844 (readable squares,
scrolls by design). The cabin is still a **narrow column** at 40 rows (~200 px of
a 1920 px frame): the vertical 40-row cabin's honest limit, unchanged by this
work and still open in `TODO.md`. `STAGE2_SEATMAP_PLAN.md` is marked superseded
where it promised no exit rows and a row-number gutter.

---

## 14. Build log — overhead compartments on the outer sides (2026-11-10)

**Asked for:** rectangles representing the overhead compartments, on the outer
sides of the seat view, each one the length of two seat rows.

**Shipped (after two corrections, same session):** the first cut stopped a run
of rows at an exit band, so no bin crossed a door. The second made the bins span
an exit but **ran three rows long over it** (two seat rows plus the band) and left
the chevrons sitting on the hatch. Both are fixed:

- **The exit counts as one row of a compartment.** `cabin_slots(rows)` lays the
  cabin out as one-slot pieces — `("row", n)` for a seat row, `("band", n)` for
  the exit band in front of row `n` — and `.cabin` holds `--exit-gap: 1` so a band
  **is one row pitch tall**. `cabin_bins(rows)` consumes the slots two at a time,
  so every compartment is exactly two rows: at 40 rows the bins run 1+2, 3+4, …
  15+16, **17+b18**, 18+19, **b20+20**, 21+22 … 39+40 (21 a side). `top` / `-1`
  says whether a rectangle starts at its anchor row or reaches up over the band in
  front of it (`reaches-up`); the label (`"17+b18"`) rides on the element for the
  tests. An odd number of rows still leaves the last row bare.
- **The bin has its own lane.** Every band is now five flex pieces — door gutter,
  bin lane, seats, bin lane, door gutter — and the rectangle is drawn in the
  `.cabin-lane`, never in the `.cabin-door` gutter. A bin and an `EXIT` marker are
  level with each other but in different lanes, so they **cannot overlap**.

It hangs over the two rows it belongs to — the lane sits on the row's centre
line, so `top: calc(--unit * -.5 …)` is the row top and `-1.5` one row higher —
which means it is a pure overlay: **`--exit-count` and the height budget are
untouched** (`test_a_compartment_costs_the_height_budget_nothing` pins that the
`.cabin-bin` rule stays `position: absolute`). It is hatched
(`repeating-linear-gradient` of `--wash` over `--surface`) instead of filled,
because the seats are the picture and the bins are only the room around them.

**Verified:** 107 tests green (101 + six bin tests: the slot layout, the two-row
compartment, the band-as-a-row, and the lane separation),
`brand/check.py pitch-demo` clean (10 files), and headless screenshots at
1920x1080 (`?view=seats`, `seats&big=1`) and 390x844 — every compartment is two
rows tall including the two at the overwing bands, and the `EXIT` chips sit outboard
of the bins with room between them.

## 15. Build log — the bin-load prediction (2026-11-10)

**Asked for:** the base prediction that a passenger's bag goes in the compartment
directly above them; compartments hold 4.5 units; green 0–1, yellow 1–3, red above.

**R18.** *The prediction is the input we hold, and it is not redistributed.*

| Part | Decision | Why |
|---|---|---|
| The rule | every **declared** bag goes in the compartment above its owner's seat, on its owner's wall (`bin_for_seat()`) | The honest base case, and one line of geometry — no weights to defend on stage. A passenger who has not chosen a bag adds nothing and is the denominator instead, so the tooltip can always say *"1 of 6 seats declared"* |
| Capacity | `BIN_CAPACITY = 4.5`, **per wall per rectangle** | The two rectangles are already separate elements in the drawing, and they are two separate pieces of luggage space. Six seats (`1A–2C`, two rows) against 4.5 units makes overflow reachable; pooling both walls would hide 12 seats behind one number and flatten the picture |
| The ramp | `bin_level()`: green to 1, yellow to 3, red above, `is-over` past 4.5 | Decided on the server — `board.js` applies the word it is sent, exactly as it applies a seat's state (R9). `is-over` is the one fact red alone cannot carry: the prediction no longer fits |
| Zero | **literal green** | A compartment with nothing predicted in it is a compartment with room (requested). 42 green bands at 0 scans is truthful: the map is a prediction, and an empty prediction has room in it |
| The number | `title` + a legend, **never inside the rectangle** | At 40 rows on a 1080p projector the lane is ~8 px wide; no number fits. The legend spells the ramp out in words and counts each level, and it sits outside the `role="img"` seatmap so the colour is not the only carrier |

**Geometry worth recording:** `17+b18` and `b20+20` cover one seat row each, so
three whole trolleys fill one to exactly `3.0` — the top of yellow. **Red is
unreachable in the two exit-band compartments**, and that is the layout rather
than a bug; `test_an_exit_band_compartment_cannot_reach_red` pins it so nobody
"fixes" it later.

**No new colour, no schema change.** The ramp is `--hke-ok` / `--hke-warn` /
`--hke-error` (already in the kit) tinted into the existing hatch; `/api/state`
gains `bins`, and `cabin_html()` colours each wall from it. The bag ladder
(0.25–1, `R15`) is the input and is untouched.

**Verified:** 125 tests green, `brand/check.py pitch-demo` clean (10 files),
headless screenshots at 1920x1080 and 390x844 with a throwaway 68-scan db — the
front bays read red with `is-over` on the 2 px edge, one yellow bay mid-cabin,
the blank rear two-thirds green, and the legend counts matching the rectangles
(30 room / 1 filling / 11 full / 6 over capacity).

*(Two rulings above are numbered `R15` — a collision from 2026-11-07. This one is
`R18`, so the next free number is `R19`.)*

## 16. Build log — the stowage suggestion on the card (2026-11-10)

**Asked for:** the ticket page (what the person sees after the boarding-pass QR)
should suggest which compartment to stow their bag in, based on the overhead-bin
status; if the compartment above them is full — "at 4 units of occupancy or above"
— suggest **opposite / in front / opposite and in front / behind (last)**; and
name compartments `1-2L`, `3-4R` (row span, wall) for now.

**R19.** *The advice is the base prediction read outward, and it names the bin it
would have used.*

| # | candidate | from `18A` | why that order |
|---|---|---|---|
| 0 | the bin above your seat | `18-19L` | the base rule (R18) — used until it stops fitting |
| 1 | **opposite**, same rows | `18-19R` | one step across the aisle: the nearest bin that is not overhead, and the least walking in a queue |
| 2 | **in front**, same wall | `17L` | forward is where the cabin is emptier (people fill from their own row forward) |
| 3 | **opposite and in front** | `17R` | the two moves combined |
| 4 | **behind**, same wall | `20L` | last: the person has already passed it, and it is the fill direction |

- **The trigger is its own constant**, `STOW_AWAY_UNITS = 4.0` of the bin's 4.5 —
  *not* the map's red (above 3). Red is "getting full", the advice is "a bag will
  not fit": at 4 of 4.5 there is room for a backpack and nothing bigger. The two
  lines moving independently is deliberate, and the client's call if they should
  coincide.
- **Naming (temporary):** the seat rows the compartment hangs over, then the wall —
  `1-2L`, `3-4R`. A compartment beside an exit band hangs over **one** seat row, so
  its span is that row (`17L`, `20R`) rather than counting the band as passengers;
  `bin_name()` is the one place to change if `17-17L` is preferred.
- **Every branch still points somewhere.** If all four candidates are nearly full
  the emptiest is named anyway (`packed`); a row with no compartment over it (an
  odd cabin's last row) says so (`none`) instead of inventing a name.
- **Re-painted live.** `stow_advice()` returns the whole payload — copy included —
  and the scan response carries it, so `app.js` re-paints the block after a bag is
  recorded (which changes the load in the passenger's own bin). Copy lives on the
  server, one place, like `baggage_text`.
- **No new colour.** The block's left edge inherits the ramp's single `--bin-ink`,
  and the level is a word (`room`/`filling`/`full`, from `BIN_LEVEL_TEXT`, shared
  with the board's legend) as well as a colour.

**Verified:** 137 tests green, `brand/check.py pitch-demo` clean (11 files), and a
**real headless click** through a temporary same-origin harness — own bin at 3.75
units, two trolleys added, Confirm pressed, and the block re-painted from `own`
`1-2L` to `redirect` `1-2R` with "The bin above you (1-2L) is nearly full — stow
opposite your seat". Screenshot of the redirect state on a 430×900 phone frame.

## 17. Build log — predicted is stowed (2026-11-10)

**Asked for, right after R19:** "if a bag is redirected to another bin, then the
corresponding bin will get the passenger's carry-on. since this demo is compressed,
predicted in a bin = stowed in that bin".

**R20.** *One plan, two surfaces: the loads ARE the stowage.*

- **`stow_plan(passengers, rows)` is the model**, and it is sequential. Bags are
  placed one at a time in **arrival order — first come, first served** (Davis,
  2026-11-10, superseding the rear-first order). Each bag walks `stow_order()`:
  own → opposite → in front → opposite and in front → behind. `build_state()` and
  the control table both hand the plan their bags by **scan row id**, so the order
  is the order the bags were checked in; because the walk is a left fold over that
  order, appending a bag can never move one already placed — a full bin reroutes
  the **new** bag around the bags already there.
- **A compartment qualifies when it is not past `STOW_AWAY_UNITS` (4.0) *and* can
  still hold the bag** (`BIN_CAPACITY` 4.5). The occupancy line wins over the fit
  (a handbag at 4.25 is moved even though it would fit), and the fit rule is what
  keeps the picture real: nothing is stowed where it does not fit unless **every**
  candidate is full, in which case the emptiest takes it and reads `is-over` (state
  `packed`). That is now the *only* way a compartment goes over capacity.
- **The advice and the colours are one computation.** `build_state()` builds the
  plan once (`advice=True` also returns the per-seat payload, for the card and the
  scan response); `/api/state` ships `bins` only. `stow_for()` prefers the plan's
  own decision for a seat (that bag is in the picture) and falls back to
  `stow_advice()` — the same walk with no bag size — for a passenger who has not
  chosen yet. So a card can never quote a load the board is not colouring.
- **The copy says where the bag *is*** when it has been declared ("Your bag is in
  the bin above your seat", "The bin above you (1-2L) was full — your bag is
  opposite your seat") and suggests when it has not ("stow opposite your seat").
- **`declared` became `bags`** in the compartment record (the tooltip reads "4 of
  4.5 units · 4 items"): a compartment can now hold bags belonging to seats it does
  not hang over, so "n of 6 seats declared" was no longer true. `data-declared` on
  the element is `data-bags`.
- **The exit-band bays are no longer red-proof** (this supersedes the note in §15):
  `17+b18` tops out at 3.0 from its own three seats, but a neighbour redirected
  "in front" can push it over.
- **Invalidation:** void if the demo ever separates the prediction from the
  stowage (e.g. a passenger who ignores the card), because then `bins` would have
  to become "predicted" again and the card's statement form would be a lie.
- **Amended 2026-11-10 (first come, first served):** the original order was
  rear rows first; it was replaced because a later check-in at a rear seat was
  re-sorted ahead of bags already on the board and silently re-stowed them (a
  card could flip `1-2L` → `1-2R` when someone else checked in). Arrival order
  now comes from the scan row id, so an existing bag is never moved. Void if the
  demo ever wants a seat-based boarding order back — the redirect targets move
  with it.

**Copy pass (same day, requested):** the ticket line was rewritten — "The bin
above you (1-2L) was full — your bag is opposite your seat · 2 of 4.5 units there"
was anti-slop and ungrammatical. Now: **two sentences and a state word**, no unit
figure (the load is the board's business, and a person holding a bag cannot use
"2 of 4.5 units"), no em dash, no middot fragments, and the direction is short
enough to finish *"…in the one ___"* (opposite / in front / opposite and in front /
behind you). The heading is the one instruction, *Stow your bag in:*, in both
states. Examples:
*"Your bag is in the compartment above your seat."* /
*"The bin above your seat was full, so your bag is in the one opposite."* /
*"Every bin near your seat was full. Your bag is in the emptiest one, in front."*
A test pins every direction, the packed line, and that the block contains no
"unit", no "4.5", no em dash and no middot; the board's tooltip keeps the figure.

**Verified:** 162 tests green, `brand/check.py pitch-demo` clean,
`stow_plan()` spot-checked on the real 40-row cabin (5 trolleys in the `1-2` bay →
four stay at 4.0 red, the fifth is in `1-2R`), live unit restarted.

## 18. Build log — reset clears the bags, and the words follow (2026-11-10)

**Asked for:** "reset board should reset the bins and baggage too."

**What was actually wrong (R21).** The *data* already reset: a choice lives on the
scan row, so `DELETE FROM scans` took `bag_units`/`bag_mix`, and `bins`,
`stow_plan()` and the Baggage column are all projections of that. What did **not**
reset was everything the reset could not see:

1. `passengers.baggage_volume_l` — the one bag figure no scan owns, printed in the
   Baggage column and on the ticket, left standing by a reset. `reset_world()`
   now nulls it in the same transaction (D7a).
2. **The legend and the seatmap's accessible name.** `bin_legend_html()` and
   `cabin_html()`'s `aria-label` were rendered once, server-side: 3 counts, "N
   compartments over capacity", "overhead bins 30 with room, 1 filling, 11 full".
   `board.js` repainted the rectangles every second but never touched the copy, so
   a reset produced an empty green cabin with the words still claiming eleven full
   compartments — and a screen reader got the stale picture too.
3. **The confirm dialogue** promised the opposite ("Passengers, tickets and
   baggage stay"), which is how a reset that keeps baggage would read.

**The shape.** `bin_counts()` and `bin_over_label()` are the one place the counts
and the words are computed; `cabin_meta()` turns them into `{levels, over,
over_label, label}`; `cabin_html()`, `bin_legend_html()` and `/api/state`'s new
`cabin` all read from it. The figure is its own element (`<b class="bin-over-n">`)
with the words in a sibling span, so the script rewrites numbers without parsing
sentences — the same contract as `baggage_text` (server writes the copy, page
mirrors it) and `data.bins` (server decides the level, page applies it).

`reset_world(conn)` is now the single operation behind both doors: the board's
`POST /api/reset` (behind the token) and the console's Clear. The console's
`affected` count still means "scan rows cleared".

**Verified:** 167 tests green (six new: the orphan bag, the console door, the
legend markup at zero and at one, the payload copy, and that `board.js` reaches
for exactly the elements the page renders), `brand/check.py pitch-demo` clean,
`node --check static/board.js` parses, and on the live site a scan of
`bag-roller ×3 + bag-duffel ×3` (5.25 units, one compartment over) followed by
`POST /api/reset` returns `/api/state` to `over: 0`, all-green, "nothing over
capacity", with no passenger holding a bag.

**Not done, on purpose:** an *already open* boarding pass is not re-rendered by a
reset — the card has no poll (R11: a card never records on load, so it has no
reason to fetch). Rescanning the pass, or reloading, shows the cleared card. Ask
if a reset should also reach open cards (a visibility-triggered refresh, or a
2-second poll) — that is a product call, not a bug.

## 19. Build log — red re-based to the advice line (2026-11-10)

**Asked for:** "red = 4.0+". The board's ramp and the stowage trigger had drifted
apart: `bin_level()` painted a bay red above **3** units while `STOW_AWAY_UNITS`
only sent a passenger elsewhere at **4.0**. For any bay in the `(3, 4)` band the
projector showed red and the card still said *"stow it above your seat"* — a
visible contradiction on stage, and the one found in review.

**R22.** *Red begins exactly at `STOW_AWAY_UNITS`.* `bin_level()` is now
`units >= STOW_AWAY_UNITS` — the same constant the redirect reads, so the colour
and the advice cannot disagree again. Yellow now spans `(1, 4)` and the legend
labels follow: **0–1 unit**, **1–4 units**, **4 units and over**. `is-over` is
still `> BIN_CAPACITY` (4.5).

- **What did not change:** the redirect order, the fit rule, `packed`, the
  per-wall model, `is-over`, and every piece of card copy. A 4.0 bay was already
  the redirect line; only the *board* moved onto it.
- **The exit-band compartments are unaffected:** `17+b18` and `b20+20` still top
  out at 3.0 from their own three seats — now yellow, and red is still out of
  reach from those seats alone (a redirected neighbour can push one over; that
  part of §17 stands).
- **Invalidation:** §15/§16's "red above 3" and "deliberately not the map's red"
  are superseded here. Void if the ramp is decoupled from the advice again (then
  the board may carry a separate "getting full" warning below the line, and this
  entry should say so).

**Verified:** `bin_level()` pinned at every boundary (1 green · 3 yellow · 3.75
yellow · 4 red · 5 red+over), the legend test and the two-walls render updated to
the new labels and a 4.0 red bay, and the suite green.

## 19. Build log — the poll can repaint, and the legend goes (2026-11-10)

**Asked for:** fix the compartment-key defect, delete the legend, stop the advice
being built for every passenger, and make sure the page refreshes live.

**R23.** *One compartment, one key, and the payload is built where it is read.*

| Change | Why |
|---|---|
| `data-bin` is the **anchor row**; the two-slot label moved to `data-slot` | The rectangle was keyed by its label (`1+2`) while `bins` keyed it by the anchor (`1`). `board.js` built `1\|left` against a page that said `1+2\|left`, so `if (!el) return` fired for all 42 bins and **no compartment ever repainted** — the colours froze at page load while the seat squares and the legend's numbers ticked |
| **The legend is deleted** (`bin_legend_html()`, `bin_over_label()`, its CSS and its `board.js` plumbing) | Requested. Each rectangle keeps its own load in its `title`, and `cabin_meta()` now returns only the seatmap's `aria-label`, so the counts are still read out — once, where a screen reader gets them |
| `stow_plan()` returns **decisions**, not payloads; `stow_for()` builds the single payload a card needs | The board polls once a second and never wanted the copy: `build_state(advice=True)` built a two-sentence payload for every declared bag and shipped them nowhere. `ctl_stow_map()` is the one caller that still wants them all, and it builds them itself |
| `Cache-Control: no-store` on `/api/state` | A board left open on the projector must never be served a cached cabin, by a browser or by a proxy |

- **What did not change:** the ramp (R22), the redirect order, the fit rule, the
  arrival-order walk, the card's copy, and the `packed` path. The advice a card
  prints is byte-identical to what it was: the payload moved, it did not change.
- **Supersedes:** §17's "`title` + a legend" row and §18's legend labels — there is
  no legend left to label. The words live in the `aria-label`.
- **Invalidation:** void if a legend comes back (then `cabin_meta()` should return
  its counts again and the no-legend test should go), or if a compartment ever needs
  a name that is not derived from its anchor row.

**Verified end to end, in a real browser.** A temporary same-origin harness loaded
`/bggo/live?view=seats`, POSTed a three-trolley scan, waited for two polls and read
the DOM back: `cabin-bin load-green` → `cabin-bin load-yellow`, `data-load` `1` →
`3`, the `title` rewritten, and the `aria-label` recounted (41 with room, 1
filling). Before the fix the same harness left `data-load` at its page-load value.
The join is now pinned by `test_every_bin_the_payload_sends_is_a_rectangle_the_poll_can_find`,
which compares the keys the two functions write. 175 tests green, `brand/check.py
pitch-demo` clean.

## 20. Build log — zones: two compartments merge into one (2026-11-10)

**Asked for:** "make the map less granular — instead of each compartment, it shows
zones composed of 2 compartments each, i.e. merge 1-2L + 3-4L becomes 1-4L".

**R24.** *A zone is two compartments, and the zone is the unit of the model.*

- **Geometry.** `cabin_bin_rows()` now groups **four seat rows** (`1-4`, `5-8`, …)
  instead of consuming two bin slots. `cabin_bins()` draws one rectangle a zone and
  returns its slot count; the exit bands are anatomy *inside* a zone, so the
  `17-20` zone spans six row-pitches (`17+b18+18+19+b20+20`) and every other zone
  four. `top` is always `0` (no `reaches-up`), the `over-band` class is gone, and
  `.cabin-bin` heights on `--bin-slots`.
- **Model.** `BIN_CAPACITY` is now **9.0** (two 4.5-unit compartments, via
  `COMPARTMENT_CAPACITY`), `STOW_AWAY_UNITS` **8.0**, green through **2**
  (`BIN_GREEN_UNITS`). Names are `1-4L`, `5-8R` (first row–last row); a four-row
  zone no longer reads `1-2-3-4L`.
- **What did not change:** the arrival-order fold, the redirect order
  (own → opposite → in front → opposite-ahead → behind), the fit rule, `packed`,
  the card's copy structure, and the `data-bin` = anchor-row join `board.js` uses.
- **Amends:** R18/R20/R22's per-compartment geometry, the "exit bins cannot reach
  red" note, and every `4.5`/`4.0`/`1–4` figure in §14/§15/§17/§18. The 40-row
  cabin is **10 zones a wall** (was 21 compartments).
- **The exit zone is uniform by decision (B1):** `cabin_bin_rows()` counts seat
  rows, not slots, so `17-20` holds 9 units like any other zone even though it
  physically spans more bin length.
- **Invalidation:** void if the client wants a different group size — it is
  `ZONE_ROWS = 4` and the four-row band in `cabin_bin_rows()` — or a mix of
  two- and three-compartment bands.

**Verified:** 175 tests green, `brand/check.py pitch-demo` clean, service
restarted; the rendered HTML has 20 rectangles (10 a wall), 18 at `--bin-slots:4`
and the two exit zones at `:6`, and a 1920×1080 headless screenshot shows the
merged map with the taller exit zone. README's map and prediction sections are
rewritten to match. **Copy pass (same day, requested):** the card's green pill
reads **`mostly empty`** (was `room`); the seatmap's `aria-label` still says
"with room".

## 21. Build log — the card gives an instruction, and the name says Row (2026-11-11)

**Asked for:** a zone reads `Row 5-8L`, not `5-8L`; every passenger line is an
instruction (*Stow your bag in the zone above your seat*, not *Your bag is in the
zone above your seat*); and the redirected line ends *…please use the bin across the
aisle* rather than *…stow your bag in the zone opposite*.

**R25.** *The passenger copy is a verb, and the name is words.*

| Change | Why |
|---|---|
| `bin_name()` returns `Row 5-8L` | The figure alone (`5-8L`) reads like a code, and a person has to find it on a card. The word rides inside the name, so every surface that prints a zone — the card's figure, the console's Stowed column, the log line, the map's spoken label — got it from the one function |
| Every sentence is imperative | Someone holding a bag wants to be told what to do; a status sentence (*Your bag is in…*) makes them work out the instruction. The tense no longer depends on whether a bag is on file — `declared` now only decides *full* vs *nearly full* |
| The heading is *Where to put your bag* | The sentence starts *Stow your bag in…*, and the old heading *Stow your bag in:* said it twice in a row |
| `STOW_DIRECTION["opposite"]` is `across the aisle` | Written the way a person says it, and asked for by name. *opposite and in front of you* keeps its compass word |
| The last-resort line uses the same verb | The card now has exactly two verbs: *Stow your bag in…* for its own zone, *please use the bin…* for anywhere else |

- **The shape did not change:** two sentences and a state word, never the arithmetic —
  the zone's load in units stays the board's business. `stow_payload()` is still the
  only place any of it is written.
- **Supersedes** §20's copy table. The split is now *zone* for your own and *bin* for
  the one you are sent to, which is how the customer wrote it.
- **Invalidation:** void if the copy is ever composed in a second place — a second
  author means the card and the console can disagree, which is what R20 exists to
  prevent.

**Verified:** 229 tests green. `bin_name()` is pinned as `Row 1-4L` / `Row 5-8R` by
`test_a_zone_is_named_by_its_row_span_and_wall`; the sentence matrix is pinned by
exact-string assertions, including all four redirect directions; and
`test_the_stowage_copy_moves_you_and_does_not_do_arithmetic` fails the build if the
block ever contains `unit`, `9`, `—` or `·`. The brand gate is clean, and a card was
read back from the running service for both the own and the redirected case.

**R25 addendum — the whole card is unit-free (2026-11-11).** The ruling above fixed
the stowage sentence; the same rule now runs over the rest of the passenger surface.
The bag fact reads *Small trolley case* (no *2 units*), the status line reads *Checked
in. Small trolley case.*, the picker's running total is a **count** (*2 items*), the
picker tiles no longer print *0.25 units each*, and the card's own cabin map carries
`Row 1-4L · full` where the board carries `Zone above 1A–4C · 8 of 9 units · 8 items`.

- **One flag, one drawing:** `cabin_html(units_in_titles=False)` is the only difference
  between the two maps, so the card still cannot drift from the board — and
  `test_the_ticket_and_the_board_are_the_same_drawing` now asserts the titles *differ*
  in exactly that way (the board's says `unit`, the card's does not, and both name the
  same zone).
- **What did not change:** the board's tooltip, the log strip's *1 unit of carry-on*,
  the console's Baggage column and unit totals, and every payload field. `data-units`
  stays on the tiles because the console's dialog still derives its own unit total.
- **Knock-on:** the console shares the picker markup, so its tiles lost the per-bag
  *N units each* line too; its total still shows units. Flagged in `TODO.md`.
- **Invalidation:** void if a passenger-facing surface ever needs a figure again (e.g.
  a "your bag is 0.75 of the locker" pitch beat) — then it belongs in that beat's copy,
  not back in the card's chrome.

**Verified:** 229 tests green, gate clean, the page's rendered text carries no `unit`
(only the icon provenance comment in the markup, which is not painted), and the live
card was read back for the checked-in, redirected and empty-picker cases.
