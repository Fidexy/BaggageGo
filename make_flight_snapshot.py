#!/usr/bin/env python3
"""Build a demo snapshot: the frozen 240-seat cabin, 150 passengers boarded.

The output is the same JSON `GET /bggo/api/ctl/export` downloads and
`POST /bggo/api/ctl/import` (or `server_v1.py --import FILE`) consumes, so it
can be fed to the operator console's *Import state* picker as-is.

Fixed inputs, all asserted below:

* **the manifest is the whole aircraft** — all 240 locked slots carry a name and
  a seat (the seeder's fixed deal), so `passengers[]` is the full 40-row cabin
  and the seat map can never shrink. Cabin capacity is derived from the highest
  allocated row, so freezing the list at 240 freezes the capacity at 240;
  boarding 150 does not shrink the plane.
* **150 of the 240 are boarded** — `scans` holds exactly 150 rows, so the board
  reads "150 of 240 scanned" and 90 passengers are still waiting.
* **seat 1D is not boarded** — id 4 (Leung Tsz Hei) has no scan row; he is one
  of the 90 waiting.
* **no zone above 7 units** — every overhead zone (four seat rows, one wall)
  boards 7 or 8 of its 12 passengers and gets a bag budget capped at 6.5 units
  (the brief's 7-unit limit), under the 8-unit advice line, so every zone
  renders green (<= 2.0) or yellow (> 2.0 and < 8.0) and no bag is redirected.

The manifest is rebuilt from `passenger_codes.txt` (the append-only lock) and
the seeder's fixed deal, so the script does **not** read `passengers.db`: that
file is live demo state and another session may be re-dealing it. The boarding
draw and the bag loads are dealt deterministically (fixed seeds and fixed
timestamps), so re-running the script reproduces the same file.
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import seed_passengers as seed  # noqa: E402
import server_v1 as S  # noqa: E402

OUT_PATH = HERE / "snapshots" / "flight-150.json"

SEATS = 240                             # the lock: 40 rows x 3 + 3
BOARDED = 150                           # passengers on board (the rest wait)
NO_SHOW_SEAT = "1D"                     # id 4, Leung Tsz Hei
MAX_ZONE_UNITS = 6.5                    # <= 7 by requirement, < STOW_AWAY_UNITS (8) keeps every zone green/yellow
ZONE_FLOOR = 7                          # boarded per zone before the spare 10 are dealt

# Fixed booking/boarding clock (UTC); stored timestamps are UTC, the board
# renders HKT. Fixed rather than `now` so the file is reproducible.
BOOKED_AT = "2026-11-07T00:00:00+00:00"
BOARDING_START = datetime(2026, 11, 10, 15, 20, 0, tzinfo=timezone.utc)


def manifest() -> list[dict]:
    """The full 240-slot manifest: locked codes in order, the seeder's fixed
    deal (team row 1, then names dealt front to back).

    Built in a throwaway db from `passenger_codes.txt` + the seeder's seed, so
    the snapshot never depends on the live `passengers.db` — a parallel session
    may have re-dealt that file to a different count.
    """
    codes = seed.load_or_create_codes()
    with tempfile.TemporaryDirectory(prefix="bggo-snapshot-") as tmp:
        path = Path(tmp) / "manifest.db"
        seed.ensure_db(codes, db_path=path)
        seed.allocate(seed.SLOTS, seed.DEFAULT_SEED, db_path=path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, code, full_name, seat_number FROM passengers ORDER BY id"
                )
            ]
        finally:
            conn.close()
    if len(rows) != SEATS:
        raise SystemExit(f"the lock holds {len(rows)} slots, need {SEATS}")
    if any(not r["full_name"] or not r["seat_number"] for r in rows):
        raise SystemExit("the manifest must be fully allocated, 1..240")
    return rows


def quarter_bags(rng: random.Random, quarters: int) -> dict[str, int]:
    """A bag mix worth `quarters` quarter-units, in the picker's own keys.

    The four bags are 0.25 / 0.5 / 0.75 / 1.0, i.e. 1-4 quarters, so a
    passenger's total is a whole number of them. Some 0.75/1.0 totals are split
    into two smaller bags the way a real cabin bag plus personal item would be.
    """
    if quarters == 0:
        return {}
    if quarters == 1:
        return {"bag-handbag": 1}
    if quarters == 2:
        return {"bag-backpack": 1}
    if quarters == 3:
        return (
            {"bag-duffel": 1}
            if rng.random() < 0.7
            else {"bag-handbag": 1, "bag-backpack": 1}
        )
    if quarters == 4:
        return (
            {"bag-roller": 1}
            if rng.random() < 0.6
            else {"bag-handbag": 1, "bag-duffel": 1}
        )
    raise ValueError(f"one passenger cannot carry {quarters} quarters")


def zone_target(rng: random.Random, index: int, seats: int) -> float:
    """A bag budget for one zone, in whole 0.25 units (the smallest bag).

    Every third zone is kept green (<= 2.0 units) so the board shows both ramp
    colours on the same cabin; the rest fill toward yellow. The cap is
    `MAX_ZONE_UNITS`, and the floor is whatever the zone's passengers can
    actually carry at one unit each.
    """
    if seats == 0:
        return 0.0
    if index % 3 == 2:
        target = min(2.0, max(0.5, 0.22 * seats))
    else:
        target = max(2.5, 0.60 * seats + rng.uniform(0.4, 1.8))
    target = min(target, MAX_ZONE_UNITS, 4.0 * seats)
    return round(target * 4) / 4


def deal_zone(rng: random.Random, target: float, seats: list[dict]) -> dict[int, dict]:
    """Split `target` units across the zone's boarded passengers, one bag each
    (some get none). Keeps every passenger at <= 1.0 units — the biggest bag
    the picker offers — and the zone total at exactly the target (all targets
    are a multiple of 0.25, the smallest bag)."""
    if target > MAX_ZONE_UNITS:
        raise SystemExit(f"target {target} exceeds the {MAX_ZONE_UNITS}-unit cap")
    quarters = round(target * 4)
    if abs(quarters / 4 - target) > 1e-9:
        raise SystemExit(f"target {target} is not a whole number of 0.25 units")
    n = len(seats)
    if quarters > 4 * n:
        raise SystemExit(f"{n} passengers cannot carry {quarters} quarters")
    # Most passengers carry something (the sold-seat average is ~0.4 units); a
    # zone whose budget is narrower than one handbag per person leaves the rest
    # empty-handed rather than inventing a bigger bag.
    bagged = max((quarters + 3) // 4, min(n, quarters))
    if bagged == 0:
        return {p["id"]: {} for p in seats}
    if bagged > n:
        raise SystemExit(f"zone needs {bagged} bags but has {n} passengers")

    chosen = rng.sample(seats, bagged)
    wallet = {p["id"]: 1 for p in chosen}
    remaining = quarters - bagged
    order = [p["id"] for p in chosen]
    rng.shuffle(order)
    # Hand out the spare quarters in random chunks rather than one by one, so a
    # zone shows the whole ladder (a 1-unit trolley beside a 0.25 handbag) and
    # not a cabin of identical half-full backpacks.
    index = 0
    while remaining:
        pid = order[index % len(order)]
        room = 4 - wallet[pid]
        if room:
            take = rng.randint(1, min(room, remaining))
            wallet[pid] += take
            remaining -= take
        index += 1
    mixes: dict[int, dict] = {p["id"]: {} for p in seats}
    for pid, q in wallet.items():
        mixes[pid] = quarter_bags(rng, q)
    return mixes


def choose_boarded(rows: list[dict]) -> list[dict]:
    """150 of the 240 allocated passengers board, spread evenly enough that every
    zone carries bags: each of the 20 zones boards 7, and ten of them board 8.

    Seats are pre-assigned, so a half-full cabin is not filled front to back;
    the per-zone draw is seeded per zone and seat 1D is never among the picked.
    """
    row_max = max(S.parse_seat(r["seat_number"])[0] for r in rows)
    zones: dict[tuple[int, str], list[dict]] = {}
    for r in rows:
        row, letter = S.parse_seat(r["seat_number"])
        zones.setdefault(S.bin_for_seat(row_max, row, letter), []).append(r)
    keys = sorted(zones)
    rng = random.Random("bggo-boarding-spread")
    counts = {k: ZONE_FLOOR for k in keys}
    spare = BOARDED - sum(counts.values())
    if spare < 0 or spare > len(keys):
        raise SystemExit(f"{BOARDED} does not fit {len(keys)} zones from {ZONE_FLOOR} each")
    for k in rng.sample(keys, spare):
        counts[k] += 1

    picked: list[dict] = []
    for k in keys:
        eligible = [r for r in zones[k] if r["seat_number"] != NO_SHOW_SEAT]
        if counts[k] > len(eligible):
            raise SystemExit(f"zone {k} has {len(eligible)} boarding seats, needs {counts[k]}")
        picked += random.Random(f"bggo-board-{k[0]}-{k[1]}").sample(eligible, counts[k])
    if len(picked) != BOARDED:
        raise SystemExit(f"picked {len(picked)} boarders, need {BOARDED}")
    if len({r["seat_number"] for r in picked}) != BOARDED:
        raise SystemExit("boarding draw is not one passenger per seat")
    if any(r["seat_number"] == NO_SHOW_SEAT for r in picked):
        raise SystemExit(f"seat {NO_SHOW_SEAT} must not be boarded")
    return sorted(picked, key=lambda r: r["id"])


def build() -> dict:
    rows = manifest()
    boarded = choose_boarded(rows)

    row_max = max(S.parse_seat(r["seat_number"])[0] for r in rows)
    zones: dict[tuple[int, str], list[dict]] = {}
    for r in boarded:
        row, letter = S.parse_seat(r["seat_number"])
        zones.setdefault(S.bin_for_seat(row_max, row, letter), []).append(r)
    if len(zones) != 20:
        raise SystemExit(f"expected 20 zones on {row_max} rows, got {len(zones)}")

    bags: dict[int, dict] = {}
    for index, key in enumerate(sorted(zones)):
        members = zones[key]
        rng = random.Random(f"bggo-{key[0]}-{key[1]}")
        target = zone_target(rng, index, len(members))
        mixes = deal_zone(rng, target, members)
        if sum(S.mix_units(m) or 0 for m in mixes.values()) != target:
            raise SystemExit(f"zone {key} did not reach its target")
        bags.update(mixes)

    # Boarding order is a deterministic shuffle (a real queue is not seat
    # order); timestamps are fixed and strictly increasing by scan id.
    scans = []
    moment = BOARDING_START
    order = boarded[:]
    random.Random("bggo-boarding-order").shuffle(order)
    rng = random.Random("bggo-boarding-clock")
    for index, passenger in enumerate(order, start=1):
        mix = bags.get(passenger["id"]) or None
        units = S.mix_units(mix)
        scans.append(
            {
                "id": index,
                "code": passenger["code"],
                "known": 1,
                "first_seen_at": moment.isoformat(timespec="microseconds"),
                "last_seen_at": moment.isoformat(timespec="microseconds"),
                "hits": 1,
                "session_id": None,
                "ua": None,
                "bag_units": units,
                "bag_mix": json.dumps(mix, sort_keys=True) if mix else None,
            }
        )
        moment += timedelta(seconds=rng.randint(4, 14))

    passengers = [
        {
            "id": r["id"],
            "code": r["code"],
            "full_name": r["full_name"],
            "seat_number": r["seat_number"],
            "baggage_volume_l": None,
            "created_at": BOOKED_AT,
        }
        for r in rows
    ]
    return {
        "format": S.SNAPSHOT_FORMAT,
        "version": S.SNAPSHOT_VERSION,
        "exported_at": (moment + timedelta(seconds=60)).isoformat(timespec="microseconds"),
        "counts": {"passengers": len(passengers), "scans": len(scans)},
        "passengers": passengers,
        "scans": scans,
    }


def main() -> None:
    snapshot = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(
        f"wrote {OUT_PATH} — {len(snapshot['passengers'])} seats, "
        f"{snapshot['counts']['scans']} boarded"
    )


if __name__ == "__main__":
    main()
