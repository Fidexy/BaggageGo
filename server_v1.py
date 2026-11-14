#!/usr/bin/env python3
"""BaggageGO stage 1 server — the gate.

Deliverables (see STAGE1_PLAN.md §0):
  1.1  GET /bggo/live      live board, open (no token — see R7)
  1.2  GET /bggo/<CODE>    the page a printed ticket opens
  1.3  the board's highlight rules, boarding feed and reset button

Decisions that bend the plan, and why:
  * ``GET /bggo/live`` is **open** — no token. The board is a presenter bookmark,
    and ``/api/state`` (which carries the same 20 names and seats) was never
    gated, so a token on the page protected nothing. The token is still minted
    once into ``settings.board_token`` and kept for the 1.3 reset header.
  * ``GET /bggo/<CODE>`` is rendered server-side and works with JS off. The
    scan is *recorded* by a ``POST /bggo/api/scan`` fired by the page, so a
    link-preview fetch (iMessage, WhatsApp, Slack) or a bot cannot inflate the
    scan counts.
  * The plan names the API ``/api/...`` but deploys a single NPM location
    ``/bggo/``. The router is mounted twice — ``/bggo/api/...`` (canonical,
    what the HTML uses) and ``/api/...`` (the plan's literal paths, kept
    working in case two proxy locations get configured).
  * 1.3 is built: most-recent scan pinned to the top and accented, earlier scans
    stay lit, `×N` on repeats, the boarding feed in the corner, and a Reset button whose
    `POST /bggo/api/reset` needs the `X-Board-Token` header. The token is
    embedded in the board page, which is fine: it is not a login, it is CSRF
    protection (a cross-site form cannot set a custom header, and there is no
    CORS here). It never appears on the audience page.

Invariants: this module never writes ``passengers``; it only ever inserts into
``scans`` and ``settings``. ``passenger_codes.txt`` is read-only forever.

Run:  uvicorn server_v1:app --host 127.0.0.1 --port 8091
"""

from __future__ import annotations

import html
import itertools
import json
import os
import random
import re
import secrets
import sqlite3
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("BGGO_DB", HERE / "passengers.db"))
STATIC_DIR = HERE / "static"
ICON_DIR = STATIC_DIR / "icons"
# The brand kit lives at ../brand in the monorepo (brand.json -> build.py ->
# brand.css) and this app serves the kit rather than copying it. The demo has to
# stand alone though -- a container build, a folder copied out of the repo, no
# sibling checkout -- so it now carries a vendored, byte-identical snapshot at
# ./brand/. test_brand_vendored.py fails the moment ./brand/ drifts from a
# present ../brand/, so the snapshot cannot silently go stale, and
# BGGO_BRAND_DIR overrides both when you want the live kit instead.
BRAND_DIR = Path(os.environ.get("BGGO_BRAND_DIR", HERE / "brand"))
BRAND_CSS = BRAND_DIR / "brand.css"
BRAND_FONT = BRAND_DIR / "fonts" / "Montserrat-var.ttf"
BRAND_ICONS = BRAND_DIR / "icons" / "svg"

BASE = "/bggo"
LIVE_PATH = f"{BASE}/live"
SID_COOKIE = "sid"
SID_MAX_AGE = 60 * 60 * 24 * 365

# D9: the ticket QR encodes "{PUBLIC_BASE}{BASE}/<CODE>". Changing either half
# after the first print invalidates every ticket on paper. Keep them here so
# there is exactly one place to look before printing.
PUBLIC_BASE = os.environ.get("BGGO_PUBLIC_BASE", "https://sophon1.dedyn.io")
PORT = int(os.environ.get("BGGO_PORT", "8091"))
HKT = timezone(timedelta(hours=8))  # display only; every timestamp is stored UTC

# The flight on paper — the facts the ticket page's flight strip repeats.
# The passes are printed and locked (boarding-pass/real-tickets.txt), so the
# page must agree with them rather than the other way round: a judge holding a
# pass reads UO 628 / 15Nov / 204 / 1910 off both. Source of truth is
# boarding-pass/template.html; a change there means a change here, and a new
# print run. Cities (Hong Kong → Tokyo-Haneda) live on the paper; the strip
# uses the codes an airline app uses.
FLIGHT = {
    "no": "UO 628",
    "date": "Sun 15 Nov",
    "from": "Hong Kong",
    "from_code": "HKG",
    "to": "Tokyo-Haneda",
    "to_code": "HND",
    "departs": "19:55",
    "gate": "204",
    "boards": "19:10",
}

# The four bags the picker offers (stage 2 starts here). Volume is in abstract
# "units" — one definition, used by the picker, the boarding pass and the board,
# so the three can never disagree. A real litre figure per bag is a later
# decision; until then nothing converts these to litres.
#
# Named for the specific bag, not an airline category: everything here is carried
# on, so "checked bag" would be meaningless, and the fabric differs — a handbag is
# not a duffel.

# How many of one kind anyone could plausibly carry on. A cap, not a feature,
# and each bag may lower it: a rigid trolley takes the space a soft bag does not.
MAX_PER_BAG = 3

Bag = namedtuple("Bag", "units name icon limit", defaults=(MAX_PER_BAG,))
# The ladder runs 0.25 → 1.0, one step per bag: a handbag gives most of a bin
# back, a rigid case costs a whole one. The soft duffel sits *below* the (small)
# rigid trolley because it can be squashed into the space it is handed — that is
# the whole point of costing a bag in units rather than in centimetres.
#
# `bag-roller` stays the trolley's key. Keys are storage identifiers: they appear
# verbatim inside the `bag_mix` JSON of every existing scan row, so a key is
# renamed only alongside a migration of that data. Labels are free to move.
BAGS = (
    Bag(0.25, "Handbag", "bag-handbag"),
    Bag(0.5, "Backpack", "bag-backpack"),
    Bag(0.75, "Duffel bag", "bag-duffel"),
    # Two cases is already two whole compartments' worth of hard luggage, and
    # a third is not something one person wheels to the gate (Davis, 2026-11-11).
    Bag(1.0, "Small trolley case", "bag-roller", limit=2),
)
BAG_UNITS = tuple(b.units for b in BAGS)
BAG_ICONS = tuple(b.icon for b in BAGS)

# The other cap: the **total** number of items, however they are spread across
# the four bags. The demo asks "what are you carrying on", and three pieces is
# what one person plausibly shoulders to the gate. Per-bag caps (`Bag.limit`)
# still apply on top — the trolley is 2 — so this is an and, not an either.
MAX_ITEMS = 3

# Every bag list the caps allow, with its total in units. Once the caps are
# applied there are only a couple of hundred, so enumerate them once: "a random
# load inside this range" then becomes a choice from a finite list instead of a
# rejection loop that can miss a narrow range (or spin).
BAG_COMBOS: tuple[tuple[dict[str, int], float], ...] = tuple(
    (
        {b.icon: n for b, n in zip(BAGS, counts) if n},
        round(sum(b.units * n for b, n in zip(BAGS, counts)), 4),
    )
    for counts in itertools.product(*(range(b.limit + 1) for b in BAGS))
    if any(counts) and sum(counts) <= MAX_ITEMS
)

# The cabin: 3 + 3 lettering, from the seeder's own SEAT_LETTERS. Kept as a
# second copy so the board does not import the script that writes `passengers`;
# a test pins the two together so they cannot drift.
SEAT_LETTERS = "ABCDEF"
SEATS_PER_SIDE = 3
_SEAT_RE = re.compile(r"^(\d{1,2})\s*([A-Za-z])$")

# The cabin's doors. HK Express' A321 carries an entry door at the front, a pair
# of overwing exits and a rear door; on a 40-row cabin that is rows 1, 18/19 and
# 40, clamped by cabin_doors() to the cabin we actually have.
DOOR_ROWS = (1, 18, 19, 40)
# The overwing pair gets air around it: the supplied seat map spaces the exit rows
# off from their neighbours, and that spacing is what makes them read as exits
# rather than as two more rows. The gap itself is `--exit-gap` in style.css; this
# side only says which rows and how many gaps that is.
OVERWING_ROWS = (18, 19)

# One overhead compartment holds 4.5 units, in the same abstract units the picker
# prices bags in (`BAGS`). The map **merges two compartments into a four-row
# zone** (decision 2026-11-10), and the zone is the unit the model stows into, so
# the capacity below is deliberately two compartments' worth. "Bin" in this file
# now means the merged zone — the one rectangle the map draws and the card names.
COMPARTMENT_CAPACITY = 4.5
BIN_CAPACITY = COMPARTMENT_CAPACITY * 2          # 9.0 — one merged bin = one zone

# When the zone above a passenger reaches *this* load, the boarding pass stops
# pointing at it — and it is also where the board's red begins. The two lines are
# the same line by decision (2026-11-10): a red zone is exactly one the advice
# sends past, so the colour and the redirect can never disagree. At 8 of 9 there
# is room for a backpack and nothing larger, so the person is sent looking.
STOW_AWAY_UNITS = 8.0

# How many boardings the live log carries. A log is read at the *bottom*, so the
# payload holds the last N and the page renders the last N: the start of the
# boarding scrolls out of the panel instead of growing the poll forever.
BOARDING_LOG_LIMIT = 24

# The top of green, in the same scale: green through two units (one compartment's
# worth), yellow above it.
BIN_GREEN_UNITS = 2.0

# How many seat rows one merged zone covers. Two compartments, four rows.
ZONE_ROWS = 4


def board_url(base: str = PUBLIC_BASE) -> str:
    return f"{base}{BASE}/live"


def asset_url(path: Path, url_path: str) -> str:
    """`<url>?v=<mtime>` — the HTML is `no-store` and always references the
    current file, so a stale stylesheet or script cannot survive a code tweak.

    This is not gold-plating: without it `StaticFiles` sends only `last-modified`
    and an ETag, so a browser heuristically caches `style.css`/`board.js` and a
    fresh page renders against the previous UI (found live 2026-11-07 — the seat
    map fell out below the list and the view links looked dead). The server also
    sends `Cache-Control: no-cache` for these paths; the version is the belt.
    """
    try:
        version = path.stat().st_mtime_ns
    except OSError:
        version = 0
    return f"{url_path}?v={version}"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL UNIQUE,
    known         INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    hits          INTEGER NOT NULL DEFAULT 1,
    session_id    TEXT,
    ua            TEXT,
    bag_units     REAL,
    bag_mix       TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ----------------------------------------------------------------- plumbing

def utcnow_iso() -> str:
    # microsecond precision on purpose: two tickets scanned inside the same
    # second must still be orderable, or "most recent" falls back to seat order.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def parse_iso(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _hkt_clock(moment: datetime) -> str:
    return moment.astimezone(HKT).strftime("%H:%M:%S")


def age_text(last_seen: str | None, *, now: datetime | None = None) -> str:
    """Relative under a minute, HKT wall clock after that (§3.2)."""
    if not last_seen:
        return "—"
    seen = parse_iso(last_seen)
    seconds = int(((now or datetime.now(timezone.utc)) - seen).total_seconds())
    return f"{max(0, seconds)}s ago" if seconds < 60 else _hkt_clock(seen)


def repeat_hits(counters: dict) -> int:
    """§3.2 calls this "repeat hits": scans beyond each ticket's first."""
    return max(0, counters["hits"] - counters["scanned"])


def unit_text(units: float) -> str:
    return f"{units:g} unit" + ("" if units == 1 else "s")


def valid_units(raw) -> float | None:
    """The picker offers exactly four bags; anything else is not a bag."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    for bag in BAGS:
        if value == bag.units:
            return bag.units
    return None


def bag_for(units) -> Bag | None:
    """The bag whose unit size these are — the single-bag shorthand's lookup."""
    value = valid_units(units)
    return next((b for b in BAGS if b.units == value), None) if value else None


def parse_mix(counts: dict | None, *, limits: bool = True) -> dict[str, int] | None:
    """Validate a {bag: how many} mapping. None if it is not a bag list at all.

    An all-zero map is not a choice: confirming nothing must not check anyone in.

    `limits=False` is for a choice already on file: caps can be tightened, and a
    scan made under the old ones is history that must still render (a card that
    went blank because a number became unprintable would be worse than showing
    it). New input always goes through the caps.

    Two caps apply to new input: `Bag.limit` per bag, and `MAX_ITEMS` in total,
    so four handbags is as much a no as three trolleys.
    """
    if not counts or any(key not in BAG_ICONS for key in counts):
        return None
    clean: dict[str, int] = {}
    for bag in BAGS:
        if bag.icon not in counts:
            continue
        try:
            qty = int(counts[bag.icon])
        except (TypeError, ValueError):
            return None
        if qty < 0 or (limits and qty > bag.limit):
            return None
        if qty:
            clean[bag.icon] = qty
    if limits and sum(clean.values()) > MAX_ITEMS:
        return None
    return clean or None


def no_bags_chosen(counts: dict | None) -> bool:
    """All zero (or absent): a normal mis-tap, not a malformed request."""
    try:
        return not any(int(v) for v in (counts or {}).values())
    except (TypeError, ValueError):
        return False


def scan_mix(raw: str | None) -> dict | None:
    """A stored choice, parsed. Never raises — a hand-edited row is not worth a 500."""
    if not raw:
        return None
    try:
        return parse_mix(json.loads(raw), limits=False)
    except (TypeError, ValueError):
        return None


def mix_units(mix: dict | None) -> float | None:
    """The whole point of the steppers: quantities add up into units."""
    if not mix:
        return None
    return sum(b.units * mix[b.icon] for b in BAGS if mix.get(b.icon))


def mix_parts(mix: dict | None) -> list[dict]:
    return [
        {"key": b.icon, "name": b.name, "units": b.units, "qty": mix[b.icon]}
        for b in BAGS
        if mix and mix.get(b.icon)
    ]


def mix_text(mix: dict | None) -> str:
    """"Handbag, Backpack ×2" — the ×1 dropped, because it carries no information."""
    return ", ".join(
        p["name"] if p["qty"] == 1 else f"{p['name']} ×{p['qty']}" for p in mix_parts(mix)
    )


def bag_summary(mix: dict | None) -> dict | None:
    """The API's `bag` object and the card's facts row, from one place."""
    units = mix_units(mix)
    if units is None:
        return None
    return {
        "units": units,
        "text": unit_text(units),
        "mix": mix_parts(mix),
        "mix_text": mix_text(mix),
    }


def baggage_text(bag: dict | None, litres: float | None) -> str:
    """What the board's Baggage column shows. One rule, applied on the server —
    and sent in /api/state so board.js never re-derives it."""
    if bag:
        return bag["text"]
    return f"{litres:g} L" if litres is not None else "—"


