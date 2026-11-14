#!/usr/bin/env python3
"""BaggageGO demo passenger database.

Design
------
* **240 slots.** The db always holds exactly 240 rows, id 1..240 — a full
  cabin: 40 rows of 3 + 3, every seat sold.
* **Codes are locked.** Each slot owns one ticket code (the QR payload), written
  once to ``passenger_codes.txt`` and never regenerated — not on rebuilds, not on
  name refreshes, not ever. The file is the source of truth for the lock, and it
  only ever grows by **appending** (``--grow-codes``), so widening the schema
  cannot invalidate a pass that is already printed.
* **The team roster is pinned.** Ids 1..5 always belong to the five named team
  members, with fixed seats (``TEAM_ROSTER``). No seed, ``--refresh`` or
  ``--count`` can move or rename them; they are constants, not dealt data.
* **Names are allocation, not identity.** A run fills ids 6..``--count`` with a
  unique full name + seat; every slot past that stays empty. Re-running re-deals
  names to the same codes and leaves the rest blank. Baggage volume is never
  touched by a name refresh (it is scan data).
* **Seats fill front to back.** ``id 6`` takes the first free seat (2A), ``id 7``
  takes 2B, and so on row by row. Seats are not dealt, so raising or lowering
  ``--count`` only adds or removes passengers at the back of the cabin — everyone
  who was already seated keeps their seat, and the boarding pass in their hand
  stays right. The team's row 1 is skipped, never handed out.

Schema
------
    id               INTEGER PK      -- 1..240, fixed
    code             TEXT UNIQUE     -- locked, printed on the ticket as a QR
    full_name        TEXT            -- NULL until allocated
    seat_number      TEXT UNIQUE     -- NULL until allocated
    baggage_volume_l REAL            -- NULL until the bag is scanned
    created_at       TEXT

Usage
-----
    python3 seed_passengers.py                  # team 1-5 + 15 dealt = 20 named
    python3 seed_passengers.py --count 240      # the full cabin: all 240 seats
    python3 seed_passengers.py --count 50       # team 1-5 + 45 dealt, 190 blank
    python3 seed_passengers.py --refresh        # new random names, same codes/seats
    python3 seed_passengers.py --check          # verify the deal (writes no names/seats)
    python3 seed_passengers.py --grow-codes     # append the codes a wider schema needs
    python3 seed_passengers.py --regen-codes    # DESTRUCTIVE: burn the lock
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "passengers.db"
CODES_PATH = HERE / "passenger_codes.txt"

SLOTS = 240                     # total passengers / locked codes (40 rows x 6)
DEFAULT_COUNT = 20              # slots that get a name + seat (including the team)
DEFAULT_SEED = 114514           # the deal everyone rehearses against

# --------------------------------------------------------------- team roster
# Hardcoded on purpose: these five ids and seats must survive every re-deal and
# every --refresh, so they are constants rather than rows drawn from the pools.
# Name order is surname-first, as on a Hong Kong travel document.
TEAM_ROSTER: dict[int, tuple[str, str]] = {
    1: ("Au Bai Qiao", "1A"),
    2: ("Hui Long Wai", "1B"),
    3: ("Lee Lok Sang", "1C"),
    4: ("Leung Tsz Hei", "1D"),
    5: ("Man Chor Fung", "1E"),
}
TEAM_COUNT = len(TEAM_ROSTER)
TEAM_SEATS = frozenset(seat for _, seat in TEAM_ROSTER.values())

# --------------------------------------------------------------------------
# Name pools: 40 given names x 40 Hong Kong surnames = 1,600 distinct full
# names, comfortably more than the 240 slots, so a deal never repeats a name.
# The pools are deliberately small — this is a demo manifest, not a
# passenger service system — and names repeat the way they do in Hong Kong:
# one surname, many passengers.
#
# Two hard rules, checked at import below:
#   * Surnames are Cantonese romanisations and must be in the boarding pass's
#     SURNAME_FIRST list (boarding-pass/make_passes_v1.py), or the IATA printer
#     would take the given name for the surname.
#   * No given name may also be a surname. Dealt names are given-first
#     ("Aaron Chan"), and the printer reads parts[0] as the surname when it
#     matches that list, so a given name like "Lee" would print "Lee/Brown".
# --------------------------------------------------------------------------
GIVEN_NAMES = (
    "Aaron", "Abigail", "Adrian", "Alice", "Amanda", "Amy", "Andrea", "Andrew",
    "Angela", "Anna", "Arthur", "Audrey", "Barbara", "Benjamin", "Bernard",
    "Bethany", "Blake", "Brenda", "Brian", "Bruce", "Catherine", "Charlotte",
    "Chloe", "Claire", "Clara", "Daniel", "Danielle", "David", "Diana",
    "Dominic", "Dorothy", "Dylan", "Emily", "Emma", "Eric", "Ethan", "Eva",
    "Evelyn", "Fiona", "Florence",
)

LAST_NAMES = (
    "Au", "Chan", "Cheng", "Cheung", "Chiu", "Choi", "Chow", "Chu", "Chung",
    "Fung", "Ho", "Hui", "Ip", "Ko", "Kwok", "Lai", "Lam", "Lau", "Law",
    "Lee", "Leung", "Li", "Lo", "Mak", "Man", "Ng", "Pang", "Poon", "Shum",
    "Siu", "So", "Sze", "Tam", "Tang", "Tsang", "Tse", "Wong", "Wu", "Yeung",
    "Yip",
)

# fail loudly at import if the pools ever drift
for _pool, _label in ((GIVEN_NAMES, "given"), (LAST_NAMES, "surname")):
    if len(set(_pool)) != len(_pool):
        _dupes = sorted({n for n in _pool if _pool.count(n) > 1})
        raise SystemExit(f"duplicate {_label} names: {_dupes}")
if len(GIVEN_NAMES) * len(LAST_NAMES) < SLOTS:
    raise SystemExit(
        f"name pools hold {len(GIVEN_NAMES) * len(LAST_NAMES)} distinct full names, "
        f"need at least {SLOTS}"
    )
_clash = {n.lower() for n in GIVEN_NAMES} & {n.lower() for n in LAST_NAMES}
if _clash:
    raise SystemExit(
        f"a given name is also a surname: {sorted(_clash)} — the boarding pass "
        "printer would read it as the surname"
    )

# No I/L/O/U: unambiguous read aloud or off a ticket when the scanner dies.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_PREFIX = "BGGO"

SEAT_LETTERS = "ABCDEF"
SEAT_ROWS = range(1, 41)        # 240 seats, 235 of them dealable (team has row 1)
# The team's fixed seats are removed from the deal pool, never overwritten.
# Order matters: this tuple *is* the allocation order — row by row from the
# front of the cabin (2A, 2B, … 2F, 3A …), so a passenger's seat never moves
# when --count is scaled up or down.
DEAL_SEATS = tuple(
    f"{row}{letter}"
    for row in SEAT_ROWS
    for letter in SEAT_LETTERS
    if f"{row}{letter}" not in TEAM_SEATS
)

SCHEMA = """
CREATE TABLE passengers (
    id               INTEGER PRIMARY KEY,        -- 1..240, fixed slot order
    code             TEXT NOT NULL UNIQUE,       -- locked ticket code
    full_name        TEXT,                       -- NULL until allocated
    seat_number      TEXT UNIQUE,                -- NULL until allocated
    baggage_volume_l REAL,                       -- NULL until the bag is scanned
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ---------------------------------------------------------------- codes (lock)

def _new_code(rng: random.Random) -> str:
    body = "".join(rng.choice(CODE_ALPHABET) for _ in range(8))
    return f"{CODE_PREFIX}-{body[:4]}-{body[4:]}"


def _mint(count: int, taken: set[str]) -> list[str]:
    """`count` fresh codes that are not in `taken` (updated in place)."""
    rng = random.Random()
    fresh: list[str] = []
    while len(fresh) < count:
        code = _new_code(rng)
        if code not in taken:
            taken.add(code)
            fresh.append(code)
    return fresh


def load_or_create_codes(path: Path = CODES_PATH, regen: bool = False,
                         grow: bool = False) -> list[str]:
    """Read the locked code list, creating it exactly once.

    The list is **append-only**: widening the schema must never re-code a slot,
    because the code is already printed inside every pass handed out. A file that
    is short is therefore not topped up silently — a truncated file and a widened
    schema look identical from here, and guessing wrong re-codes every ticket
    after the cut. Minting is an explicit act: ``grow=True`` / ``--grow-codes``.
    """
    if path.exists() and not regen:
        codes = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
        if len(codes) > SLOTS:
            raise SystemExit(
                f"{path.name} has {len(codes)} codes, more than the {SLOTS} slots — "
                "the lock file is longer than the schema; refusing to guess"
            )
        if len(codes) < SLOTS:
            if not grow:
                raise SystemExit(
                    f"{path.name} has {len(codes)} codes, expected {SLOTS}. Codes are "
                    f"append-only: run with --grow-codes to mint the missing "
                    f"{SLOTS - len(codes)} and leave the existing ones alone (a "
                    "truncated file would otherwise re-code every later slot)."
                )
            codes += _mint(SLOTS - len(codes), set(codes))
            path.write_text("\n".join(codes) + "\n")
        if len(set(codes)) != SLOTS:
            raise SystemExit(f"{path.name} contains duplicate codes")
        return codes

    codes = _mint(SLOTS, set())
    path.write_text("\n".join(codes) + "\n")
    return codes


# ---------------------------------------------------------------- db plumbing

def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db(codes: list[str], db_path: Path = DB_PATH, rebuild: bool = False) -> None:
    """Create the empty slots if absent; widen an existing db by **appending**.

    A db written by a narrower schema is grown, never rebuilt: ``--rebuild``
    deletes the file and takes the live board's scan state with it, which is not
    a cost a schema widening should impose. The db must stay a *prefix* of the
    lock file — that is what makes appending safe.
    """
    if rebuild and db_path.exists():
        db_path.unlink()
    conn = _connect(db_path)
    try:
        conn.executescript(SCHEMA.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))
        existing = conn.execute("SELECT id, code FROM passengers ORDER BY id").fetchall()
        if existing:
            stored = [r["code"] for r in existing]
            if ([r["id"] for r in existing] != list(range(1, len(stored) + 1))
                    or stored != codes[: len(stored)]):
                raise SystemExit(
                    f"{db_path.name} does not match {CODES_PATH.name}. Codes are "
                    "locked and append-only, so the db must be a prefix of the code "
                    "file — rebuild with --rebuild, or restore the codes file."
                )
            if len(stored) < len(codes):
                conn.executemany(
                    "INSERT INTO passengers (id, code) VALUES (?, ?)",
                    [(i + 1, code) for i, code in enumerate(codes[len(stored):], len(stored))],
                )
                conn.commit()
            return
        conn.executemany(
            "INSERT INTO passengers (id, code) VALUES (?, ?)",
            [(i + 1, code) for i, code in enumerate(codes)],
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- allocation

def allocate(count: int, seed: int, db_path: Path = DB_PATH) -> list[dict]:
    """Pin the team to ids 1..5, deal ids 6..count, blank every other slot.

    Names come from the seed; **seats do not** — they are the first ``count -
    TEAM_COUNT`` seats of ``DEAL_SEATS``, front to back, so the deal is stable
    across ``--count`` changes and only the names move on ``--refresh``.
    Slots > count are cleared, not deleted: they keep their locked code.
    baggage_volume_l is deliberately left alone (it is scan data, not demo data).
    """
    _validate_count(count)
    demo_count = count - TEAM_COUNT
    seats = DEAL_SEATS[:demo_count]

    rng = random.Random(seed)
    # Given-first, as the dealt names have always been ("Aaron Chan"); the pass
    # printer's last-word rule then puts the surname in front of the slash.
    combos = [f"{given} {surname}" for given in GIVEN_NAMES for surname in LAST_NAMES]
    rng.shuffle(combos)

    rows = [
        {"id": pid, "full_name": name, "seat_number": seat}
        for pid, (name, seat) in TEAM_ROSTER.items()
    ]
    rows += [
        {
            "id": TEAM_COUNT + 1 + i,
            "full_name": combos[i],
            "seat_number": seats[i],
        }
        for i in range(demo_count)
    ]

    conn = _connect(db_path)
    try:
        conn.execute("UPDATE passengers SET full_name = NULL, seat_number = NULL")
        conn.executemany(
            "UPDATE passengers SET full_name = :full_name, seat_number = :seat_number "
            "WHERE id = :id",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return rows


# ---------------------------------------------------------------- read paths

def lookup(code: str, db_path: Path = DB_PATH) -> dict | None:
    """What the scanner does at the gate: ticket code -> passenger record."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT code, full_name, seat_number, baggage_volume_l FROM passengers "
            "WHERE code = ?",
            (code.strip().upper(),),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _validate_count(count: int) -> None:
    if not TEAM_COUNT <= count <= SLOTS:
        raise SystemExit(
            f"--count must be {TEAM_COUNT}..{SLOTS}: ids 1-{TEAM_COUNT} are the "
            "fixed team roster and are always allocated"
        )


def check(count: int, db_path: Path = DB_PATH) -> None:
    _validate_count(count)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, code, full_name, seat_number, baggage_volume_l "
            "FROM passengers ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == SLOTS, f"expected {SLOTS} slots, found {len(rows)}"
    assert [r["id"] for r in rows] == list(range(1, SLOTS + 1)), f"ids are not 1..{SLOTS}"
    codes = [r["code"] for r in rows]
    assert len(set(codes)) == SLOTS, "duplicate codes"
    for code in codes:
        assert code.startswith("BGGO-") and len(code) == 14, f"bad code format: {code}"
        assert all(c in CODE_ALPHABET for c in code[5:].replace("-", "")), f"bad chars: {code}"

    filled, empty = rows[:count], rows[count:]
    for pid, (name, seat) in TEAM_ROSTER.items():
        r = rows[pid - 1]
        assert r["full_name"] == name, f"slot {pid} should be {name}, has {r['full_name']}"
        assert r["seat_number"] == seat, f"slot {pid} should be seat {seat}, has {r['seat_number']}"
    for r in rows[TEAM_COUNT:count]:
        assert r["full_name"], f"slot {r['id']} should have a name"
        given, _, surname = r["full_name"].rpartition(" ")
        assert given in GIVEN_NAMES, f"unknown given name: {r['full_name']}"
        assert surname in LAST_NAMES, f"unknown surname: {r['full_name']}"
        assert r["seat_number"] in DEAL_SEATS, f"seat {r['seat_number']} is reserved or invalid"
    dealt_seats = [r["seat_number"] for r in rows[TEAM_COUNT:count]]
    assert dealt_seats == list(DEAL_SEATS[: count - TEAM_COUNT]), (
        "seats are not filled front to back: "
        f"expected {list(DEAL_SEATS[: count - TEAM_COUNT])}, have {dealt_seats}"
    )
    for r in empty:
        assert r["full_name"] is None, f"slot {r['id']} should be empty, has {r['full_name']}"
        assert r["seat_number"] is None, f"slot {r['id']} should have no seat"
    assert len({r["full_name"] for r in filled}) == count, "duplicate full names"
    assert len({r["seat_number"] for r in filled}) == count, "duplicate seats"
    assert all(r["baggage_volume_l"] is None for r in rows), "baggage should still be empty"


# ---------------------------------------------------------------- cli

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=DEFAULT_COUNT,
                    help=f"slots to name + seat, incl. the team at 1-{TEAM_COUNT} "
                         f"(default {DEFAULT_COUNT} of {SLOTS})")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="name-deal seed")
    ap.add_argument("--refresh", action="store_true",
                    help="random new names (codes and seats unchanged)")
    ap.add_argument("--check", action="store_true",
                    help="verify the deal and write no names/seats (the db may still be "
                         "created or extended to the current slot count)")
    ap.add_argument("--rebuild", action="store_true", help="recreate the db from the locked codes")
    ap.add_argument("--regen-codes", action="store_true",
                    help="DESTRUCTIVE: discard the locked codes and mint new ones")
    ap.add_argument("--grow-codes", action="store_true",
                    help=f"mint only the codes {CODES_PATH.name} is missing "
                         "(append-only: existing codes and printed passes untouched)")
    args = ap.parse_args()

    if args.regen_codes:
        print("regenerating ticket codes — every printed ticket is now invalid", file=sys.stderr)
        codes = load_or_create_codes(regen=True)
    else:
        had_codes = CODES_PATH.exists()
        codes = load_or_create_codes(grow=args.grow_codes)
        if not had_codes:
            print(f"minted {SLOTS} ticket codes, locked in {CODES_PATH.name}")
        elif args.grow_codes:
            print(f"{CODES_PATH.name} now holds {len(codes)} codes (append-only)")
    ensure_db(codes, rebuild=args.rebuild)

    if args.check:
        check(args.count)
        print(f"OK: {SLOTS} slots in {DB_PATH.name}, {args.count} allocated")
        return 0

    seed = random.SystemRandom().randrange(1 << 32) if args.refresh else args.seed
    rows = allocate(args.count, seed)
    check(args.count)

    width = max((len(r["full_name"]) for r in rows), default=0)
    print(f"{args.count}/{SLOTS} slots allocated  (seed {seed})")
    print(f"{'id':>3}  {'code':<15} {'name':<{width}}  seat")
    for r in rows:
        print(f"{r['id']:>3}  {codes[r['id'] - 1]:<15} {r['full_name']:<{width}}  {r['seat_number']}")
    if args.count < SLOTS:
        print(f"     … slots {args.count + 1}-{SLOTS}: code only, name/seat empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