def parse_seat(raw) -> tuple[int, str] | None:
    """A seat as (row, letter), or None. Unreadable seats are ignored, never
    fatal: the db is hand-editable and one bad row must not break the board."""
    match = _SEAT_RE.match(str(raw).strip()) if raw is not None else None
    if not match:
        return None
    row, letter = int(match.group(1)), match.group(2).upper()
    return (row, letter) if row >= 1 and letter in SEAT_LETTERS else None


def most_recent_code(passengers: list[dict]) -> str | None:
    """The code of the newest scan, or None (§3.2). One definition, shared by
    the list's top pin and the seat map's accent, so the two cannot disagree."""
    recent = max(
        (p for p in passengers if p["scanned"] and p["last_seen"]),
        key=lambda p: parse_iso(p["last_seen"]),
        default=None,
    )
    return recent["code"] if recent else None


def seat_map(passengers: list[dict], recent_code: str | None = None) -> dict:
    """The cabin: every seat in the allocated range, row-major from the front
    (1A, 1B, …, 2A …), each cell already carrying its display state. This is the
    one source shared by the server-rendered grid and the /api/state payload, so
    the projector and the poll cannot disagree.

    States are waiting (dim) and scanned (lit). A seat with no allocated
    passenger is waiting — the board is about seats, and who owns one is the
    other view's job.

    `recent_code` still accents the newest seat if a caller passes one, and no
    caller does: the newest check-in is marked on the **list** (the row pinned to
    the top), not on the cabin. The seat map answers "where are the seats taken"
    and a second loud square in a picture whose one loud mark is the passenger's
    own seat reads as *selected* (requested 2026-11-10). The parameter and the
    `recent` field stay so the payload's shape does not move for one request.
    """
    occupied = {p["seat_number"]: p for p in passengers if p["seat_number"]}
    rows = max((parse_seat(seat)[0] for seat in occupied if parse_seat(seat)), default=0)
    cells = []
    for row in range(1, rows + 1):
        for index, letter in enumerate(SEAT_LETTERS):
            seat = f"{row}{letter}"
            p = occupied.get(seat)
            recent = bool(p and p["code"] == recent_code)
            scanned = bool(p and p["scanned"])
            cells.append(
                {
                    "seat": seat,
                    "row": row,
                    "letter": letter,
                    "code": p["code"] if p else None,
                    "scanned": scanned,
                    "recent": recent,
                    "state": "recent" if recent else "scanned" if scanned else "waiting",
                    "aisle_after": index == SEATS_PER_SIDE - 1,
                }
            )
    return {"rows": rows, "letters": list(SEAT_LETTERS), "side": SEATS_PER_SIDE, "cells": cells}


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def normalize_code(raw: str) -> str:
    """Printed codes are upper-case; scanning apps may hand back anything."""
    return raw.strip().upper()


def coarse_ua(ua: str | None) -> str | None:
    if not ua:
        return None
    low = ua.lower()
    if "iphone" in low:
        return "iPhone"
    if "android" in low:
        return "Android"
    return "Other"


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """CREATE TABLE IF NOT EXISTS cannot add a column to a table that already
    exists, and the live db predates the picker. A rebuild would wipe the board
    state, so migrate in place."""
    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in have:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db(db_path: Path) -> None:
    """Create the additive tables, migrate, and lock the board token. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _add_column_if_missing(conn, "scans", "bag_units", "REAL")
        _add_column_if_missing(conn, "scans", "bag_mix", "TEXT")
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('board_token', ?)",
            (secrets.token_urlsafe(24),),
        )
        # A distinct token for the control page. Deliberately NOT the board
        # token: the board token is embedded in /bggo/live, which is open, so it
        # is a public value. This one is only ever served behind a matching
        # ?k=, so the control URL is a real (bookmarkable) secret.
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('controls_token', ?)",
            (secrets.token_urlsafe(24),),
        )
        conn.commit()
    finally:
        conn.close()


def get_board_token(db_path: Path = DB_PATH) -> str:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'board_token'").fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(f"settings.board_token missing from {db_path} — run init_db()")
    return row["value"]


def get_controls_token(db_path: Path = DB_PATH) -> str:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'controls_token'").fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(f"settings.controls_token missing from {db_path} — run init_db()")
    return row["value"]


def controls_url(db_path: Path = DB_PATH, base: str = PUBLIC_BASE) -> str:
    """The one bookmark the operator needs. The token rides in ?k= because a
    control surface must not be reachable by guessing /bggo/controls."""
    return f"{base}{BASE}/controls?k={get_controls_token(db_path)}"


def sid_from_request(request: Request) -> tuple[str, bool]:
    """Return (session id, needs_to_be_set). One sid per response, always."""
    sid = request.cookies.get(SID_COOKIE)
    if sid and len(sid) <= 64:
        return sid, False
    return secrets.token_urlsafe(16), True


def set_sid_cookie(response, sid: str) -> None:
    """httpOnly and path-scoped to /bggo — the host is shared with other apps."""
    response.set_cookie(
        SID_COOKIE, sid, max_age=SID_MAX_AGE, path=BASE, httponly=True, samesite="lax"
    )


# -------------------------------------------------------------------- data

def fetch_passenger(conn: sqlite3.Connection, code: str) -> dict | None:
    """An allocated ticket only: slots 21..240 have no name (plan D6)."""
    row = conn.execute(
        "SELECT code, full_name, seat_number, baggage_volume_l FROM passengers "
        "WHERE code = ? AND full_name IS NOT NULL",
        (code,),
    ).fetchone()
    return dict(row) if row else None


def fetch_scan(conn: sqlite3.Connection, code: str) -> dict | None:
    row = conn.execute(
        "SELECT hits, last_seen_at, bag_units, bag_mix FROM scans WHERE code = ?", (code,)
    ).fetchone()
    return dict(row) if row else None


def record_scan(conn: sqlite3.Connection, code: str, known: bool, sid: str | None,
                ua: str | None, mix: dict | None = None) -> None:
    """One row per ticket (plan D5). A re-scan is activity, not a second person.

    `mix` is optional on purpose and COALESCEd on update: an operator re-scan that
    carries no bag must not erase the passenger's choice. The units are stored
    beside the counts rather than derived at read time, because every surface
    displays the total — but they are computed here, from the counts, so the two
    can never disagree.
    """
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO scans (code, known, first_seen_at, last_seen_at, hits, session_id, ua,
                           bag_units, bag_mix)
        VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            known         = excluded.known,
            last_seen_at  = excluded.last_seen_at,
            hits          = scans.hits + 1,
            session_id    = excluded.session_id,
            ua            = excluded.ua,
            bag_units     = COALESCE(excluded.bag_units, scans.bag_units),
            bag_mix       = COALESCE(excluded.bag_mix, scans.bag_mix)
        """,
        (
            code,
            1 if known else 0,
            now,
            now,
            sid,
            ua,
            mix_units(mix),
            json.dumps(mix, sort_keys=True) if mix else None,
        ),
    )


# ------------------------------------------------- the control surface's data
# The control page edits the demo's *state*, and the demo's state is the `scans`
# table. Check-in is deliberately idempotent: it creates the row a QR scan would
# have created and never bumps an existing one, so an operator cannot inflate the
# board by clicking twice, and `hits` keeps meaning "the audience scanned it".
# "Most recent" therefore belongs to the last *first* check-in — check out and
# back in if a row needs to move to the top.

def ctl_rows(conn: sqlite3.Connection) -> list[dict]:
    """Every allocated passenger plus their scan row, for the control table."""
    out = []
    for r in conn.execute(
        """
        SELECT p.id, p.code, p.full_name, p.seat_number, p.baggage_volume_l,
               s.id AS scan_id, s.hits, s.last_seen_at, s.bag_mix
        FROM passengers p
        LEFT JOIN scans s ON s.code = p.code
        WHERE p.full_name IS NOT NULL
        ORDER BY p.id
        """
    ):
        mix = scan_mix(r["bag_mix"])
        bag = bag_summary(mix)
        out.append(
            {
                "id": r["id"],
                "scan_id": r["scan_id"],
                "code": r["code"],
                "name": r["full_name"],
                "seat": r["seat_number"],
                "checked_in": r["hits"] is not None,
                "hits": r["hits"] or 0,
                "last_seen": r["last_seen_at"],
                "bag": bag,
                "baggage_text": baggage_text(bag, r["baggage_volume_l"]),
                "mix": mix or {},
                "stow": None,
            }
        )
    # where each declared bag is put, from the one plan the board and the card
    # share — so the console can never disagree with either
    stow = ctl_stow_map(out)
    for p in out:
        advice = stow.get(p["seat"])
        if advice:
            p["stow"] = {
                "bin": advice["bin"],
                "own_bin": advice["own_bin"],
                "side": advice["side"],
                "state": advice["state"],
                "level": advice["level"],
                "note": advice["note"],
            }
    return out


def ctl_stow_map(passengers: list[dict]) -> dict[str, dict]:
    """`stow_plan()` keyed by seat id, for the control table's Stowed column.
    A passenger with no declared bag has no entry — there is nothing to stow."""
    # rows come from every allocated seat, not just the ones with a bag: the cabin
    # is what it is whether or not anyone has declared luggage
    seats = [p["seat"] for p in passengers if p.get("seat")]
    rows = max((parse_seat(s)[0] for s in seats if parse_seat(s)), default=0)
    if not rows:
        return {}
    # same arrival order as `build_state()`: the scan row's id, first come first
    # served, so the console's Stowed column matches the card and the board
    ordered = sorted(
        (p for p in passengers if p.get("seat")),
        key=lambda p: (p.get("scan_id") is None, p.get("scan_id") or 0),
    )
    plan_input = [{"seat_number": p["seat"], "bag": p.get("bag")} for p in ordered]
    bins, decisions = stow_plan(plan_input, rows)
    # the console shows every seat at once, so this is the one caller that wants a
    # payload per passenger: the card wants one, the board poll wants none
    loads = bin_index(bins)
    return {
        seat: stow_payload(rows, seat, **decision, loads=loads)
        for seat, decision in decisions.items()
    }


def ctl_passenger(conn: sqlite3.Connection, code: str) -> dict | None:
    for p in ctl_rows(conn):
        if p["code"] == code:
            return p
    return None


def ctl_checkin(conn: sqlite3.Connection, code: str, mix: dict | None = None) -> None:
    """Create the scan row if it is absent; leave an existing one untouched."""
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO scans (code, known, first_seen_at, last_seen_at, hits, bag_units, bag_mix)
        VALUES (?, 1, ?, ?, 1, ?, ?)
        ON CONFLICT(code) DO NOTHING
        """,
        (code, now, now, mix_units(mix), json.dumps(mix, sort_keys=True) if mix else None),
    )


def ctl_checkout(conn: sqlite3.Connection, code: str) -> None:
    """Un-check-in: delete the row outright, bag choice included. That is what
    the QR flow has no equivalent of — it is a correction, not a scan."""
    conn.execute("DELETE FROM scans WHERE code = ?", (code,))


def ctl_set_bag(conn: sqlite3.Connection, code: str, mix: dict) -> None:
    """Write the picker's choice onto the scan row. Creates the row (i.e. checks
    the passenger in) if they were not on the board, but never moves `hits` or
    `last_seen_at` on an existing row: choosing a bag is data entry, not activity."""
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO scans (code, known, first_seen_at, last_seen_at, hits, bag_units, bag_mix)
        VALUES (?, 1, ?, ?, 1, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            bag_units = excluded.bag_units,
            bag_mix   = excluded.bag_mix
        """,
        (code, now, now, mix_units(mix), json.dumps(mix, sort_keys=True)),
    )


def ctl_random_mix(rng: random.Random) -> dict[str, int]:
    """A plausible, never-empty bag list for the "random bags" fill.

    Dealt one item at a time so the total cap is a property of the deal rather
    than a rejection loop: 1..`MAX_ITEMS` pieces, each bag within its own limit.
    """
    counts: dict[str, int] = {}
    for _ in range(rng.randint(1, MAX_ITEMS)):
        bag = rng.choice([b for b in BAGS if counts.get(b.icon, 0) < b.limit])
        counts[bag.icon] = counts.get(bag.icon, 0) + 1
    return counts


def ctl_mix_in_range(min_units: float | None, max_units: float | None,
                     rng: random.Random | None = None) -> tuple[dict | None, float]:
    """A random, valid bag list whose total sits inside `[min_units, max_units]`.

    The trickle's baggage: every arrival carries something, and how much is dealt
    inside the operator's range rather than fixed. A range with no exact step in
    it (the ladder is 0.25 apart) picks the combination nearest its middle, so a
    hand-typed bound never leaves a passenger bare or the endpoint 422-ing.
    A ceiling of zero means "no bag", and returns `(None, 0)`.
    """
    rng = rng or random.Random()
    if max_units is not None and float(max_units) <= 0:
        return None, 0.0          # a zero ceiling means "no bag", whatever the floor
    lo = 0.0 if min_units is None else max(0.0, float(min_units))
    hi = float("inf") if max_units is None else float(max_units)
    if hi < lo:
        lo, hi = hi, lo
    if hi <= 0:
        return None, 0.0
    options = [c for c in BAG_COMBOS if lo - 1e-9 <= c[1] <= hi + 1e-9]
    if not options:
        mid = (lo + hi) / 2
        options = [min(BAG_COMBOS, key=lambda c: abs(c[1] - mid))]
    return rng.choice(options)


def reset_world(conn: sqlite3.Connection) -> int:
    """Put the demo back to a cabin where nobody has checked in a bag.

    Every bag the app can show is cleared here: the choice lives on the scan row
    (`bag_units` + `bag_mix`) and goes with it, and `passengers.baggage_volume_l`
    — the one figure no scan owns — is nulled as well, because the board's
    Baggage column and the ticket both print it. The bin loads, the legend's
    counts and the stow advice are all derived from these two, so clearing them
    is what "reset the bins" means; nothing else is projected. Names, seats and
    tickets are left alone (D7, amended by R21).

    Returns how many scan rows were cleared.
    """
    before = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
    conn.execute("DELETE FROM scans")
    conn.execute("UPDATE passengers SET baggage_volume_l = NULL")
    return before


def ctl_bulk(conn: sqlite3.Connection, action: str, codes: list[str] | None = None) -> int:
    """The page-level buttons. Returns how many rows the action touched.

    * ``checkin_all``  — everyone not on the board gets the row they are missing
    * ``clear``        — the board's Reset, without the header dance
    * ``random_bags``  — fill every checked-in passenger with a random bag list
    * ``preboard``     — check in just the codes pasted in, ignoring the rest
    """
    if action == "checkin_all":
        affected = 0
        for p in ctl_rows(conn):
            if not p["checked_in"]:
                ctl_checkin(conn, p["code"])
                affected += 1
        return affected
    if action == "clear":
        return reset_world(conn)
    if action == "random_bags":
        rng = random.Random()
        affected = 0
        for p in ctl_rows(conn):
            if p["checked_in"]:
                ctl_set_bag(conn, p["code"], ctl_random_mix(rng))
                affected += 1
        return affected
    if action == "preboard":
        affected = 0
        for raw in codes or []:
            code = normalize_code(raw)
            if not code or fetch_passenger(conn, code) is None:
                continue
            before = conn.execute("SELECT 1 FROM scans WHERE code = ?", (code,)).fetchone()
            if before is None:
                ctl_checkin(conn, code)
                affected += 1
        return affected
    raise HTTPException(status_code=422, detail=f"unknown action {action!r}")


# ------------------------------------------------------- state snapshots
# Export/import the whole demo board as one JSON file, so a rehearsal or a
# test can be repeated exactly: board it, export, reset, import, and the cabin
# is byte-for-byte what it was. The snapshot is the two tables that hold state —
# `passengers` (the deal + seats + the legacy litre field) and `scans` — and
# deliberately **not** `settings`: importing must never rotate the board or
# control token, and an export must never leak them into a file on disk.

SNAPSHOT_FORMAT = "baggagego.state"
SNAPSHOT_VERSION = 1


def export_state(conn: sqlite3.Connection) -> dict:
    passengers = [
        dict(r)
        for r in conn.execute(
            "SELECT id, code, full_name, seat_number, baggage_volume_l, created_at "
            "FROM passengers ORDER BY id"
        )
    ]
    scans = [
        dict(r)
        for r in conn.execute(
            "SELECT id, code, known, first_seen_at, last_seen_at, hits, session_id, ua, "
            "bag_units, bag_mix FROM scans ORDER BY id"
        )
    ]
    return {
        "format": SNAPSHOT_FORMAT,
        "version": SNAPSHOT_VERSION,
        "exported_at": utcnow_iso(),
        "counts": {"passengers": len(passengers), "scans": len(scans)},
        "passengers": passengers,
        "scans": scans,
    }


def import_state(conn: sqlite3.Connection, snapshot: dict) -> dict:
    """Replace `passengers` and `scans` with the file's contents, in one
    transaction. `settings` (the tokens) is untouched.

    Lenient about shape, strict about codes: a hand-edited snapshot that omits
    `created_at` or writes `bag_mix` as an object rather than a JSON string is
    normalised, but a row with no ticket code is a 422 rather than half an import.
    """
    fmt = snapshot.get("format")
    if fmt not in (None, SNAPSHOT_FORMAT):
        raise HTTPException(status_code=422, detail=f"not a {SNAPSHOT_FORMAT} snapshot")
    try:
        version = int(snapshot.get("version", SNAPSHOT_VERSION))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="snapshot version is not a number")
    if version > SNAPSHOT_VERSION:
        raise HTTPException(
            status_code=422,
            detail=f"snapshot version {version} is newer than this server ({SNAPSHOT_VERSION})",
        )
    passengers = snapshot.get("passengers")
    scans = snapshot.get("scans")
    if not isinstance(passengers, list) or not isinstance(scans, list):
        raise HTTPException(status_code=422, detail="snapshot needs a passengers[] and a scans[] list")

    clean_scans: list[dict] = []
    for raw in scans:
        if not isinstance(raw, dict) or not raw.get("code"):
            raise HTTPException(status_code=422, detail="every scan needs a code")
        row = dict(raw)
        mix = row.get("bag_mix")
        if isinstance(mix, (dict, list)):
            row["bag_mix"] = json.dumps(mix, sort_keys=True)
        clean_scans.append(row)
    clean_passengers: list[dict] = []
    for raw in passengers:
        if not isinstance(raw, dict) or not raw.get("code"):
            raise HTTPException(status_code=422, detail="every passenger needs a code")
        clean_passengers.append(dict(raw))

    now = utcnow_iso()
    conn.execute("DELETE FROM scans")
    conn.execute("DELETE FROM passengers")
    for p in clean_passengers:
        conn.execute(
            "INSERT INTO passengers (id, code, full_name, seat_number, baggage_volume_l, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (p.get("id"), p["code"], p.get("full_name"), p.get("seat_number"),
             p.get("baggage_volume_l"), p.get("created_at") or now),
        )
    for s in clean_scans:
        conn.execute(
            "INSERT INTO scans (id, code, known, first_seen_at, last_seen_at, hits, session_id, ua, "
            "bag_units, bag_mix) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (s.get("id"), s["code"], int(s.get("known", 1)),
             s.get("first_seen_at") or now, s.get("last_seen_at") or now,
             int(s.get("hits", 1)), s.get("session_id"), s.get("ua"),
             s.get("bag_units"), s.get("bag_mix")),
        )
    conn.commit()
    return {"passengers": len(clean_passengers), "scans": len(clean_scans)}


def build_state(conn: sqlite3.Connection, *, advice: bool = False) -> dict:
    passengers = []
    for r in conn.execute(
        """
        SELECT p.id, p.code, p.full_name, p.seat_number, p.baggage_volume_l,
               s.id AS scan_id, s.hits, s.last_seen_at, s.bag_units, s.bag_mix
        FROM passengers p
        LEFT JOIN scans s ON s.code = p.code
        WHERE p.full_name IS NOT NULL
        ORDER BY p.id
        """
    ):
        bag = bag_summary(scan_mix(r["bag_mix"]))
        passengers.append(
            {
                "id": r["id"],
                "scan_id": r["scan_id"],
                "code": r["code"],
                "full_name": r["full_name"],
                "seat_number": r["seat_number"],
                "baggage_volume_l": r["baggage_volume_l"],
                "bag": bag,
                "baggage_text": baggage_text(bag, r["baggage_volume_l"]),
                "scanned": r["hits"] is not None,
                "hits": r["hits"] or 0,
                "last_seen": r["last_seen_at"],
            }
        )
    unknown = conn.execute("SELECT COUNT(*) FROM scans WHERE known = 0").fetchone()[0]
    newest = max(
        (parse_iso(r[0]) for r in conn.execute("SELECT last_seen_at FROM scans")),
        default=None,
    )
    age = None
    if newest:
        age = max(0, int((datetime.now(timezone.utc) - newest).total_seconds()))

    # No recent code: the newest check-in is the list's mark, not the cabin's.
    seats = seat_map(passengers)
    # every declared bag is stowed in the compartment the card names (R20), so the
    # board's colours and the ticket's advice come out of one computation; the
    # advice itself is only asked for where it is used (the card and the scan),
    # because the board has no seat to give it. The bags go in by their scan row's
    # id — first come, first served — so a new check-in can never move a bag that
    # is already stowed (2026-11-10)
    stow_input = sorted(
        (p for p in passengers if p["bag"]),
        key=lambda p: (p["scan_id"] is None, p["scan_id"] or 0),
    )
    bins, decisions = stow_plan(stow_input, seats["rows"])
    scanned = sum(1 for p in passengers if p["scanned"])
    state = {
        "allocated": len(passengers),
        "passengers": passengers,
        # the cabin projection, shared by the rendered grid and this payload, so
        # the projector and the poll cannot disagree (R9)
        "seats": seats,
        "seat_rows": seats["rows"],
        "bins": bins,
        # the live boarding log: from the same decision the bins were built from,
        # so a line cannot name a zone the bag is not in
        "log": boarding_log(passengers, decisions, seats["rows"]),
        # the seatmap's accessible name, in words, for the same reason the loads
        # travel with it: board.js repaints the compartments every poll, so a reset
        # must not leave the words describing a cabin that no longer exists (R21)
        "cabin": cabin_meta(bins, scanned=scanned, allocated=len(passengers)),
        "counters": {
            "scanned": scanned,
            "hits": sum(p["hits"] for p in passengers),
            # distinct unknown tickets seen, not unknown scan events
            "unknown": unknown,
            # age of the most recent scan of ANY kind (known or not)
            "last_scan_age_s": age,
        },
        "server_now": utcnow_iso(),
    }
    if advice:
        state["decisions"] = decisions
    return state


def board_view(state: dict) -> dict:
    """Row order + display strings for the board (§3.2).

    Most recent scan pinned to the top and accented, earlier scans stay lit,
    everything else keeps seat order. board.js mirrors this exactly, ordering on
    (is_recent, data-index) — keep the two rules in step or the first poll will
    visibly reshuffle the board.
    """
    passengers = state["passengers"]
    recent_code = most_recent_code(passengers)
    return {
        "counters": state["counters"],
        "recent": recent_code,
        "seats": state["seats"],
        "seat_rows": state["seat_rows"],
        "bins": state["bins"],
        "log": state["log"],
        "cabin": state["cabin"],
        "rows": [
            {
                **p,
                "index": p["id"],
                "state_text": age_text(p["last_seen"]),
                "is_recent": p["code"] == recent_code,
            }
            for p in sorted(passengers, key=lambda p: (0 if p["code"] == recent_code else 1, p["id"]))
        ],
    }


# ------------------------------------------------------------------- views

def esc(value) -> str:
    return html.escape(str(value), quote=True)


def lockup() -> str:
    """The white BaggageGO lockup, for the purple band. SVG, so it stays sharp
    on a projector; white because the band is brand purple."""
    return (
        f'<a class="lockup" href="{LIVE_PATH}" aria-label="BaggageGO live board">'
        f'<img src="{BASE}/brand/icons/baggagego-icon-white.svg" alt="BaggageGO"></a>'
    )


def icon(name: str) -> str:
    """Inline one of the glyphs. Inlined rather than <img> because the kit draws
    them with currentColor — the CSS decides the colour, so no colour is frozen
    into an icon file (and they cost no extra request). The picker's bag glyphs
    live in static/icons; the kit's own marks (the plane on the route line) in
    brand/icons/svg."""
    for base in (ICON_DIR, BRAND_ICONS):
        try:
            svg = (base / f"{name}.svg").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        return "\n".join(line for line in svg.splitlines() if not line.lstrip().startswith("<!--"))
    return ""


def hke_wordmark() -> str:
    """The airline's own wordmark, for the app bar on the ticket page.

    Inline rather than an <img>: an external SVG cannot read the kit's tokens,
    and the e-symbol's leading edge is the one cyan mark brand.json reserves.
    The letterforms take currentColor, so the bar owns their colour; the cyan
    polygon is addressed by class and filled from the token in style.css.
    """
    return (
        '<svg class="hke-wordmark" viewBox="45 45 690 190" role="img" aria-label="HK Express">'
        '<g>'
        '<polygon points="307.82,102.12 279.26,102.12 279.26,79.16 271.16,79.16 271.16,132.58 279.26,132.58 279.26,109.21 307.82,109.21 307.82,132.58 315.92,132.58 315.92,79.16 307.82,79.16"/>'
        '<polygon points="346.84,102.51 334.69,102.51 334.69,79.16 326.59,79.16 326.59,132.58 334.69,132.58 334.69,109.6 346.42,109.59 362.65,132.64 371.2,132.64 371.2,131.37 353.06,106.24 371.23,80.4 371.23,79.16 362.81,79.16"/>'
        '<path d="M648.69,88.95c5.08,0,11.5,1.62,16.6,4.17l3.51-11.51c-4.07-1.84-12.15-3.72-18.86-3.72c-14.07,0-23.22,6.87-23.32,17.51c-0.08,8.06,4.04,13.09,12.59,15.39l9.32,2.45c3.84,1,7.29,2.13,7.26,5.04c-0.04,4.38-4.81,4.94-8.95,4.94c-6.71,0-13.28-2.81-17.04-4.76l-2.48,11.25c3.05,1.52,10.24,4.47,20.02,4.47c13.28,0,21.26-6.35,21.36-16.97c0.08-8.18-3.74-12.83-12.38-15.07l-10.25-2.66c-3.96-1.05-6.36-1.93-6.33-5.25C639.77,90.86,643.03,88.95,648.69,88.95z"/>'
        '<path d="M703.9,102.12l-10.25-2.66c-3.96-1.05-6.36-1.93-6.33-5.25c0.03-3.34,3.3-5.26,8.96-5.26c5.08,0,11.5,1.62,16.6,4.17l3.51-11.51c-4.07-1.84-12.15-3.72-18.86-3.72c-14.07,0-23.22,6.87-23.32,17.51c-0.08,8.06,4.04,13.09,12.59,15.39l9.32,2.45c3.84,1,7.29,2.13,7.26,5.04c-0.04,4.38-4.81,4.94-8.95,4.94c-6.71,0-13.28-2.81-17.04-4.76l-2.48,11.25c3.05,1.52,10.24,4.47,20.02,4.47c13.28,0,21.26-6.35,21.36-16.97C716.37,109.01,712.55,104.36,703.9,102.12z"/>'
        '<path d="M501.51,77.88c-12.53-0.83-22.24,7.27-22.24,7.27l-0.01,68.74H493V91.31c2.24-0.79,4.56-1.28,6.72-1.28c10.87,0,15.35,8.68,15.35,16.89c0,10.15-8.96,15.7-17.3,13.9l-0.02,13.17c28.2,1.8,31.52-21.37,31.55-28.17C529.6,90.53,519.22,78.54,501.51,77.88z"/>'
        '<path d="M542.7,83.08c-5.15,2.94-5.51,6.8-5.53,8.81l0.01,40.55h13.81l-0.01-38.77c0,0,7.12-4.95,19.18-3.64l0.11-10.77c-1.77-0.77-4.16-1.1-7.17-1.15C552.84,77.93,545.1,81.61,542.7,83.08z"/>'
        '<path d="M589.62,118.85c-2.73-2.16-4.39-5.32-4.59-8.8c-0.41-6.85,4.35-12.82,5.31-14.03c4.9-5.39,10.28-6.2,13.69-5.16c4.59,1.41,2.33,4.53,0.28,6.54c-4.72,4.64-12.04,4.57-12.04,4.57c-2.98,4.92-3.71,9.32-0.31,12.36c6.76,1.12,15.17-1.04,21.79-7.08c7.19-6.65,6.33-13.44,5.18-16.9c-1.8-5.4-6.55-9.74-12.7-11.62c-3.44-1.25-16.13-2.8-26.08,8.23c-9.18,10.9-10.66,25.58-3,36.04c12.95,17.76,34.91,10.49,42.86,5.12L612.26,117C607.45,120.55,596.68,124.43,589.62,118.85z"/>'
        '<path d="M473.13,79.55l0.12-0.33h-14.57l-0.07,0.14c-2.52,5.12-6.12,10.62-10.41,15.92l-11.21-16.06h-15.23l17.9,25.64c-4.78,4.24-18.52,14.91-30.36,16.96c-8.82,1.53-15.85-1.66-19.89-6.78c-1.52-1.93-2.47-4.28-2.49-6.74c-0.05-6.29,4.46-12.03,5.31-13.09c2.96-3.25,6.09-4.84,8.85-5.36c1.82-0.34,3.48-0.24,4.81,0.21c1.7,0.57,5.22,1.82,0.1,6.72c-4.85,3.82-8.37,4.02-11.97,4.38c-5.01,8.02-1.08,11.99,1.28,12.47c8.66,0.94,16.24-3.42,20.32-7.2c3.09-2.86,7.86-8.85,5.18-16.9c-1.8-5.4-6.55-9.74-12.7-11.62c-3.44-1.05-15.49-3.56-26.08,8.23c0,0-11.48,12.36-6.75,28.04c5.19,17.18,20.73,21.26,35.36,20.02c14.69-1.25,27.8-10.92,36.61-18.48l11.39,16.99h15.23l-17.58-25.86C464.11,97.82,470.08,88.13,473.13,79.55z"/>'
        '<polygon points="292.54,171.81 270.06,143.73 249.49,160.93 271.97,189.01" class="mark-e"/>'
        '<path d="M242.96,166.16c0,0-27.47,25.53-55.68,25.53c-33.32,0-42.77-23.72-44.4-28.48c-9.59-27.92,15.73-60.78,32.57-69.68c6.87-3.64,16.58-7.29,23.93-3.38c0.25,0.13,3.16,1.86-1.42,10.12c-2.94,5.29-17.94,25.44-43.51,29.53c-8.49,18.14-4.35,30.33-0.91,36.18c18.37-1.34,35.75-8.36,51.76-20.92l0.37-0.3c14.1-11.73,36.66-35.46,29.34-62.61c-4.09-15.15-16.68-25.9-33.69-28.76c-21.6-3.62-53.78,5.66-78.89,42.85c-15.21,20.26-39.22,90.36,28.07,123.96c60.5,26.44,114.59-25.81,114.59-25.81L242.96,166.16z"/>'
        '<path d="M104.11,114.18c-9.22-6.72-22.23-19.11-27.66-24.69l-23.81,27.07c12.76,16.15,36,31.18,44.14,35.6C96.66,138.21,99.89,124.86,104.11,114.18z"/>'
        '</g></svg>'
    )


# The app's own five tabs, in the app's order. Check-in is the current screen:
# it is where the app keeps the e-boarding pass this page stands in for.
APP_TABS = (
    ("Home", '<path d="M4 11l8-6 8 6v9H4z"/>'),
    ("Search", '<circle cx="11" cy="11" r="7"/><path d="M16 16l5 5"/>'),
    ("Trips", '<path d="M4 6h16v14H4z"/><path d="M8 10h8"/>'),
    ("Check-in", '<path d="M6 3h12v18l-6-3-6 3z"/><path d="M9 9l2 2 4-4"/>'),
    ("Account", '<circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0 1 16 0"/>'),
)
CURRENT_TAB = "Check-in"


def app_bar_html(seat: str | None = None) -> str:
    """The airline's app bar: its wordmark, and the passenger's seat.

    The seat is here and not only on the card because the card is forty rows
    tall: the one fact its owner must not scroll away from stays pinned with
    the chrome. Without a passenger there is no seat, and the bar says nothing
    rather than inventing one.
    """
    seat_html = (
        f'<p class="appbar-seat"><span>Seat</span> <b>{esc(seat)}</b></p>'
        if seat else ""
    )
    return (
        '<header class="appbar"><div class="appbar-inner">'
        f'{hke_wordmark()}{seat_html}'
        '</div></header>'
    )


def tab_bar_html() -> str:
    """The app's tab bar, five tabs, the current one marked.

    The tabs are spans, not links: this is one screen of the demo, and a row of
    links that goes nowhere would promise navigation that does not exist. The
    labels stay legible to a screen reader (a nav landmark), but nothing here
    is focusable or claims to act.
    """
    tabs = "".join(
        f'<span class="tab{" is-on" if label == CURRENT_TAB else ""}"'
        + (' aria-current="page"' if label == CURRENT_TAB else '')
        + f'><svg viewBox="0 0 24 24" aria-hidden="true">{glyph}</svg>{label}</span>'
        for label, glyph in APP_TABS
    )
    return f'<nav class="tabbar" aria-label="HK Express app"><div class="tabbar-inner">{tabs}</div></nav>'


def bag_choice_html(bag: Bag, qty: int) -> str:
    """One bag with its stepper.

    The minus/plus buttons are `type="button"`: with JS off they do nothing, and
    the number input beside them still carries the count into the form post — so
    the panel degrades to "type how many", which is honest rather than broken.
    """
    return f"""<div class="bag" data-units="{bag.units:g}" data-key="{bag.icon}">
  <span class="bag-icon">{icon(bag.icon)}</span>
  <span class="bag-name">{esc(bag.name)}</span>
  <span class="stepper">
    <button class="minus" type="button" aria-label="One fewer {esc(bag.name)}">&minus;</button>
    <input class="qty" type="number" name="{bag.icon}" data-key="{bag.icon}" value="{qty}"
           min="0" max="{bag.limit}" inputmode="numeric"
           aria-label="How many {esc(bag.name)}s">
    <button class="plus" type="button" aria-label="One more {esc(bag.name)}">+</button>
  </span>
</div>"""


def picker_html(code: str, mix: dict | None) -> str:
    """The translucent overlay: a stepper per bag, a running total, one confirm.

    Rendered in the card and hidden once a choice is on file. The counts come
    pre-filled from `mix`, so a re-render (a second scan of the same ticket)
    starts from what was recorded rather than from zero. There is deliberately no
    "Change bag" button on the card: the gate's controls page owns corrections.
    """
    chosen = mix or {}
    bags = "\n".join(bag_choice_html(b, chosen.get(b.icon, 0)) for b in BAGS)
    # The running total is a **count**, not a load: the passenger never sees units.
    items = sum(chosen.values())
    total = "no items yet" if not items else f"{items} item" + ("" if items == 1 else "s")
    hidden = " hidden" if mix else ""
    return f"""<div class="picker-wrap" id="picker"{hidden} role="dialog" aria-modal="true" aria-labelledby="picker-title" data-max-items="{MAX_ITEMS}">
  <div class="veil"></div>
  <div class="picker">
    <h2 id="picker-title">Which bags are you bringing?</h2>
    <p class="picker-hint">Add each bag, then confirm to check in.</p>
    <p class="picker-hint">Choose up to {MAX_ITEMS} items for this simulated boarding scenario.</p>
    <form id="picker-form" method="post" action="{BASE}/{esc(code)}/bag">
      <div class="bags">
{bags}
      </div>
      <p class="total"><span>Total</span>
        <b id="total">{esc(total)}</b></p>
      <button id="confirm" type="submit">Confirm and check in</button>
    </form>
    <p class="picker-error" id="picker-error" hidden></p>
  </div>
</div>"""


def shell(title: str, body: str, *, big: bool = False, page: str = "", extra_head: str = "") -> str:
    classes = " ".join(c for c in (page, "big" if big else "") if c)
    brand_css = asset_url(BRAND_CSS, f"{BASE}/brand/brand.css")
    style_css = asset_url(STATIC_DIR / "style.css", f"{BASE}/static/style.css")
    font = asset_url(BRAND_FONT, f"{BASE}/brand/fonts/Montserrat-var.ttf")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{brand_css}">
<link rel="stylesheet" href="{style_css}">
<link rel="preload" as="font" type="font/ttf" crossorigin href="{font}">{extra_head}
</head>
<body class="{classes}">
{body}
</body>
</html>
"""


def passenger_html(p: dict, code: str, mix: dict | None = None, *, notice: str | None = None,
                   stow: dict | None = None, cabin: dict | None = None) -> str:
    summary = bag_summary(mix)
    if summary:
        icons = "".join(icon(part["key"]) for part in summary["mix"])
        # The passenger's card carries **no unit figures**: the counts are what a
        # person acts on ("Small trolley case ×2"), and the arithmetic is the
        # board's business. Same rule as the stowage line beneath it.
        baggage = (
            f'<span class="bag-icon bag-icon--inline">{icons}</span>'
            f'<span>{esc(summary["mix_text"])}</span>'
        )
        status = f'Checked in. {esc(summary["mix_text"])}.'
        state = "recorded"
    else:
        passenger_bag = p["baggage_volume_l"]
        baggage = f"{passenger_bag:g} L" if passenger_bag is not None else "not recorded yet"
        status = notice or "Choose a bag to check in."
        state = "pending"
    # The pass, in the artifact's own composition: a masthead naming what this
    # is and which flight, the route with the plane sitting on its hairline,
    # the two things that identify the person, the three figures a passenger
    # acts on, and then the booking's own rows. All of it is the paper's own
    # facts (see FLIGHT) — the pass someone is holding must not disagree with
    # the pass on their phone.
    route_label = (
        f'{FLIGHT["from"]} ({FLIGHT["from_code"]}) to '
        f'{FLIGHT["to"]} ({FLIGHT["to_code"]})'
    )
    return shell(
        f"{p['full_name']} — BaggageGO",
        f"""{app_bar_html(p['seat_number'])}
<main class="card-wrap">
  <article class="card">
    <header class="band">{lockup()}</header>
    <div class="card-body">
      <p class="pass-head">
        <span class="ticket-kind">Boarding pass</span>
        <span class="pass-flight"><b>{esc(FLIGHT['no'])}</b> <span>{esc(FLIGHT['date'])}</span></span>
      </p>
      <div class="route" role="img" aria-label="{esc(route_label)}">
        <b class="route-code">{esc(FLIGHT['from_code'])}</b>
        <span class="route-line" aria-hidden="true">{icon('icon-plane')}</span>
        <b class="route-code">{esc(FLIGHT['to_code'])}</b>
        <span class="route-city">{esc(FLIGHT['from'])}</span>
        <span class="route-city is-to">{esc(FLIGHT['to'])}</span>
      </div>
      <div class="identity">
        <h1 class="name">{esc(p['full_name'])}</h1>
        <p class="figure">
          <b>{esc(p['seat_number'])}</b><span>seat</span>
        </p>
      </div>
      <dl class="fields">
        <div><dt>Departs</dt><dd>{esc(FLIGHT['departs'])}</dd></div>
        <div><dt>Gate</dt><dd>{esc(FLIGHT['gate'])}</dd></div>
        <div><dt>Boards</dt><dd>{esc(FLIGHT['boards'])}</dd></div>
      </dl>
      <dl class="facts">
        <dt>Baggage</dt><dd class="bag-fact">{baggage}</dd>
        <dt>Ticket</dt><dd class="mono">{esc(code)}</dd>
      </dl>
    </div>
    <div class="tear"></div>
    <div class="card-body">
      <p class="status" id="status" data-state="{state}">{status}</p>
      {stow_html(stow) if stow else ''}
      {card_cabin_html(cabin['seats'], cabin['bins'], stow) if (stow and cabin) else ''}
    </div>
  </article>
</main>
{tab_bar_html()}
{picker_html(code, mix)}
<script src="{asset_url(STATIC_DIR / 'app.js', f'{BASE}/static/app.js')}" data-code="{esc(code)}" defer></script>
""",
        page="card-page",
    )


def no_booking_html(code: str) -> str:
    return shell(
        "BaggageGO — no booking on this flight",
        f"""{app_bar_html()}
<main class="card-wrap">
  <article class="card">
    <header class="band">{lockup()}</header>
    <div class="card-body">
      <h1 class="name is-error">No booking on this flight</h1>
      <p class="lead">Ticket <span class="mono">{esc(code)}</span> is not on the
        manifest. Ask the gate agent.</p>
      <p class="status" id="status" data-state="pending">Recording your scan…</p>
    </div>
  </article>
</main>
{tab_bar_html()}
<script src="{asset_url(STATIC_DIR / 'app.js', f'{BASE}/static/app.js')}" data-code="{esc(code)}" defer></script>
""",
        page="card-page",
    )


def row_html(p: dict) -> str:
    baggage = p["baggage_text"]
    classes = ["row"] + (["scanned"] if p["scanned"] else []) + (["recent"] if p["is_recent"] else [])
    # board.js patches these two nodes; the row markup itself never changes shape.
    hits = f'<span class="hits">×{p["hits"]}</span>' if p["hits"] > 1 else '<span class="hits"></span>'
    age = p["state_text"] if p["scanned"] else "—"
    return (
        f'<tr class="{" ".join(classes)}" data-index="{p["index"]}"'
        f' data-code="{esc(p["code"])}">'
        f'<td class="cell-seat">{esc(p["seat_number"])}</td>'
        f'<td class="cell-name">{esc(p["full_name"])}</td>'
        f'<td class="cell-code mono">{esc(p["code"])}</td>'
        f'<td class="cell-bag">{esc(baggage)}</td>'
        f'<td class="cell-state">{hits}<span class="age">{esc(age)}</span></td>'
        f"</tr>"
    )


# The placeholder a barer board shows: the panel exists before the first scan, so
# the audience is not looking at an empty rectangle and board.js has somewhere to
# put line one.
LOG_EMPTY = '<li class="log-empty">Waiting for the first scan.</li>'


def log_html(entries: list[dict]) -> str:
    """The live boarding log, in its current state (R9).

    Newest last, because a log is read at the bottom, and the seat is a `<b>` so
    the figure the eye is looking for leads the line — the same job the figure
    does on the card. The sentence itself is the server's (`note`); `board.js`
    only appends lines it has not seen and re-prints one whose text changed, so
    the panel and the poll cannot say different things.

    No header: the first line explains the strip, and the region is named for
    assistive tech only (`aria-label`). A label above a feed is the box telling
    the audience what they are already watching.

    `is-empty` drives the placeholder: empty, the strip says it is waiting; one
    boarding later, the same markup is the list and the placeholder is gone.
    """
    lines = "\n".join(
        f'<li class="log-line" data-key="{esc(e["key"])}">'
        f'<b class="log-seat">{esc(e["seat"])}</b> {esc(e["note"])}</li>'
        for e in entries
    )
    empty = "" if entries else " is-empty"
    return (
        f'<aside class="log{empty}" aria-label="Boarding log">'
        '<ol class="log-lines" role="log" aria-live="polite">'
        f"{lines or LOG_EMPTY}"
        "</ol></aside>"
    )


def seat_cell_html(cell: dict, own: bool = False) -> str:
    """One square, and no text on it.

    The letters run along the top of the cabin and the row numbers down the
    aisle, so a square's position already says which seat it is; printing `1A` in
    it made the drawing busy at board size and illegible at ticket size, and the
    one thing a filled square has to say is *taken*. The seat's identity stays on
    the element — `data-seat`, `title`, `data-code` for the row that owns it — and
    never on the picture.

    `own` is the ticket's own seat: the same square, marked louder. Only the card
    passes it; the board marks seats by state and nothing else.
    """
    code = f' data-code="{esc(cell["code"])}"' if cell["code"] else ""
    cls = f'seat {cell["state"]}' + (" is-you" if own else "")
    return (
        f'<span class="{cls}" data-seat="{esc(cell["seat"])}"'
        f' title="{esc(cell["seat"])}"{code}></span>'
    )


def cabin_doors(rows: int) -> list[int]:
    """The rows that carry a door, clamped to the cabin we actually have: a short
    cabin (the 4-row test deal) still gets a front and a rear door rather than one
    hanging past the end of the fuselage."""
    return sorted(r for r in {1, min(18, rows), min(19, rows), max(rows, 1)} if r >= 1)


def cabin_markers(rows: int) -> list[tuple[int, int]]:
    """Every exit marker as (band position, door row).

    A band is an empty strip — as tall as `--exit-gap` — that sits immediately
    before the row it is keyed to, and position `rows + 1` is *behind* the last
    row. Nothing marks a seat row itself: the front door is marked in front of
    row 1, the overwing pair in the gaps either side of it, and the rear door
    behind the last row. The height budget is charged for one band per entry, so
    the list is the count as well as the placement.
    """
    rows = max(rows, 1)
    doors = cabin_doors(rows)
    front, rear = doors[0], doors[-1]
    overwing = [row for row in doors if row not in (front, rear)]
    markers = [(1, front)]                                   # in front of row 1
    if overwing:
        markers.append((overwing[0], overwing[0]))           # gap above the pair
        markers.append((overwing[-1] + 1, overwing[-1]))     # gap below the pair
    markers.append((rows + 1, rear))                         # behind the last row
    return sorted(markers)


def cabin_slots(rows: int) -> list[tuple[str, int]]:
    """The cabin's vertical axis, top to bottom, as one-slot pieces: `("row", n)`
    for a seat row and `("band", n)` for the exit band in front of row `n`.

    A band is **one row pitch tall** (`.cabin` holds `--exit-gap: 1`), so it is a
    slot like any row: an exit is part of the cabin's length, not extra space a
    compartment has to run over. The band behind the last row is outside the
    binning — there is no row after it to pair with.
    """
    interior = {p for p, _ in cabin_markers(rows) if 1 < p <= rows}
    slots: list[tuple[str, int]] = []
    for row in range(1, rows + 1):
        if row in interior:
            slots.append(("band", row))
        slots.append(("row", row))
    return slots


def cabin_bins(rows: int) -> list[tuple[int, int, str, int]]:
    """Every overhead **zone** rectangle as `(anchor row, top, label, slots)`.

    A zone merges two of the old two-slot compartments, so it covers four seat
    rows (`1-4`, `5-8`, ...). `slots` is how many one-row-pitch slots the
    rectangle spans: four seat rows, **plus one for every exit band inside the
    zone** — so `17-20` spans six (`17+b18+18+19+b20+20`) and every other zone
    four. `top` is always `0` now: a zone begins at its first seat row (the old
    `reaches-up` case existed only because a compartment could start on a band).
    `label` is the slot tiling and rides on the element so the tests can see it.
    """
    slot_list = cabin_slots(rows)
    bins: list[tuple[int, int, str, int]] = []
    for anchor, seat_rows in cabin_bin_rows(rows):
        first = slot_list.index(("row", anchor))
        last = slot_list.index(("row", seat_rows[-1]))
        slots = slot_list[first:last + 1]
        label = "+".join(
            ("b" if kind == "band" else "") + str(number)
            for kind, number in slots
        )
        bins.append((anchor, 0, label, len(slots)))
    return bins


def side_letters(side: str) -> tuple[str, ...]:
    """The letters on one wall, in order: A/B/C left, D/E/F right."""
    half = SEAT_LETTERS[:SEATS_PER_SIDE] if side == "left" else SEAT_LETTERS[SEATS_PER_SIDE:]
    return tuple(half)


def seat_side(letter: str) -> str:
    """Which wall a seat's compartment runs along — the aisle is the split, so
    the letter decides it and nothing else does."""
    return "left" if letter in side_letters("left") else "right"


def bin_level(units: float) -> tuple[str, bool]:
    """The ramp as `(level, over)`: green through `BIN_GREEN_UNITS` (2), yellow
    above that and up to `STOW_AWAY_UNITS` (8), red from there.

    Decided here so `board.js` never re-derives a threshold (R9) — it applies the
    level the server sent, exactly as it does for a seat square. Red begins at the
    advice line, not before it, so the board never paints a zone red that the card
    would still send a passenger to (decision 2026-11-10). `over` is the second
    fact the ramp alone cannot show: past `BIN_CAPACITY` the prediction no longer
    fits and that passenger has to look further down the cabin. Red covers it
    already; the flag sharpens the edge and gets counted in the legend. Zero is
    green — a zone with nothing predicted in it is a zone with room.
    """
    level = "red" if units >= STOW_AWAY_UNITS else "yellow" if units > BIN_GREEN_UNITS else "green"
    return level, units > BIN_CAPACITY


# The ramp in one word, shared by the board's legend and the card's stowage pill,
# so the two can never spell the same load two ways.
BIN_LEVEL_TEXT = {"green": "mostly empty", "yellow": "filling", "red": "full"}


def cabin_bin_rows(rows: int) -> list[tuple[int, tuple[int, ...]]]:
    """Each merged **zone** as `(anchor row, the seat rows it hangs over)`.

    Two of the old two-slot compartments become one zone, so a zone is four seat
    rows (`1-4`, `5-8`, ...). The rows are counted in **seat rows, not bin
    slots**: the exit bands are anatomy inside a zone, not extra zones, so the
    `17-20` zone holds rows 17,18,19,20 even though it spans six slots. A short
    tail is simply fewer rows (`rows=18` -> the last zone is `17-18`).
    """
    out: list[tuple[int, tuple[int, ...]]] = []
    row = 1
    while row <= rows:
        band = tuple(range(row, min(row + ZONE_ROWS, rows + 1)))
        out.append((band[0], band))
        row += ZONE_ROWS
    return out


def bin_seats(rows: int, anchor: int, side: str) -> list[str]:
    """The seats one compartment hangs over, on one wall, front to back."""
    for candidate, seat_rows in cabin_bin_rows(rows):
        if candidate == anchor:
            return [f"{row}{letter}" for row in seat_rows for letter in side_letters(side)]
    return []


def bin_for_seat(rows: int, row: int, letter: str) -> tuple[int, str] | None:
    """The base prediction: the compartment above a passenger's own seat.

    Nothing is shared, moved or split — the rule is one line of geometry, and the
    seat decides both halves of it: which pair of rows, which wall. `None` when no
    compartment covers the row (an odd cabin leaves its last row bare).
    """
    for anchor, seat_rows in cabin_bin_rows(rows):
        if row in seat_rows:
            return anchor, seat_side(letter)
    return None


def bin_title(seats: list[str], units: float, bags: int) -> str:
    """The one line a compartment carries. It is a tooltip rather than a label
    because the lane is about eight pixels wide on a 1080p projector at 40 rows —
    no number fits inside the rectangle, so the figure lives here and in the
    legend, and the fill itself carries only the colour.

    Reader: the board, and only the board. The passenger's card is drawn by the same
    `cabin_html()` with `units_in_titles=False`, so a phone gets `Row 1-4L · full`
    where the projector gets `Zone above 1A–4C · 8 of 9 units · 8 items`
    (2026-11-11). Items, not bags: the board counts pieces in a compartment, and
    "bag" would make the figure read like a different measure from the cap.
    """
    span = f"{seats[0]}–{seats[-1]}" if seats else "the zone"
    return (
        f"Zone above {span} · {units:g} of {BIN_CAPACITY:g} units"
        f" · {bags} item{"s" if bags != 1 else ""}"
    )


def empty_bins(rows: int) -> list[dict]:
    """Every compartment as an empty record: one per wall, in cabin order.

    The two walls are separate pieces of luggage space, so the same anchor row
    carries one number on the left and another on the right. `seats` is the seats
    the compartment hangs over; `bags` is how many declared bags end up in it,
    which is *not* the same set once a bag is redirected here.
    """
    bins: list[dict] = []
    for anchor, _ in cabin_bin_rows(rows):
        for side in ("left", "right"):
            bins.append(
                {
                    "bin": anchor,
                    "side": side,
                    "seats": bin_seats(rows, anchor, side),
                    "units": 0.0,
                    "bags": 0,
                }
            )
    return bins


def finish_bins(bins: list[dict]) -> list[dict]:
    """Add every compartment's level, `over` flag and tooltip once its load is
    known — the one place the ramp is applied, so nothing reads `units` directly."""
    for load in bins:
        level, over = bin_level(load["units"])
        load["capacity"] = BIN_CAPACITY
        load["level"] = level
        load["over"] = over
        load["title"] = bin_title(load["seats"], load["units"], load["bags"])
    return bins


def bin_index(bins: list[dict]) -> dict:
    """Compartments by `(anchor row, wall)`, which is how a seat finds its bin."""
    return {(load["bin"], load["side"]): load for load in bins}


def bin_loads(passengers: list[dict], rows: int) -> list[dict]:
    """The compartments after every declared bag has been stowed (`stow_plan`).

    The compressed demo makes the prediction and the stowage the same thing: a bag
    the card sends elsewhere is *in* the compartment it was sent to, so this is
    the load the board colours and the card's advice refers to — one computation,
    two surfaces.
    """
    return stow_plan(passengers, rows)[0]


def cabin_html(seats: dict, *, scanned: int, allocated: int, bins: list[dict] | None = None,
               advice: dict | None = None, own: str | None = None,
               label: str | None = None, units_in_titles: bool = True) -> str:
    """The cabin: the seat grid and its labels, with an exit marker in its own
    empty band wherever there is a door.

    Nothing encloses the seats — no outline, no walls, no bulkheads — and nothing
    marks a seat row either: every `EXIT` chip sits in a band of space, because
    the space is what says "exit". The front door is marked in front of row 1,
    the overwing pair in the gaps either side of it, the rear door behind the
    last row (`cabin_markers()` says where each band goes). Behind those bands
    the layout is the map's own: rows down the page with row 1 at the front, the
    letters across it, the row numbers in the aisle, nothing about `seat_map()`
    changed when the drawing appeared.

    Each band is one flex row of five pieces (door gutter, bin lane, seats, bin
    lane, door gutter), and every band takes its width from one `--body-w` — the
    drawing cannot drift away from the grid it wraps. The overhead compartments
    (`cabin_bins()`) are drawn in the bin lane, a strip of its own between the
    door gutter and the seats, so a bin and an `EXIT` marker can be level with
    each other without overlapping: they are in different lanes. A bin hangs over
    the two rows it belongs to, so it changes the picture without touching the
    budget.

    Rendered server-side in its current state (R9) and present in the page even
    while the list is the visible view, so the switch costs no fetch and a
    JS-less load still shows the truth.

    Each compartment also carries its **stowed load** as a class
    (`load-green` / `load-yellow` / `load-red`, plus `is-over` past the bin's 4.5
    units) and as `data-load`, from `bin_loads()`. The level is decided on the
    server (R9) and `board.js` only applies it, so a JS-less load already tells the
    truth. The two walls are coloured separately: they are two separate pieces of
    space, and the same anchor can read green on one side and red on the other.

    The same drawing serves the boarding pass (`card_cabin_html()`), so the two
    views cannot drift: `advice` rings the one zone the card names (nothing on the
    board passes it), `own` marks the ticket's own square, `label` replaces the
    cabin's own accessible name with the card's sentence, and every zone carries
    `data-name` (`1-4R`) so a client can find it by the name the advice uses.
    """
    letters = list(seats["letters"])
    side = seats["side"]
    rows = max(seats["rows"], 1)
    markers = cabin_markers(seats["rows"])
    bands_at: dict[int, list[int]] = {}
    for position, door_row in markers:
        bands_at.setdefault(position, []).append(door_row)
    by_row: dict[int, dict[str, dict]] = {}
    for cell in seats["cells"]:
        by_row.setdefault(cell["row"], {})[cell["letter"]] = cell

    def block(parts: list[str], aisle: str) -> str:
        """A·B·C | aisle | D·E·F, wrapped in the element that carries the walls."""
        return (
            '<span class="cabin-seats">'
            f'{"".join(parts[:side])}'
            f'<span class="cabin-aisle">{aisle}</span>'
            f'{"".join(parts[side:])}</span>'
        )

    def lane(inner: str) -> str:
        """The overhead-compartment lane: a reserved strip between a door gutter
        and the seats, empty except where a compartment is drawn. Its own lane is
        what keeps a bin from overlapping the `EXIT` marker beside it."""
        return f'<span class="cabin-lane" aria-hidden="true">{inner}</span>'

    spacer = '<span class="cabin-door" aria-hidden="true"></span>'
    compartments = {anchor: (top, label, slots) for anchor, top, label, slots in cabin_bins(seats["rows"])}
    loads = {(load["bin"], load["side"]): load for load in (bins or [])}

    def bin_html_for(anchor: int, top: int, slot: str, slots: int, side: str) -> str:
        """One merged zone on one wall, carrying its combined load.

        The two walls are drawn separately because they are filled separately: the
        same anchor holds one number on the left and another on the right. Where no
        load was sent (a caller that only wants the grid) the zone is empty and
        green — zero units is the bottom of the ramp. The rectangle's height is
        `--bin-slots` row pitches, so the exit zone (six slots) is taller than a
        plain one (four) and no internal seams show.
        """
        load = loads.get((anchor, side)) or {
            "units": 0.0,
            "level": "green",
            "over": False,
            "bags": 0,
            "title": bin_title(bin_seats(seats["rows"], anchor, side), 0.0, 0),
        }
        classes = "cabin-bin"
        classes += f' load-{load["level"]}' + (" is-over" if load["over"] else "")
        name = bin_name(seats["rows"], anchor, side)
        # The board's zones say the load; the passenger card's zones say the name and
        # the state word, because a tooltip is a thing a person reads (long-press on a
        # phone) and the arithmetic is the board's business (R25).
        title = load["title"] if units_in_titles else f'{name} · {BIN_LEVEL_TEXT[load["level"]]}'
        if advice and advice.get("level") and advice.get("bin") == name:
            classes += " is-advised"
        # `data-bin` carries the ANCHOR ROW. That is the key `bins` uses in the
        # payload and the key board.js looks up, so the poll can find this element
        # and repaint it. `data-slot` keeps the slot tiling (`"1+2+3+4"`) that the
        # tests read, and `--bin-slots` is the rectangle's height in row pitches.
        return (
            f'<span class="{classes}" data-bin="{anchor}" data-slot="{slot}"'
            f' data-side="{side}" data-load="{load["units"]:g}"'
            f' data-level="{load["level"]}" data-bags="{load["bags"]}"'
            f' data-name="{esc(name)}"'
            f' style="--bin-slots:{slots}"'
            f' title="{esc(title)}"></span>'
        )

    def marker(row: int, which: str) -> str:
        """One gutter cell of an exit band: the chip outside, chevrons pointing
        out of the cabin."""
        chip = '<span class="exit">EXIT</span>'
        chev = '<span class="chev" aria-hidden="true"></span>'
        inner = chip + chev if which == "left" else chev + chip
        return (
            f'<span class="cabin-door is-door {which}" data-door="{row}">{inner}</span>'
        )

    def band(position: int) -> str:
        """The empty strip an exit marker lives in — in front of row 1, either
        side of the overwing pair, or behind the last row. Returns "" where no
        marker sits at this position, so the row loop can just ask."""
        door_rows = bands_at.get(position, [])
        if not door_rows:
            return ""
        lefts = "".join(marker(row, "left") for row in door_rows)
        rights = "".join(marker(row, "right") for row in reversed(door_rows))
        return (
            f'<div class="cabin-row cabin-gap" data-exit="{position}">'
            f"{lefts}{lane('')}{block([], '')}{lane('')}{rights}</div>"
        )

    legend = [f'<span class="cabin-letter">{letter}</span>' for letter in letters]
    bands = [
        '<div class="cabin-row cabin-head">'
        f'{spacer}{lane("")}'
        f'{block(legend, "")}'
        f'{lane("")}{spacer}</div>'
    ]
    for row in range(1, seats["rows"] + 1):
        # in front of this row: an exit band if one is keyed to it
        bands.append(band(row))
        cells = by_row.get(row, {})
        parts = [
            seat_cell_html(cells[letter], own=(cells[letter]["seat"] == own))
            if letter in cells
            else '<span class="cabin-cell" aria-hidden="true"></span>'
            for letter in letters
        ]
        # the row number rides in the aisle, which is where the supplied seat map
        # puts it: the axis label runs once along the cabin, the other one at both ends
        aisle = f'<span class="cabin-rno">{row}</span>'
        # the overhead zones on the outer sides, one rectangle per merged zone —
        # the same rectangle in both lanes, and only where one is anchored. The
        # rectangle's height (`--bin-slots`) already pays for any exit band inside
        # the zone, so there is no `reaches-up`/`over-band` special case.
        cell = compartments.get(row)
        left = right = ""
        if cell:
            top, slot, slots = cell
            left = bin_html_for(row, top, slot, slots, "left")
            right = bin_html_for(row, top, slot, slots, "right")
        # every row keeps empty gutters: an exit marker never sits on a seat row
        bands.append(
            f'<div class="cabin-row" data-row="{row}">'
            f'{spacer}{lane(left)}{block(parts, aisle)}{lane(right)}{spacer}</div>'
        )
    # behind the last row
    bands.append(band(seats["rows"] + 1))
    bands.append(
        '<div class="cabin-row cabin-foot">'
        f'{spacer}{lane("")}'
        f'{block(legend, "")}'
        f'{lane("")}{spacer}</div>'
    )

    body = "\n".join(bands)
    # colour is not the only carrier: the label spells the ramp out in words, so a
    # screen reader (or a colour-blind judge) gets the same picture as the room
    if label is None:
        label = cabin_meta(list(loads.values()), scanned=scanned, allocated=allocated)["label"]
    # --rows drives the square size in CSS; 1 keeps the calc safe on an empty cabin
    return (
        f'<section class="seatmap" role="img" aria-label="{esc(label)}">'
        # --exit-count is how many exit bands the cabin carries, so the height
        # budget pays for them (one band's height is --exit-gap in style.css)
        f'<div class="cabin" style="--rows:{rows};--exit-count:{len(markers)}">'
        f"{body}</div></section>"
    )


def bin_name(rows: int, anchor: int, side: str) -> str:
    """A zone's name, as a passenger hears it: the seat rows it hangs over, then
    the wall — `Row 1-4L`, `Row 5-8R`. A short tail is its real span (`Row
    17-18L`), and a one-row zone alone reads `Row 17L`; the name stays the truth
    about the seats underneath instead of echoing the old two-compartment tiling.
    The word `Row` is part of the name because the figure alone (`5-8L`) reads
    like a code — the card prints this where a person has to find it.
    """
    seat_rows = next((r for a, r in cabin_bin_rows(rows) if a == anchor), ())
    if not seat_rows:
        return "?L" if side == "left" else "?R"
    span = str(seat_rows[0]) if len(seat_rows) == 1 else f"{seat_rows[0]}-{seat_rows[-1]}"
    return f"Row {span}{'L' if side == 'left' else 'R'}"


# How a direction reads on the card, in the priority order the client asked for.
# Short, because every sentence finishes "...stow your bag in the zone ___": the
# zone's own name is already the big figure above the sentence, so the words say
# *where*, and the code stays the code.
STOW_DIRECTION = {
    "own": "above your seat",
    "opposite": "across the aisle",
    "ahead": "in front of you",
    "opposite-ahead": "opposite and in front of you",
    "behind": "behind you",
}


def stow_order(rows: int, anchor: int, side: str) -> list[tuple[str, tuple[int, str]]]:
    """The compartments one bag tries, in order: its own, then **opposite, in
    front, opposite and in front, and behind last** — the walk the client asked
    for, read from the seat. A row with nothing ahead of it (or nothing behind it)
    simply has fewer candidates; no name is invented for a compartment that is not
    there.
    """
    other = "right" if side == "left" else "left"
    anchors = [a for a, _ in cabin_bin_rows(rows)]
    here = anchors.index(anchor)
    before = anchors[here - 1] if here > 0 else None
    after = anchors[here + 1] if here + 1 < len(anchors) else None
    order = [("own", (anchor, side)), ("opposite", (anchor, other))]
    if before is not None:
        order += [("ahead", (before, side)), ("opposite-ahead", (before, other))]
    if after is not None:
        order.append(("behind", (after, side)))
    return order


def stow_target(loads: dict, order: list[tuple[str, tuple[int, str]]],
                units: float | None = None) -> tuple[str, tuple[int, str], str]:
    """`(reason, key, state)` for one bag of `units` — the whole placement rule.

    A compartment **qualifies** when it is not nearly full (`STOW_AWAY_UNITS`) and
    the bag fits (`BIN_CAPACITY`). `units=None` means the bag's size is not known
    yet — the card before the picker is answered — and then only occupancy
    decides. When nothing qualifies the first compartment that at least fits is
    used, so a nearly-full bin still takes a handbag; when nothing fits even that,
    the emptiest of the candidates is — and *that* is how a compartment ends up
    over capacity and reads `is-over`.
    """
    fallback: tuple[str, tuple[int, str]] | None = None
    for reason, key in order:
        load = loads.get(key)
        if load is None:
            continue
        room = load["units"] + (units or 0.0) <= BIN_CAPACITY
        if load["units"] < STOW_AWAY_UNITS and room:
            return reason, key, "own" if reason == "own" else "redirect"
        if fallback is None and units is not None and room:
            fallback = (reason, key)
    if fallback is not None:
        reason, key = fallback
        return reason, key, "own" if reason == "own" else "redirect"
    reason, key = min(
        ((r, k) for r, k in order if k in loads), key=lambda option: loads[option[1]]["units"]
    )
    return reason, key, "packed"


def stow_payload(rows: int, seat: str, reason: str, state: str,
                 choice: tuple[int, str], own: tuple[int, str],
                 own_units: float, loads: dict, declared: bool = False) -> dict:
    """The card's advice object — the one place its wording is written.

    Every sentence is an **instruction**: the card tells a person what to do with
    the bag, because a passenger holding one wants a verb, not a status. The
    imperative is the same whether a bag is already on file or not, so the only
    thing `declared` changes is whether the zone is *full* (the bag is counted in
    it) or *nearly full* (the person has not chosen yet, so the model is sending
    them somewhere with room).

    The passenger gets **two sentences and a word**, never the arithmetic: the
    zone's load in units is the board's business (its tooltip), and a person
    standing in an aisle holding a bag cannot do anything with "2 of 4.5 units".
    The level comes through as `room` / `filling` / `full` beside the zone's name,
    and the sentence says where that zone is. No em dash, no middot-joined
    fragments: this is a line a person reads once, on a phone.
    """
    chosen = loads[choice]
    direction = STOW_DIRECTION[reason]
    if state == "own":
        note = "Stow your bag in the zone above your seat."
    elif state == "redirect":
        note = (f"The zone above your seat is full, please use the bin {direction}."
                if declared else
                f"The zone above your seat is nearly full, please use the bin {direction}.")
    else:
        note = ("Every zone near your seat is full. "
                f"Please use the bin {direction}." if declared else
                "Every zone near your seat is nearly full. "
                f"Please use the bin {direction}.")
    return {
        "seat": seat,
        "state": state,
        "reason": reason,
        "bin": bin_name(rows, choice[0], choice[1]),
        "side": choice[1],
        "level": chosen["level"],
        "level_text": BIN_LEVEL_TEXT[chosen["level"]],
        "units": chosen["units"],
        "capacity": BIN_CAPACITY,
        "label": "Where to put your bag",
        "declared": declared,
        "note": note,
        "own_bin": bin_name(rows, own[0], own[1]),
        "own_units": own_units,
    }


def stow_none(rows: int, seat: str | None = None) -> dict:
    """The card's answer when there is no compartment over the seat at all — an
    odd cabin's bare last row — so it admits that instead of naming a bin."""
    return {
        "seat": seat, "state": "none", "reason": "none", "bin": "—",
        "side": None, "level": None, "level_text": "", "units": None,
        "capacity": BIN_CAPACITY, "own_bin": None, "own_units": None,
        "label": "Where to put your bag", "declared": False,
        "note": "No zone is listed above your seat. Stow your bag in the nearest one you pass.",
    }


def stow_plan(passengers: list[dict], rows: int) -> tuple[list[dict], dict]:
    """Where every declared bag goes, and what that does to every compartment.

    This is the model the compressed demo asked for: **predicted in a bin = stowed
    in that bin**, so a bag the card redirects is inside the compartment it was
    sent to, and the board's colours are the loads the advice creates. Bags are
    placed one at a time in **arrival order — first come, first served**, which is
    the order the caller hands them in (the order their scan rows were recorded).
    Because the walk is a left fold over that order, a later bag can *never* move
    an earlier one: if the compartment above a new passenger is full, the new bag
    is rerouted around the bags already there. Each bag takes the first
    compartment in `stow_order()` that qualifies, so the only compartments past
    capacity are the ones where nothing near the passenger had room (state
    `packed`).

    Returns `(bins, decisions)`: the finished compartments, and the raw decision
    per seat (`"18A"`) — the same decision the load was built from, so the map and
    the ticket cannot disagree. A decision holds no copy: `stow_payload()` turns it
    into the card's words and only the card asks, so the board's one-second poll
    never builds 240 sentences (R22).
    """
    bins = empty_bins(rows)
    loads = bin_index(bins)
    walking: list[tuple[tuple[int, str], float, tuple[int, str]]] = []
    for passenger in passengers:
        seat = parse_seat(passenger.get("seat_number"))
        bag = passenger.get("bag")
        if not seat or not bag:
            continue
        where = bin_for_seat(rows, *seat)
        if where and where in loads:
            walking.append((seat, bag["units"], where))
    # no re-sort: the caller's order *is* the arrival order, and preserving it is
    # what makes the placement stable — the prefix of the fold cannot change
    decisions: dict[str, dict] = {}
    for (row, letter), units, own in walking:
        seat = f"{row}{letter}"
        own_units = loads[own]["units"]
        reason, key, state = stow_target(loads, stow_order(rows, *own), units)
        loads[key]["units"] = round(loads[key]["units"] + units, 4)
        loads[key]["bags"] += 1
        decisions[seat] = {
            "reason": reason, "state": state, "choice": key, "own": own,
            "own_units": own_units, "declared": True,
        }
    finish_bins(bins)
    return bins, decisions


def boarding_log(passengers: list[dict], decisions: dict, rows: int,
                 *, limit: int = BOARDING_LOG_LIMIT) -> list[dict]:
    """The live log: one line per boarding, **oldest first, newest last**.

    A boarding is a scan row, so the order is the *arrival* order the stow plan
    walks (`scan_id`) — not seat order and not "most recent". A repeat scan bumps
    `hits` without creating a second boarding, exactly as the bins are not
    re-stowed by it, so a passenger who scans twice still has one line.

    Each line is built from the very decision the plan acted on, so the zone it
    names is the zone the bag is *in*: `stowed` for the `own` state, `diverted`
    for the two the card sends elsewhere (`redirect`, `packed`) — the card's own
    word for a bag that is not above its owner. A passenger with no bag on file
    still boarded, so they get a line too; a seat the cabin does not cover has a
    bag but no destination, and no zone is invented for it.

    The copy is written here, beside every other string the surfaces read (R22):
    the payload carries the sentence *and* its pieces, and the board only prints
    them (R9). "One unit" is singular — `unit_text()` is the one place that rule
    lives, so the log cannot say "1 units".
    """
    entries: list[dict] = []
    boarded = sorted(
        (p for p in passengers if p["scanned"] and p["scan_id"]),
        key=lambda p: p["scan_id"],
    )
    for passenger in boarded:
        seat = passenger["seat_number"] or "—"
        bag = passenger["bag"]
        decision = decisions.get(seat) if bag else None
        zone = bin_name(rows, *decision["choice"]) if decision else None
        if bag and zone:
            units = bag["units"]
            verb = "stowed" if decision["state"] == "own" else "diverted"
            note = f"boarded, {unit_text(units)} of carry-on {verb} to {zone}"
        elif bag:
            note = f"boarded, {unit_text(bag['units'])} of carry-on"
        else:
            note = "boarded, no carry-on"
        entries.append(
            {
                "key": str(passenger["scan_id"]),
                "seat": seat,
                "units": bag["units"] if bag else 0.0,
                "state": decision["state"] if decision else "none",
                "zone": zone,
                "note": note,
                "text": f"{seat} {note}",
                "seen": passenger["last_seen"],
            }
        )
    return entries[-limit:]


def stow_advice(bins: list[dict], rows: int, seat) -> dict:
    """Where a bag would go if the person chose one right now — no size known.

    This is the pre-check-in card and the scan response for a passenger who has
    not declared a bag: the same walk and the same order as `stow_plan()`, run
    against the loads as they stand, with only occupancy deciding (a person who has
    not chosen a bag has no size to fit). `stow_plan()` supersedes it the moment a
    bag exists, because then the bag is in the picture.
    """
    parsed = parse_seat(seat)
    loads = bin_index(bins)
    where = bin_for_seat(rows, *parsed) if parsed else None
    if not where or where not in loads:
        return stow_none(rows)
    reason, key, state = stow_target(loads, stow_order(rows, *where))
    return stow_payload(
        rows, f"{parsed[0]}{parsed[1]}", reason, state, key, where,
        loads[where]["units"], loads,
    )


def stow_for(state: dict, seat) -> dict:
    """The card's advice for one seat, and the only place one payload is built.

    The plan's own decision when a bag is on file (that bag is already in the
    picture), turned into copy here; otherwise where a bag would go right now. The
    board's poll never comes through here, so it never builds a sentence (R22).
    """
    parsed = parse_seat(seat)
    key = f"{parsed[0]}{parsed[1]}" if parsed else None
    decision = (state.get("decisions") or {}).get(key) if key else None
    if decision:
        return stow_payload(
            state["seat_rows"], key, **decision, loads=bin_index(state["bins"])
        )
    return stow_advice(state["bins"], state["seat_rows"], seat)


def stow_html(advice: dict) -> str:
    """The stowage block on the boarding pass, under the action cluster.

    Server-rendered, with stable ids, because `app.js` re-paints it from the scan
    response: recording a bag changes the picture and can move the advice. The
    compartment's name is the second figure on the card after the seat, so it wears
    the figure purple; the level is a **word** as well as a colour, and everything
    else is a sentence the server wrote (`stow_payload()`).
    """
    pill = (
        f'<span class="stow-level" id="stow-level">{esc(advice["level_text"])}</span>'
        if advice["level"] else
        '<span class="stow-level" id="stow-level" hidden></span>'
    )
    return (
        f'<section class="stow" id="stow" data-state="{esc(advice["state"])}"'
        f' data-level="{esc(advice["level"] or "none")}">'
        f'<p class="stow-label" id="stow-label">{esc(advice["label"])}</p>'
        f'<p class="stow-line"><b class="stow-bin" id="stow-bin">{esc(advice["bin"])}</b>{pill}</p>'
        f'<p class="stow-note" id="stow-note">{esc(advice["note"])}</p>'
        "</section>"
    )


def card_cabin_label(seats: dict, advice: dict | None) -> str:
    """The card map's accessible name, in words, written here and nowhere else.

    The figure is a `role="img"`, so a screen reader gets this sentence and
    nothing else from inside it: every mark has to be said. It names the seat and
    — when the card is sending the bag somewhere — the zone and its level. The
    scan response carries a freshly computed sentence beside the advice, so
    `app.js` replaces the name instead of composing one of its own (R9): a
    check-in can move the advice, and a map that still describes the old zone is
    the same failure as a board that still paints the old load.
    """
    rows = max(int(seats.get("rows") or 0), 1)
    label = f"Cabin map, {rows} rows with the front at the top."
    advice = advice or {}
    if advice.get("seat"):
        label += f" Your seat {advice['seat']} is marked."
    if advice.get("level"):
        label += f" The zone for your bag is {advice['bin']}, {advice['level_text']}."
    if advice.get("state") in {"redirect", "packed"} and advice.get("own_bin"):
        label += f" The zone above your seat is {advice['own_bin']}."
    return label


def card_cabin_html(seats: dict, bins: list[dict], advice: dict) -> str:
    """The cabin on the boarding pass: **the board's own drawing**, with the two
    marks a passenger needs added to it.

    This used to be a second, smaller drawing — one lane per wall, no door
    gutters, no exit bands, a fill gauge instead of a colour — on the argument
    that the projector drawing does not survive the trip to a phone. It does: the
    row pitch is a `min()` of a rem floor and a share of the card's width, so the
    same component fits 390px, and a second drawing is a second truth to keep in
    sync. The card and the board now share `cabin_html()`, the same
    `cabin_bin_rows()` zones, the same green/yellow/red swatches and the same
    `EXIT` chips, so what a judge sees on the projector and what the audience sees
    on their phone cannot disagree about where a zone is or how full it is.

    Two differences, both passed in rather than baked in:

    * `advice` rings the zone the card names, dashed in figure purple — the
      sentence above the map needs something to point at, and the board has no
      advice to point with;
    * `own` marks the ticket's seat: the same square, filled, so "which row am I
      in" survives a cabin where every boarded seat is already tinted.

    The key above the drawing explains those two marks and nothing else; the
    figure's accessible name is `card_cabin_label()`, which says the advice in
    words because a `role="img"` hides everything inside it.
    """
    keys = '<span class="cm-key-seat" aria-hidden="true"></span> your seat'
    if (advice or {}).get("level"):
        keys += '<span class="cm-key-bin" aria-hidden="true"></span> your bag\'s zone'
    scanned = sum(1 for cell in seats["cells"] if cell["state"] != "waiting")
    own = (advice or {}).get("seat")
    body = cabin_html(
        seats,
        scanned=scanned,
        allocated=len(seats["cells"]),
        bins=bins,
        advice=advice,
        own=own,
        label=card_cabin_label(seats, advice),
        units_in_titles=False,
    )
    return (
        f'<figure class="cabin-card" id="cabin-map"'
        f' data-seat="{esc(own or "")}">'
        f'<p class="cm-key">{keys}</p>'
        f"{body}"
        "</figure>"
    )


def bin_counts(bins: list[dict]) -> tuple[dict, int]:
    """How many compartments sit at each level, and how many are over capacity.

    One count for three readers — the rectangles' colours, the legend under the
    cabin and the seatmap's accessible name — so no two of them can disagree.
    """
    counts = {"green": 0, "yellow": 0, "red": 0}
    over = 0
    for load in bins:
        counts[load["level"]] += 1
        over += 1 if load["over"] else 0
    return counts, over


def cabin_meta(bins: list[dict], *, scanned: int, allocated: int) -> dict:
    """The seatmap's accessible name, in words, and nothing else.

    There is no legend under the cabin any more (2026-11-10). The rectangles carry
    the colour, each one carries its own load in its `title`, and a screen reader
    gets the counts from this label. board.js repaints the compartments as the poll
    arrives, so the words travel in the payload: left server-rendered they keep
    asserting a full cabin after a reset has emptied it (R21).
    """
    counts, over = bin_counts(bins)
    summary = (
        f'overhead zones {counts["green"]} with room, {counts["yellow"]} filling, '
        f'{counts["red"]} full'
    )
    if over:
        summary += f", {over} over capacity"
    return {"label": f"Cabin seat map: {scanned} of {allocated} seats scanned; {summary}"}


def view_switch_html(current: str, *, big: bool) -> str:
    """A real pair of links: with JS, board.js turns them into an in-place class
    flip; without it, they are a normal navigation and the server renders the
    chosen view. `?big=1` rides along so the projector mode is not lost."""
    suffix = "&big=1" if big else ""
    links = []
    for name, label in (("list", "Passengers"), ("seats", "Seat map")):
        mark = ' aria-current="page"' if name == current else ""
        links.append(f'<a href="?view={name}{suffix}" data-view="{name}"{mark}>{label}</a>')
    return f'<nav class="views" aria-label="Board view">{"".join(links)}</nav>'


# ----------------------------------------------------------- control page
# A plain operations console, deliberately outside the visual system: the
# logo/livery rules govern what the audience sees, not the tool the operator
# drives. It still reuses the *bag dialog* (bag_choice_html + the .picker rules)
# so the choice looks the same in both places — the point of the page is to
# control the demo, not to re-skin it.

def control_picker_html() -> str:
    """The same "Which bags are you bringing?" dialogue the QR page opens,
    rendered once. One dialogue serves every passenger; control.js fills in the
    name and the counts, and POSTs to the control API instead of the card."""
    bags = "\n".join(bag_choice_html(b, 0) for b in BAGS)
    return f"""<div class="picker-wrap" id="ctl-picker" hidden role="dialog" aria-modal="true" aria-labelledby="ctl-picker-title" data-max-items="{MAX_ITEMS}">
  <div class="veil"></div>
  <div class="picker">
    <h2 id="ctl-picker-title">Which bags are you bringing?</h2>
    <p class="picker-hint">Editing <b id="ctl-picker-who">—</b>. Confirm to save.</p>
    <p class="picker-hint">Choose up to {MAX_ITEMS} items for this simulated boarding scenario.</p>
    <form id="ctl-picker-form">
      <div class="bags">
{bags}
      </div>
      <p class="total"><span>Total</span>
        <b id="ctl-total">no items yet</b></p>
      <button id="confirm" type="submit">Confirm</button>
    </form>
    <p class="picker-error" id="ctl-picker-error" hidden></p>
  </div>
</div>"""


def ctl_row_html(p: dict) -> str:
    checked = p["checked_in"]
    mix = json.dumps(p["mix"], sort_keys=True)
    hits = f"×{p['hits']}" if p["hits"] > 1 else ("×1" if checked else "—")
    stow = p.get("stow")
    if stow:
        # own zone reads as just the name; a redirected bag keeps its origin, so a
        # "Row 5-8R ← Row 1-2L" says both where the bag is and where the seat is
        stow_text = stow["bin"] if stow["state"] == "own" else f"{stow['bin']} \u2190 {stow['own_bin']}"
        stow_cls = "c-stow" + ("" if stow["state"] == "own" else " is-redirect")
        stow_title = stow["note"] or ""
    else:
        stow_text, stow_cls, stow_title = "—", "c-stow", ""
    return f"""<tr class="ctl-row{' is-in' if checked else ''}" data-code="{esc(p['code'])}" data-mix="{esc(mix)}">
  <td class="c-seat">{esc(p['seat'])}</td>
  <td class="c-name">{esc(p['name'])}</td>
  <td class="c-code mono"><a href="{BASE}/{esc(p['code'])}" target="_blank" rel="noopener" title="Open the boarding pass">{esc(p['code'])}</a></td>
  <td class="c-bag">{esc(p['baggage_text'])}</td>
  <td class="{stow_cls}" title="{esc(stow_title)}">{esc(stow_text)}</td>
  <td class="c-hits">{hits}</td>
  <td class="c-state">{'in' if checked else 'out'}</td>
  <td class="c-actions">
    <button type="button" data-act="checkin"{' disabled' if checked else ''}>Check in</button>
    <button type="button" data-act="checkout"{' disabled' if not checked else ''}>Check out</button>
    <button type="button" data-act="bag">Bags&#8230;</button>
  </td>
</tr>"""


def controls_html(passengers: list[dict], *, token: str) -> str:
    rows = "\n".join(ctl_row_html(p) for p in passengers)
    checked = sum(1 for p in passengers if p["checked_in"])
    control_css = asset_url(STATIC_DIR / "control.css", f"{BASE}/static/control.css")
    control_js = asset_url(STATIC_DIR / "control.js", f"{BASE}/static/control.js")
    return shell(
        "BaggageGO · controls",
        f"""<header class="ctl-head">
  <h1>BaggageGO controls</h1>
  <p class="ctl-counters"><b id="ctl-in">{checked}</b> of <b>{len(passengers)}</b> checked in
     &middot; <a href="{LIVE_PATH}">open the board</a></p>
</header>
<main class="ctl-main">
  <section class="ctl-bulk">
    <button type="button" data-bulk="checkin_all">Check in all</button>
    <button type="button" data-bulk="random_bags">Random bags (checked-in)</button>
    <button type="button" data-bulk="clear">Clear all scans and bags</button>
    <button type="button" id="ctl-reload">Refresh</button>
    <p class="ctl-status" id="ctl-status"></p>
    <label>Codes to pre-board / trickle (one per line)
      <textarea id="ctl-codes" rows="3" placeholder="BGGO-XXXX-XXXX"></textarea></label>
    <button type="button" data-bulk="preboard">Pre-board listed codes</button>
    <button type="button" id="ctl-trickle">Start boarding (trickle listed)</button>
    <button type="button" id="ctl-trickle-stop" hidden>Stop</button>
    <label>Scatter over <input id="ctl-trickle-secs" type="number" value="75" min="5" max="600"> seconds</label>
    <label>Trickle baggage
      <input id="ctl-trickle-min" type="number" value="0.25" min="0" max="3" step="0.25">–<input id="ctl-trickle-max" type="number" value="1.5" min="0" max="3" step="0.25">
      units each
      <span class="ctl-snap-note">a random bag of that size, dealt per arrival (0 = none)</span></label>
  </section>
  <section class="ctl-snapshot">
    <b>Snapshot</b>
    <button type="button" id="ctl-export">Export state (JSON)</button>
    <label>Import state <input id="ctl-import" type="file" accept="application/json,.json"></label>
    <span class="ctl-snap-note">Replaces passengers + scans. Tokens are never exported or touched.</span>
  </section>
  <p class="ctl-search">
    <label>Filter <input id="ctl-search" type="search" placeholder="name, seat or code" autocomplete="off"></label>
  </p>
  <table class="ctl-table">
    <thead><tr><th>Seat</th><th>Passenger</th><th>Ticket</th><th>Baggage</th><th>Stowed</th><th>Hits</th><th>State</th><th>Actions</th></tr></thead>
    <tbody id="ctl-body">
{rows}
    </tbody>
  </table>
</main>
{control_picker_html()}
<script>window.BGGO_CTL_TOKEN = {json.dumps(token)};</script>
<script src="{control_js}" defer></script>
""",
        page="controls-page",
        extra_head=f'\n<link rel="stylesheet" href="{control_css}">',
    )


def board_html(view: dict, *, big: bool, token: str, view_name: str = "list") -> str:
    c = view["counters"]
    rows = "\n".join(row_html(p) for p in view["rows"])
    allocated = len(view["rows"])
    return shell(
        "BaggageGO · live board",
        f"""<header class="band board-band">
  {lockup()}
  <h1>Live board</h1>
  <div class="band-right">
    <span class="clock" id="clock"></span>
    <button id="reset" type="button" data-token="{esc(token)}">Reset board</button>
  </div>
</header>
{view_switch_html(view_name, big=big)}
<main class="view">
  <section class="board">
    <table>
      <thead>
        <tr><th>Seat</th><th>Passenger</th><th class="cell-code">Ticket</th>
            <th>Baggage</th><th class="cell-state">Scan</th></tr>
      </thead>
      <tbody>
{rows}
      </tbody>
    </table>
  </section>
  {cabin_html(view["seats"], scanned=c["scanned"], allocated=allocated, bins=view["bins"])}
</main>
{log_html(view["log"])}
<p class="hint" data-state="live">Live, updating every second.</p>
<script src="{asset_url(STATIC_DIR / 'board.js', f'{BASE}/static/board.js')}" defer></script>
""",
        big=big,
        page=f"board-page view-{view_name}",
    )


# --------------------------------------------------------------------- app

class ScanIn(BaseModel):
    code: str
    # how many of each bag, keyed by bag: {"bag-handbag": 1, "bag-backpack": 2}
    bags: dict[str, int] | None = None
    # shorthand for exactly one of the bag whose unit size this is — the API's
    # original single-bag shape, and the quickest thing to type from curl
    bag_units: float | None = None


class ControlIn(BaseModel):
    code: str
    # explicit counts, or a unit range the server fills from randomly — the trickle
    # sends the range so every arrival carries a plausible, varied load
    bags: dict[str, int] | None = None
    min_units: float | None = None
    max_units: float | None = None


class ControlBagIn(BaseModel):
    code: str
    bags: dict[str, int]


class ControlBulkIn(BaseModel):
    action: str
    codes: list[str] | None = None


class SnapshotIn(BaseModel):
    # deliberately loose: `import_state()` validates the rows and the version
    format: str | None = None
    version: int | None = None
    passengers: list[dict]
    scans: list[dict]


def create_app(db_path: Path | None = None) -> FastAPI:
    path = Path(db_path) if db_path is not None else DB_PATH
    init_db(path)
    # not a login: it is embedded in the board page and guards the reset POST
    # against cross-site forms, which cannot set a custom header.
    token = get_board_token(path)
    controls_token = get_controls_token(path)

    app = FastAPI(title="BaggageGO", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = path

    def require_control(request: Request) -> None:
        """The control API's only gate. The page embeds the token (behind ?k=),
        so the header is CSRF protection — a cross-site form cannot set it."""
        if not secrets.compare_digest(request.headers.get("X-Control-Token", ""), controls_token):
            raise HTTPException(status_code=403, detail="X-Control-Token required")

    @app.middleware("http")
    async def revalidate_assets(request: Request, call_next):
        """Assets are heuristically cacheable by default (`StaticFiles` sends no
        `Cache-Control`), so a code tweak could leave a browser on yesterday's
        CSS. A versioned URL already busts that; this makes the intent explicit
        and catches anything loaded without a version (icons, the font)."""
        response = await call_next(request)
        if request.url.path.startswith((f"{BASE}/static/", f"{BASE}/brand/")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    api = APIRouter()

    @api.post("/api/scan")
    def scan(payload: ScanIn, request: Request) -> JSONResponse:
        code = normalize_code(payload.code)
        mix = None
        if payload.bags is not None:
            mix = parse_mix(payload.bags)
        elif payload.bag_units is not None:
            bag = bag_for(payload.bag_units)
            mix = {bag.icon: 1} if bag else None
        if payload.bags is not None or payload.bag_units is not None:
            if mix is None:
                raise HTTPException(status_code=422, detail="not one of the four bags")
        sid, minted = sid_from_request(request)
        conn = connect(path)
        try:
            passenger = fetch_passenger(conn, code)
            record_scan(conn, code, passenger is not None, sid,
                        coarse_ua(request.headers.get("user-agent")), mix)
            conn.commit()
            # the advice is recomputed *after* the write, so the bag just recorded
            # is part of the picture the card is told about
            state = build_state(conn, advice=True) if passenger else None
        finally:
            conn.close()

        body: dict = {"status": "ok" if passenger else "unknown"}
        if passenger:
            body["passenger"] = passenger
            if state:
                body["stow"] = stow_for(state, passenger["seat_number"])
                # the card's map re-labels itself from this sentence (R9); the
                # ring and the gauge are re-pointed from `stow` itself
                body["cabin_label"] = card_cabin_label(state["seats"], body["stow"])
        if mix:
            body["bag"] = bag_summary(mix)
        resp = JSONResponse(body)
        if minted:
            set_sid_cookie(resp, sid)
        return resp

    @api.get("/api/state")
    def state() -> JSONResponse:
        # `no-store` on purpose: the board polls this every second, and a board left
        # open on the projector must never be served a cached cabin. The page is
        # already no-store; without a header here a proxy or a heuristic browser
        # cache could still pin the bins at whatever they were when it last looked.
        conn = connect(path)
        try:
            return JSONResponse(build_state(conn), headers={"Cache-Control": "no-store"})
        finally:
            conn.close()

    @api.post("/api/reset", status_code=204)
    def reset(request: Request) -> Response:
        """Back to an empty board: scans *and* every bag they carried (R21)."""
        if not secrets.compare_digest(request.headers.get("X-Board-Token", ""), token):
            raise HTTPException(status_code=403, detail="X-Board-Token required")
        conn = connect(path)
        try:
            reset_world(conn)
            conn.commit()
        finally:
            conn.close()
        return Response(status_code=204)

    # ------------------------------------------- the control page's API
    # Every one of these needs X-Control-Token. They are the only writers of
    # `scans` besides a real scan; the module still never touches `passengers`.

    @api.get("/api/ctl/passengers")
    def ctl_list(request: Request) -> JSONResponse:
        require_control(request)
        conn = connect(path)
        try:
            return JSONResponse({"passengers": ctl_rows(conn)})
        finally:
            conn.close()

    @api.post("/api/ctl/checkin")
    def ctl_checkin_endpoint(payload: ControlIn, request: Request) -> JSONResponse:
        require_control(request)
        code = normalize_code(payload.code)
        conn = connect(path)
        try:
            if fetch_passenger(conn, code) is None:
                raise HTTPException(status_code=404, detail="no such passenger")
            if payload.bags is not None:
                mix = parse_mix(payload.bags)
                if mix is None:
                    raise HTTPException(
                        status_code=422, detail="not one of the four bags, within its cap"
                    )
                ctl_set_bag(conn, code, mix)
            elif payload.min_units is not None or payload.max_units is not None:
                # the trickle: a fresh, random load inside the operator's range
                mix, _units = ctl_mix_in_range(payload.min_units, payload.max_units)
                if mix:
                    ctl_set_bag(conn, code, mix)
                else:
                    ctl_checkin(conn, code)
            else:
                ctl_checkin(conn, code)
            conn.commit()
            return JSONResponse({"status": "ok", "passenger": ctl_passenger(conn, code)})
        finally:
            conn.close()

    @api.post("/api/ctl/checkout")
    def ctl_checkout_endpoint(payload: ControlIn, request: Request) -> JSONResponse:
        require_control(request)
        code = normalize_code(payload.code)
        conn = connect(path)
        try:
            if fetch_passenger(conn, code) is None:
                raise HTTPException(status_code=404, detail="no such passenger")
            ctl_checkout(conn, code)
            conn.commit()
            return JSONResponse({"status": "ok", "passenger": ctl_passenger(conn, code)})
        finally:
            conn.close()

    @api.post("/api/ctl/bag")
    def ctl_bag_endpoint(payload: ControlBagIn, request: Request) -> JSONResponse:
        require_control(request)
        mix = parse_mix(payload.bags)
        if mix is None:
            raise HTTPException(status_code=422, detail="choose at least one of the four bags")
        code = normalize_code(payload.code)
        conn = connect(path)
        try:
            if fetch_passenger(conn, code) is None:
                raise HTTPException(status_code=404, detail="no such passenger")
            ctl_set_bag(conn, code, mix)
            conn.commit()
            return JSONResponse({"status": "ok", "passenger": ctl_passenger(conn, code)})
        finally:
            conn.close()

    @api.post("/api/ctl/bulk")
    def ctl_bulk_endpoint(payload: ControlBulkIn, request: Request) -> JSONResponse:
        require_control(request)
        conn = connect(path)
        try:
            affected = ctl_bulk(conn, payload.action, payload.codes)
            conn.commit()
            return JSONResponse({"status": "ok", "action": payload.action, "affected": affected})
        finally:
            conn.close()

    @api.get("/api/ctl/export")
    def ctl_export(request: Request) -> Response:
        """The whole board as a download. Header-gated like every control call,
        and `no-store` so a proxy never hands back yesterday's cabin."""
        require_control(request)
        conn = connect(path)
        try:
            snapshot = export_state(conn)
        finally:
            conn.close()
        filename = "baggagego-state-" + datetime.now(HKT).strftime("%Y%m%d-%H%M%S") + ".json"
        return Response(
            json.dumps(snapshot, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @api.post("/api/ctl/import")
    def ctl_import_endpoint(payload: SnapshotIn, request: Request) -> JSONResponse:
        require_control(request)
        conn = connect(path)
        try:
            counts = import_state(conn, payload.model_dump())
        finally:
            conn.close()
        return JSONResponse({"status": "ok", "counts": counts})

    app.include_router(api, prefix=BASE)
    app.include_router(api)

    if STATIC_DIR.is_dir():
        app.mount(f"{BASE}/static", StaticFiles(directory=STATIC_DIR), name="static")

    # The brand kit, served from where it lives. Three explicit routes rather than
    # a mount of the whole folder: the kit also holds provenance archives and the
    # licence-restricted Pluto Sans trial, and none of that belongs on the wire.
    @app.get(f"{BASE}/brand/brand.css")
    def brand_css() -> Response:
        return Response(BRAND_CSS.read_text(encoding="utf-8"), media_type="text/css")

    @app.get(f"{BASE}/brand/fonts/Montserrat-var.ttf")
    def brand_font() -> Response:
        return Response(BRAND_FONT.read_bytes(), media_type="font/ttf")

    if BRAND_ICONS.is_dir():
        app.mount(f"{BASE}/brand/icons", StaticFiles(directory=BRAND_ICONS), name="brand-icons")

    @app.get(BASE + "/")
    def base_redirect() -> RedirectResponse:
        return RedirectResponse(LIVE_PATH, status_code=302)

    @app.get(LIVE_PATH)
    def live(request: Request) -> HTMLResponse:
        # Open by design (2026-11-06): /api/state is unauthenticated anyway, so a
        # ?k= gate was decorative. Any ?k= a stale bookmark carries is ignored.
        big = request.query_params.get("big", "") in {"1", "true", "yes"}
        # the URL wins: an unknown ?view= is the list, never a 500 (Q3)
        view_name = request.query_params.get("view", "list").lower()
        if view_name not in {"list", "seats"}:
            view_name = "list"
        conn = connect(path)
        try:
            view = board_view(build_state(conn))
        finally:
            conn.close()
        return HTMLResponse(
            board_html(view, big=big, token=token, view_name=view_name),
            headers={"Cache-Control": "no-store"},
        )

    @app.get(f"{BASE}/controls")
    def controls(request: Request) -> HTMLResponse:
        """The operator console. Hidden behind the controls token, which is not
        the board token: the board token is embedded in the open /bggo/live, so
        it is public; this one only ever reaches a browser that already had it."""
        key = request.query_params.get("k", "")
        if not secrets.compare_digest(key, controls_token):
            return HTMLResponse(
                "<h1>403</h1><p>Control token required: open /bggo/controls?k=&lt;token&gt;.</p>",
                status_code=403,
            )
        conn = connect(path)
        try:
            passengers = ctl_rows(conn)
        finally:
            conn.close()
        return HTMLResponse(
            controls_html(passengers, token=controls_token),
            headers={"Cache-Control": "no-store"},
        )

    def passenger_page(raw_code: str, request: Request) -> HTMLResponse:
        code = normalize_code(raw_code)
        conn = connect(path)
        try:
            passenger = fetch_passenger(conn, code)
            scan = fetch_scan(conn, code) if passenger else None
            # the card's stowage advice comes from the same plan that colours the
            # board (R9/R20): the passenger's own stowed bin when a bag is on file,
            # else where a bag would go now. The card's cabin map is drawn from the
            # same state, so the ring on it cannot point at a zone the sentence
            # does not name.
            stow = None
            cabin = None
            if passenger:
                cabin = build_state(conn, advice=True)
                stow = stow_for(cabin, passenger["seat_number"])
        finally:
            conn.close()
        chosen = scan_mix(scan["bag_mix"]) if scan else None
        # the no-JS form bounced back here rather than showing a wall of JSON
        notice = None
        if request.query_params.get("bags") == "none":
            notice = "Choose at least one bag to check in."
        resp = HTMLResponse(
            passenger_html(passenger, code, chosen, notice=notice, stow=stow, cabin=cabin)
            if passenger
            else no_booking_html(code)
        )
        # the card must show the bag that was just chosen; never serve a stale one
        resp.headers["Cache-Control"] = "no-store"
        sid, minted = sid_from_request(request)
        if minted:
            set_sid_cookie(resp, sid)
        return resp

    @app.post(f"{BASE}/{{code}}/bag")
    async def choose_bag(code: str, request: Request) -> Response:
        """The picker's own POST: a plain form of counts, so the choice lands even
        with JS off (app.js intercepts the submit when JS is available). This is
        the path the numbered inputs fall back to when the steppers are inert.
        """
        normalized = normalize_code(code)
        fields = parse_qs((await request.body()).decode("utf-8", "replace"))
        counts = {bag.icon: (fields.get(bag.icon) or ["0"])[0] for bag in BAGS}
        mix = parse_mix(counts)
        if mix is None:
            # a form that came back empty is a mis-tap: send the person back to
            # the card with a line saying so, rather than a JSON error page.
            # Counts that are out of range or unknown stay a hard 422.
            if no_bags_chosen(counts):
                return RedirectResponse(f"{BASE}/{normalized}?bags=none", status_code=303)
            raise HTTPException(status_code=422, detail="not one of the four bags")
        sid, minted = sid_from_request(request)
        conn = connect(path)
        try:
            known = fetch_passenger(conn, normalized) is not None
            record_scan(conn, normalized, known, sid,
                        coarse_ua(request.headers.get("user-agent")), mix)
            conn.commit()
        finally:
            conn.close()
        resp = RedirectResponse(f"{BASE}/{normalized}", status_code=303)
        if minted:
            set_sid_cookie(resp, sid)
        return resp

    # Registered last: /bggo/live and /bggo/api/... must win over {code}.
    @app.get(f"{BASE}/{{code}}")
    def passenger_page_slashless(code: str, request: Request) -> HTMLResponse:
        return passenger_page(code, request)

    @app.get(f"{BASE}/{{code}}/")
    def passenger_page_trailing(code: str, request: Request) -> HTMLResponse:
        return passenger_page(code, request)

    return app


app = create_app()


if __name__ == "__main__":
    import sys

    if "--url" in sys.argv:
        print(board_url())
        print(f"http://127.0.0.1:{PORT}{LIVE_PATH}")
        raise SystemExit(0)

    if "--token" in sys.argv:
        # reserved for the 1.3 reset header; no longer needed to view the board
        print(get_board_token())
        raise SystemExit(0)

    if "--controls-token" in sys.argv:
        print(get_controls_token())
        raise SystemExit(0)

    if "--controls-url" in sys.argv:
        # the operator's bookmark — the token rides in ?k=
        print(controls_url())
        print(f"http://127.0.0.1:{PORT}{BASE}/controls?k={get_controls_token()}")
        raise SystemExit(0)

    if "--export" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--export") + 1])
        conn = connect(DB_PATH)
        try:
            snapshot = export_state(conn)
        finally:
            conn.close()
        out.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        print(f"wrote {out} · {snapshot['counts']['passengers']} passengers, "
              f"{snapshot['counts']['scans']} scans")
        raise SystemExit(0)

    if "--import" in sys.argv:
        src = Path(sys.argv[sys.argv.index("--import") + 1])
        snapshot = json.loads(src.read_text(encoding="utf-8"))
        conn = connect(DB_PATH)
        try:
            counts = import_state(conn, snapshot)
        finally:
            conn.close()
        print(f"imported {src} · {counts['passengers']} passengers, {counts['scans']} scans")
        raise SystemExit(0)

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
