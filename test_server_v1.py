"""Stage 1 — deliverables 1.1 (live board) and 1.2 (scan -> passenger page).

Every test runs against a throwaway copy of the *real* deal: the locked code
file is read, a temp db is built from it, and the default seed re-deals 20 rows
— the fixed team at ids 1-5, then 15 drawn names. Test values below
(Au Bai Qiao / 1A) come from that default deal.
"""

from __future__ import annotations

import json
import inspect
import pathlib
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import seed_passengers as seed
import server_v1

UTC = timezone.utc
HKT = timezone(timedelta(hours=8))

# Default deal (seed 20261115): team ids 1-5, then dealt names from id 6.
KNOWN = "BGGO-ZWY9-YCJS"   # slot 1  Au Bai Qiao    1A
KNOWN2 = "BGGO-HPZH-WAJ7"  # slot 2  Hui Long Wai   1B
BOGUS = "BGGO-9999-9999"

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)$")


# --------------------------------------------------------------- fixtures

@pytest.fixture()
def codes() -> list[str]:
    return seed.load_or_create_codes()


@pytest.fixture()
def db(tmp_path, codes):
    """A freshly-built copy of the demo db, 20 allocated slots."""
    path = tmp_path / "passengers.db"
    seed.ensure_db(codes, db_path=path)
    seed.allocate(seed.DEFAULT_COUNT, seed.DEFAULT_SEED, db_path=path)
    return path


@pytest.fixture()
def app(db):
    return server_v1.create_app(db_path=db)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def board_token(db) -> str:
    server_v1.init_db(db)  # idempotent; makes the token readable without a client
    return server_v1.get_board_token(db_path=db)


def rows(db, sql, params=()):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


# ------------------------------------------------- team roster (seed contract)

def test_team_roster_is_pinned_to_ids_1_to_5(tmp_path, codes):
    """No seed, --refresh or re-deal may move or rename the five team members."""
    path = tmp_path / "team.db"
    seed.ensure_db(codes, db_path=path)
    expected = [(pid, name, seat) for pid, (name, seat) in seed.TEAM_ROSTER.items()]

    for deal_seed in (seed.DEFAULT_SEED, 1, 999_999):
        seed.allocate(seed.DEFAULT_COUNT, deal_seed, db_path=path)
        seed.check(seed.DEFAULT_COUNT, db_path=path)
        assert rows(
            path,
            "SELECT id, full_name, seat_number FROM passengers "
            "WHERE id BETWEEN 1 AND 5 ORDER BY id",
        ) == expected
        # the drawn rows never steal a team seat
        assert rows(
            path,
            "SELECT COUNT(*) FROM passengers "
            "WHERE seat_number IN ('1A','1B','1C','1D','1E') AND id > 5",
        ) == [(0,)]


def test_count_cannot_drop_the_team(tmp_path, codes):
    path = tmp_path / "team.db"
    seed.ensure_db(codes, db_path=path)
    with pytest.raises(SystemExit):
        seed.allocate(seed.TEAM_COUNT - 1, seed.DEFAULT_SEED, db_path=path)


# ------------------------------------------------- 1.2 POST /api/scan

def test_scan_known_code_returns_the_passenger(client):
    r = client.post("/bggo/api/scan", json={"code": KNOWN})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["passenger"] == {
        "code": KNOWN,
        "full_name": "Au Bai Qiao",
        "seat_number": "1A",
        "baggage_volume_l": None,
    }


def test_scan_is_case_insensitive_and_trimmed(client, db):
    r = client.post("/bggo/api/scan", json={"code": f"  {KNOWN.lower()}  "})
    assert r.json()["status"] == "ok"
    assert r.json()["passenger"]["code"] == KNOWN
    # stored under the canonical code, not the raw payload
    assert rows(db, "SELECT code FROM scans") == [(KNOWN,)]


def test_rescan_of_one_ticket_bumps_hits_and_keeps_one_row(client, db):
    client.post("/bggo/api/scan", json={"code": KNOWN})
    r = client.post("/bggo/api/scan", json={"code": KNOWN})
    assert r.json()["status"] == "ok"
    assert rows(db, "SELECT code, hits FROM scans") == [(KNOWN, 2)]

    state = client.get("/bggo/api/state").json()
    jade = state["passengers"][0]
    assert jade["hits"] == 2 and jade["scanned"] is True
    assert state["counters"]["scanned"] == 1
    assert state["counters"]["hits"] == 2


def test_scan_of_unallocated_slot_is_unknown(client, codes):
    slot21 = codes[20]  # first unallocated code
    r = client.post("/bggo/api/scan", json={"code": slot21})
    assert r.status_code == 200
    assert r.json()["status"] == "unknown"


def test_scan_of_code_not_in_db_is_unknown(client):
    r = client.post("/bggo/api/scan", json={"code": BOGUS})
    assert r.status_code == 200
    assert r.json()["status"] == "unknown"


def test_unknown_scan_is_flagged_and_never_a_passenger(client, codes, db):
    slot21 = codes[20]
    client.post("/bggo/api/scan", json={"code": slot21})
    assert rows(db, "SELECT code, known FROM scans") == [(slot21, 0)]
    state = client.get("/bggo/api/state").json()
    assert len(state["passengers"]) == 20
    assert state["counters"]["scanned"] == 0
    assert state["counters"]["unknown"] == 1


def test_repeated_unknown_code_counts_once(client, codes):
    slot21 = codes[20]
    client.post("/bggo/api/scan", json={"code": slot21})
    client.post("/bggo/api/scan", json={"code": slot21})
    state = client.get("/bggo/api/state").json()
    assert state["counters"]["unknown"] == 1


def test_scan_never_writes_to_passengers(client, db):
    client.post("/bggo/api/scan", json={"code": KNOWN})
    client.post("/bggo/api/scan", json={"code": BOGUS})
    assert rows(db, "SELECT COUNT(*) FROM passengers WHERE full_name IS NOT NULL") == [(20,)]
    assert rows(db, "SELECT COUNT(*) FROM passengers WHERE baggage_volume_l IS NOT NULL") == [(0,)]
    assert rows(db, "SELECT full_name FROM passengers WHERE code = ?", (KNOWN,)) == [("Au Bai Qiao",)]


def test_scan_sets_httponly_session_cookie(client):
    r = client.post("/bggo/api/scan", json={"code": KNOWN})
    cookie = r.headers["set-cookie"]
    assert "sid=" in cookie and "HttpOnly" in cookie


# ------------------------------------------------- 1.1 GET /api/state

def test_state_lists_the_twenty_allocated_passengers(client):
    state = client.get("/bggo/api/state").json()
    assert state["allocated"] == 20
    assert len(state["passengers"]) == 20
    first, second = state["passengers"][:2]
    assert (first["code"], first["full_name"], first["seat_number"]) == (KNOWN, "Au Bai Qiao", "1A")
    assert (second["code"], second["full_name"]) == (KNOWN2, "Hui Long Wai")
    assert first["baggage_volume_l"] is None
    assert first["scanned"] is False and first["hits"] == 0 and first["last_seen"] is None
    assert state["counters"] == {"scanned": 0, "hits": 0, "unknown": 0, "last_scan_age_s": None}
    assert ISO_UTC.match(state["server_now"])


def test_state_marks_only_the_scanned_passenger(client):
    client.post("/bggo/api/scan", json={"code": KNOWN2})
    state = client.get("/bggo/api/state").json()
    scanned = [p for p in state["passengers"] if p["scanned"]]
    assert [p["code"] for p in scanned] == [KNOWN2]
    assert ISO_UTC.match(scanned[0]["last_seen"])
    assert state["counters"]["last_scan_age_s"] is not None
    assert state["counters"]["last_scan_age_s"] < 60


def test_state_is_read_only(client, db):
    before = rows(db, "SELECT code, full_name, seat_number FROM passengers ORDER BY id")
    client.get("/bggo/api/state")
    assert rows(db, "SELECT code, full_name, seat_number FROM passengers ORDER BY id") == before
    assert rows(db, "SELECT COUNT(*) FROM scans") == [(0,)]


# ------------------------------------------- 1.2 GET /bggo/{code} page

def test_passenger_page_renders_name_seat_and_baggage(client):
    r = client.get(f"/bggo/{KNOWN}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Au Bai Qiao" in r.text
    assert "1A" in r.text
    assert KNOWN in r.text
    assert "not recorded" in r.text.lower()


def test_passenger_page_is_case_insensitive(client):
    assert client.get(f"/bggo/{KNOWN.lower()}").status_code == 200


def test_passenger_page_tolerates_trailing_slash(client):
    r = client.get(f"/bggo/{KNOWN}/")
    assert r.status_code == 200
    assert "Au Bai Qiao" in r.text


def test_passenger_page_sets_session_cookie(client):
    r = client.get(f"/bggo/{KNOWN}")
    assert "sid=" in r.headers.get("set-cookie", "")


def test_passenger_page_for_unallocated_slot_says_no_booking(client, codes):
    r = client.get(f"/bggo/{codes[20]}")
    assert r.status_code == 200
    assert "no booking" in r.text.lower()
    assert "Au Bai Qiao" not in r.text


def test_passenger_page_for_bogus_code_says_no_booking(client):
    r = client.get(f"/bggo/{BOGUS}")
    assert r.status_code == 200
    assert "no booking" in r.text.lower()


# --------------------------------------------------- 1.1 GET /bggo/live

def test_board_is_open(client):
    """No token: the board URL is a plain bookmark. /api/state was already open,
    so gating the page never protected anything (reverted 2026-11-06)."""
    r = client.get("/bggo/live")
    assert r.status_code == 200
    assert "Au Bai Qiao" in r.text


def test_board_ignores_a_stale_token_param(client, board_token):
    """URLs bookmarked while ?k= was required must keep working."""
    assert client.get("/bggo/live", params={"k": board_token}).status_code == 200
    assert client.get("/bggo/live", params={"k": "wrong-token"}).status_code == 200
    assert client.get("/bggo/live", params={"k": ""}).status_code == 200


def test_board_renders_the_passenger_list(client):
    r = client.get("/bggo/live")
    assert r.status_code == 200
    assert "Au Bai Qiao" in r.text and "1A" in r.text and KNOWN in r.text
    assert "Hui Long Wai" in r.text


def test_board_token_is_still_minted_once_into_passengers_db(db, board_token):
    """Reserved for the 1.3 reset header (§3.3) — still locked, just not a gate."""
    assert len(board_token) >= 20
    stored = rows(db, "SELECT key, value FROM settings WHERE key = 'board_token'")
    assert stored == [("board_token", board_token)]
    # a restart reads the same token back out of the db, it does not mint a new one
    with TestClient(server_v1.create_app(db_path=db)) as second:
        assert second.get("/bggo/live").status_code == 200
    assert len(rows(db, "SELECT * FROM settings WHERE key = 'board_token'")) == 1


def test_base_path_redirects_to_the_board(client):
    r = client.get("/bggo/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].rstrip("/").endswith("/bggo/live")


# ------------------------------------------------------------------ misc

def test_api_is_also_reachable_at_the_root_prefix(client):
    """NPM may expose /bggo/ only; keep the plan's bare /api/ paths working too."""
    r = client.post("/api/scan", json={"code": KNOWN})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert client.get("/api/state").json()["allocated"] == 20


def test_board_url_is_the_presenter_bookmark():
    assert server_v1.board_url() == "https://sophon1.dedyn.io/bggo/live"
    assert server_v1.board_url(base="https://example.test") == "https://example.test/bggo/live"


# -------------------------------------------- 1.3: counters, highlight, reset

def test_age_text_is_relative_under_a_minute_then_hkt_clock():
    seen = "2026-11-06T09:29:10+00:00"  # 17:29:10 HKT
    assert server_v1.age_text(seen, now=datetime(2026, 11, 6, 9, 29, 43, tzinfo=UTC)) == "33s ago"
    assert server_v1.age_text(seen, now=datetime(2026, 11, 6, 9, 31, 0, tzinfo=UTC)) == "17:29:10"
    assert server_v1.age_text(seen, now=datetime(2026, 11, 6, 9, 29, 9, tzinfo=UTC)) == "0s ago"
    assert server_v1.age_text(None) == "—"


def test_repeat_hits_counter_excludes_the_first_scan_of_each_ticket(client, codes):
    for code in (KNOWN, KNOWN, KNOWN2, codes[20]):
        client.post("/bggo/api/scan", json={"code": code})
    c = client.get("/bggo/api/state").json()["counters"]
    assert c["scanned"] == 2 and c["hits"] == 3 and c["unknown"] == 1
    assert server_v1.repeat_hits(c) == 1  # 3 hits - 2 tickets


def test_the_board_carries_no_counter_bar_only_the_feed(client, codes):
    """A strip of telemetry above the map said what the map and the feed already
    say, and it cost the cabin 2.5rem of height. The numbers stay in /api/state
    (the controls page and any other client still read them); the page drops them."""
    for code in (KNOWN, KNOWN, KNOWN2, codes[20]):
        client.post("/bggo/api/scan", json={"code": code})
    state = client.get("/bggo/api/state").json()["counters"]
    assert state["scanned"] == 2 and state["hits"] == 3 and state["unknown"] == 1
    html = client.get("/bggo/live").text
    assert 'class="counters"' not in html
    for gone in ("c-scanned", "c-hits", "c-unknown", "c-age"):
        assert gone not in html
    assert ".counters" not in (server_v1.STATIC_DIR / "style.css").read_text()


def test_most_recent_scan_is_pinned_to_the_top_and_accented(client):
    client.post("/bggo/api/scan", json={"code": KNOWN})     # Au Bai Qiao, earlier
    client.post("/bggo/api/scan", json={"code": KNOWN2})    # Hui Long Wai, most recent
    html = client.get("/bggo/live").text
    assert html.index("Hui Long Wai") < html.index("Au Bai Qiao") < html.index("Lee Lok Sang")
    assert re.search(r'class="row scanned recent"[^>]*data-index="2"', html)
    assert re.search(r'class="row scanned"[^>]*data-index="1"', html)  # lit, not accented
    assert re.search(r'class="row"[^>]*data-index="3"', html)          # still dim


def test_repeat_scan_moves_that_row_back_to_the_top(client):
    client.post("/bggo/api/scan", json={"code": KNOWN})
    client.post("/bggo/api/scan", json={"code": KNOWN2})
    client.post("/bggo/api/scan", json={"code": KNOWN})
    html = client.get("/bggo/live").text
    assert html.index("Au Bai Qiao") < html.index("Hui Long Wai")
    assert re.search(r'class="row scanned recent"[^>]*data-index="1"', html)
    # server-rendered on first paint (R9), not JS-only: a projector must never
    # show a stale ×N between the scan and the next poll
    assert '<span class="hits">×2</span>' in html


def test_board_with_no_scans_has_no_highlight_at_all(client):
    html = client.get("/bggo/live").text
    assert "scanned recent" not in html
    assert 'class="counters"' not in html


def test_every_row_carries_a_stable_data_index(client):
    """board.js reorders on this — it must not depend on current DOM order."""
    html = client.get("/bggo/live").text
    assert [m for m in re.findall(r'data-index="(\d+)"', html)] == [str(i) for i in range(1, 21)]


def test_reset_requires_the_board_token_header(client, db, board_token):
    client.post("/bggo/api/scan", json={"code": KNOWN})
    assert client.post("/bggo/api/reset").status_code == 403
    assert client.post("/bggo/api/reset", headers={"X-Board-Token": "wrong"}).status_code == 403
    assert rows(db, "SELECT COUNT(*) FROM scans") == [(1,)]  # untouched


def test_reset_clears_scans_and_nothing_else(client, db, board_token, codes):
    client.post("/bggo/api/scan", json={"code": KNOWN})
    client.post("/bggo/api/scan", json={"code": codes[20]})
    r = client.post("/bggo/api/reset", headers={"X-Board-Token": board_token})
    assert r.status_code == 204
    assert rows(db, "SELECT COUNT(*) FROM scans") == [(0,)]
    assert rows(db, "SELECT COUNT(*) FROM passengers WHERE full_name IS NOT NULL") == [(20,)]
    assert rows(db, "SELECT COUNT(*) FROM passengers WHERE baggage_volume_l IS NOT NULL") == [(0,)]
    state = client.get("/bggo/api/state").json()
    assert state["counters"] == {"scanned": 0, "hits": 0, "unknown": 0, "last_scan_age_s": None}
    assert not any(p["scanned"] for p in state["passengers"])


def test_reset_clears_a_bag_that_no_scan_row_owns(client, db, board_token):
    """A bag normally lives on the scan row, so DELETE FROM scans takes it.
    `passengers.baggage_volume_l` is the one place a figure can outlive a reset,
    and the board prints it in the Baggage column — so reset has to reach it too
    (R21). Passengers, names, seats and tickets stay.
    """
    con = sqlite3.connect(db)
    con.execute("UPDATE passengers SET baggage_volume_l = 27 WHERE code = ?", (KNOWN,))
    con.commit()
    con.close()
    assert "27 L" in client.get("/bggo/live").text

    r = client.post("/bggo/api/reset", headers={"X-Board-Token": board_token})
    assert r.status_code == 204
    assert rows(db, "SELECT COUNT(*) FROM passengers WHERE baggage_volume_l IS NOT NULL") == [(0,)]
    assert "27 L" not in client.get("/bggo/live").text
    assert rows(db, "SELECT COUNT(*) FROM passengers WHERE full_name IS NOT NULL") == [(20,)]


def test_the_controls_clear_button_is_the_same_reset(client, db, board_token, app):
    """Two doors, one operation: the console's Clear is not a second definition
    of what resetting means (it must clear the passenger-row bag as well)."""
    con = sqlite3.connect(db)
    con.execute("UPDATE passengers SET baggage_volume_l = 27 WHERE code = ?", (KNOWN,))
    con.commit()
    con.close()
    client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-roller": 1}})
    token = server_v1.get_controls_token(db_path=db)
    r = client.post("/bggo/api/ctl/bulk", json={"action": "clear"},
                    headers={"X-Control-Token": token})
    assert r.status_code == 200 and r.json()["affected"] == 1
    assert rows(db, "SELECT COUNT(*) FROM scans") == [(0,)]
    assert rows(db, "SELECT COUNT(*) FROM passengers WHERE baggage_volume_l IS NOT NULL") == [(0,)]


def test_reset_is_idempotent(client, board_token):
    h = {"X-Board-Token": board_token}
    assert client.post("/bggo/api/reset", headers=h).status_code == 204
    assert client.post("/bggo/api/reset", headers=h).status_code == 204


def test_reset_also_answers_at_the_bare_api_prefix(client, board_token):
    client.post("/bggo/api/scan", json={"code": KNOWN})
    r = client.post("/api/reset", headers={"X-Board-Token": board_token})
    assert r.status_code == 204
    assert client.get("/bggo/api/state").json()["counters"]["scanned"] == 0


def test_same_second_scans_are_still_ordered_by_time(client, db):
    """Regression (found 2026-11-06): with second-resolution stamps two scans
    inside the same second tie, `max()` kept the first, and the pin silently fell
    back to seat order. Ordering must come from the stamp, not the seat."""
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO scans (code, known, first_seen_at, last_seen_at, hits) "
        "VALUES (?, 1, ?, ?, 1)",
        [
            (KNOWN, "2026-11-06T09:29:10.100000+00:00", "2026-11-06T09:29:10.100000+00:00"),
            (KNOWN2, "2026-11-06T09:29:10.900000+00:00", "2026-11-06T09:29:10.900000+00:00"),
        ],
    )
    con.commit()
    con.close()
    html = client.get("/bggo/live").text
    assert html.index("Hui Long Wai") < html.index("Au Bai Qiao")
    assert re.search(r'class="row scanned recent"[^>]*data-index="2"', html)


def test_scan_stamps_carry_sub_second_precision(client, db):
    client.post("/bggo/api/scan", json={"code": KNOWN})
    client.post("/bggo/api/scan", json={"code": KNOWN2})
    stamps = [r[0] for r in rows(db, "SELECT last_seen_at FROM scans ORDER BY id")]
    assert "." in stamps[0], "second resolution cannot order two scans in one second"
    assert stamps[0] != stamps[1]


def test_board_page_carries_the_reset_token_and_a_button(client, board_token):
    html = client.get("/bggo/live").text
    assert board_token in html
    assert 'id="reset"' in html


def test_reset_token_never_reaches_the_audience_page(client, board_token):
    for path in (f"/bggo/{KNOWN}", f"/bggo/{KNOWN}/", "/bggo/"):
        r = client.get(path, follow_redirects=False)
        assert board_token not in r.text


# ------------------------------------------- HK Express livery (brand kit)
# The brand kit's own rule: brand.json is the only file holding a literal hex.
# These tests are that rule, enforced on the demo's side.

def test_pages_link_the_generated_brand_css_before_our_own(client):
    for path in (f"/bggo/live", f"/bggo/{KNOWN}"):
        html = client.get(path).text
        assert re.search(rf'href="{re.escape(server_v1.BASE)}/brand/brand\.css\?v=\d+"', html)
        assert html.index("brand/brand.css") < html.index("static/style.css"), (
            "brand.css must come first: style.css only reads its tokens"
        )


def test_assets_are_versioned_and_revalidated(client):
    """The page is `no-store`, but `StaticFiles` sends no `Cache-Control`, so a
    browser heuristically caches the CSS/JS and a fresh page renders against the
    previous UI — found live 2026-11-07 (the seat map fell out below the list and
    the view links looked dead). The HTML must reference the current mtime, and
    the asset responses must say no-cache."""
    for page in (client.get("/bggo/live").text, client.get(f"/bggo/{KNOWN}").text):
        assert re.search(r'href="/bggo/brand/brand\.css\?v=\d+"', page)
        assert re.search(r'href="/bggo/static/style\.css\?v=\d+"', page)
    assert re.search(r'src="/bggo/static/board\.js\?v=\d+"', client.get("/bggo/live").text)
    assert re.search(r'src="/bggo/static/app\.js\?v=\d+"', client.get(f"/bggo/{KNOWN}").text)
    for path in ("/bggo/static/style.css", "/bggo/static/board.js",
                 "/bggo/brand/brand.css", "/bggo/brand/fonts/Montserrat-var.ttf"):
        assert client.get(path).headers.get("cache-control") == "no-cache", path


def test_served_brand_css_is_the_brand_kit_file_not_a_copy(client):
    """Serving the source of truth, not a stale copy, is the whole point."""
    served = client.get("/bggo/brand/brand.css")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/css")
    assert "--hke-purple:#702B91" in served.text
    assert served.text == (server_v1.BRAND_DIR / "brand.css").read_text(encoding="utf-8")


def test_brand_font_and_lockup_are_self_hosted(client):
    """Venue wifi is a single point of failure: no CDN, no family fallback."""
    font = client.get("/bggo/brand/fonts/Montserrat-var.ttf")
    assert font.status_code == 200
    assert font.content[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true")

    lockup = client.get("/bggo/brand/icons/baggagego-icon-white.svg")
    assert lockup.status_code == 200
    assert b"<svg" in lockup.content


def test_static_style_carries_no_literal_hex():
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    assert found == [], f"use var(--hke-*) from brand.css instead of {found}"


def test_rendered_pages_carry_no_literal_hex(client):
    for path in (f"/bggo/live", f"/bggo/{KNOWN}", "/bggo/BGGO-9999-9999"):
        assert re.findall(r"#[0-9a-fA-F]{3,8}\b", client.get(path).text) == []


def test_montserrat_is_self_hosted_by_file_path():
    """brand.json: load by explicit file path, never by family-name fallback."""
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    start = css.index("@font-face")
    face = css[start : css.index("}", start)]
    assert "Montserrat" in face
    assert "url(/bggo/brand/fonts/Montserrat-var.ttf)" in face


def test_board_and_card_carry_the_lockup(client):
    assert "brand/icons/baggagego-icon-white.svg" in client.get("/bggo/live").text
    assert "brand/icons/baggagego-icon-white.svg" in client.get(f"/bggo/{KNOWN}").text


def test_the_card_page_wears_the_app_chrome(client):
    """The ticket page is a screen inside the airline's app: the wordmark bar
    above it, the app's five tabs below, the BaggageGO card untouched between.
    The order *is* the layout, so it is asserted as an order, not as three
    independent strings."""
    html = client.get(f"/bggo/{KNOWN}").text
    assert html.index('class="appbar"') < html.index('class="card-wrap"') < html.index('class="tabbar"')
    assert '<svg class="hke-wordmark"' in html
    # the seat is pinned in the bar, not only drawn large on the card
    assert re.search(r'class="appbar-seat"[^>]*>.*?<b>1A</b>', html, re.S)
    assert html.count('class="tab is-on"') == 1
    assert 'Check-in' in html  # the current screen: where the app keeps the e-boarding pass


def test_the_app_chrome_stays_off_the_board(client):
    """The board is an operations surface on a projector, not a phone app."""
    html = client.get("/bggo/live").text
    assert 'class="appbar"' not in html
    assert 'class="tabbar"' not in html
    assert 'hke-wordmark' not in html


def test_the_no_booking_screen_keeps_the_chrome_without_a_seat(client):
    """A ticket that is not on the manifest still opens inside the app; there
    is no passenger, so there is no seat and the bar must not invent one."""
    html = client.get(f"/bggo/{BOGUS}").text
    assert 'class="appbar"' in html and 'class="tabbar"' in html
    assert 'class="appbar-seat"' not in html


def test_the_pass_block_carries_the_paper_facts(client):
    """The pass block above the perforation is the artifact's own composition —
    masthead, route, identity, fields — and every flight fact in it is the
    paper's (see FLIGHT), because a judge will be holding the paper."""
    html = client.get(f"/bggo/{KNOWN}").text
    head = html[:html.index('class="tear"')]
    assert re.search(r'class="pass-flight"><b>UO 628</b> <span>Sun 15 Nov</span>', head)
    assert re.search(r'class="route-code">HKG<', head)
    assert re.search(r'class="route-code">HND<', head)
    assert re.search(r'class="route-line"', head) and 'currentColor' in head
    assert re.search(r'class="route-city">Hong Kong<', head)
    assert re.search(r'class="route-city is-to">Tokyo-Haneda<', head)
    assert re.search(r"<dt>Departs</dt><dd>19:55</dd>", head)
    assert re.search(r"<dt>Gate</dt><dd>204</dd>", head)
    assert re.search(r"<dt>Boards</dt><dd>19:10</dd>", head)
    # the seat figure is still the card's own, on the name's line
    assert re.search(r'class="identity">.*?<b>1A</b>', head, re.S)


def test_the_flight_facts_agree_with_the_printed_pass():
    """The paper is locked and cannot be reissued, so the page agrees with it.
    Skipped when boarding-pass/ is not beside this tree: a standalone copy of
    the demo has no paper to agree with."""
    template = pathlib.Path(__file__).resolve().parent.parent / "boarding-pass" / "template.html"
    if not template.is_file():
        pytest.skip("no ../boarding-pass — standalone copy, nothing to compare against")
    paper = template.read_text(encoding="utf-8")
    # the paper prints "UO 628 15Nov", "1955" and "19:55"-style times; compare
    # the facts against its own value cells, not the typography
    assert f'>{server_v1.FLIGHT["no"]}' in paper
    assert "15Nov" in paper.replace(" ", "")
    assert "15 Nov" in server_v1.FLIGHT["date"]
    for value in (server_v1.FLIGHT["departs"], server_v1.FLIGHT["boards"],
                  server_v1.FLIGHT["gate"]):
        assert f'>{value.replace(":", "")}<' in paper, f"{value} is not on the pass"


def test_state_cell_has_separate_hits_and_age_elements(client):
    """board.js updates these two nodes instead of rewriting the whole cell."""
    client.post("/bggo/api/scan", json={"code": KNOWN})
    html = client.get("/bggo/live").text
    assert re.search(r'class="row scanned recent"[^>]*>.*?<span class="hits"></span>', html, re.S)
    assert re.search(r'<span class="age">0s ago</span>', html)


# ------------------------------------------- the bag picker (stage 2 starts)
# The ticket URL used to check you in on load. Now it draws a translucent
# overlay and asks which bags you are bringing: a stepper per bag, a running
# total, and one confirm button. Nothing is recorded until Confirm, so the
# choice is the check-in. Volume is in abstract "units" until a real per-bag
# litre figure is agreed.

BAG_UNITS = [0.25, 0.5, 0.75, 1]
BAG_NAMES = ["Handbag", "Backpack", "Duffel bag", "Small trolley case"]
BAG_ICONS = ["bag-handbag", "bag-backpack", "bag-duffel", "bag-roller"]


def test_card_asks_for_a_bag_and_does_not_check_in_on_load(client):
    html = client.get(f"/bggo/{KNOWN}").text
    assert 'id="picker"' in html, "the overlay must be in the server-rendered card"
    for units in BAG_UNITS:
        assert f'data-units="{units:g}"' in html, f"{units} is missing from the picker"
    for name in BAG_NAMES:
        assert name in html
    # a JS-less GET is not a check-in (link previews must not inflate the board)
    assert client.get("/bggo/api/state").json()["counters"]["scanned"] == 0


def test_every_bag_has_a_quantity_field_and_the_confirm_is_at_the_bottom(client):
    html = client.get(f"/bggo/{KNOWN}").text
    inputs = {
        m.group(1): m.group(0) for m in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*>', html)
    }
    for key in BAG_ICONS:
        tag = inputs[key]  # each bag posts its own count
        assert 'type="number"' in tag and 'value="0"' in tag
        assert f'data-key="{key}"' in tag, "the stepper needs to find its input"
        limit = next(b.limit for b in server_v1.BAGS if b.icon == key)
        assert f'max="{limit}"' in tag, "the input carries the bag's own cap"
    assert 'id="confirm"' in html and "Confirm" in html
    # the button is the last thing in the panel
    assert html.index('id="confirm"') > html.index('name="bag-duffel"')
    # and it is NOT server-disabled: a disabled attribute could never be lifted
    # again with JS off, which would strand the whole no-JS path
    assert "disabled" not in html.split('id="confirm"')[1].split(">")[0]


def test_the_steppers_are_buttons_the_script_can_wire_up(client):
    html = client.get(f"/bggo/{KNOWN}").text
    assert html.count('class="plus"') == 4 and html.count('class="minus"') == 4
    assert html.count('class="qty"') == 4
    assert html.count('class="stepper"') == 4


def test_picker_offers_four_in_style_bag_icons(client):
    html = client.get(f"/bggo/{KNOWN}").text
    assert html.count('class="bag-icon"') == 4
    for name in BAG_ICONS:
        svg = (server_v1.ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
        assert "currentColor" in svg, "icons take their colour from CSS, not the file"
        assert re.findall(r"#[0-9a-fA-F]{3,8}", svg) == [], "no literal colour in an icon"


def test_the_units_are_a_ladder_from_a_quarter_bag_to_a_whole_one():
    """0.25 → 1.0, one step per bag, and the soft duffel below the rigid case:
    a duffel can be squashed into the space it is given, a hard case cannot."""
    units = [b.units for b in server_v1.BAGS]
    assert units == [0.25, 0.5, 0.75, 1.0]
    assert units == sorted(units), "the picker reads as a ladder, small to large"
    by_name = {b.name: b.units for b in server_v1.BAGS}
    assert by_name["Small trolley case"] == 1.0, "a rigid case costs a whole unit"
    assert by_name["Duffel bag"] < by_name["Small trolley case"]
    assert server_v1.unit_text(1.0) == "1 unit", "singular, not '1 units'"
    assert server_v1.unit_text(0.25) == "0.25 units"


def test_choosing_a_bag_records_the_units_on_the_scan(client, db):
    r = client.post("/bggo/api/scan", json={"code": KNOWN, "bag_units": 1})
    assert r.status_code == 200
    assert r.json()["bag"]["text"] == "1 unit"
    assert r.json()["bag"]["mix_text"] == "Small trolley case"
    assert rows(db, "SELECT bag_units FROM scans WHERE code = ?", (KNOWN,)) == [(1.0,)]
    assert client.get("/bggo/api/state").json()["counters"]["scanned"] == 1


def test_quantities_add_up_into_the_units(client, db):
    r = client.post(
        "/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-handbag": 1, "bag-backpack": 2}}
    )
    assert r.status_code == 200
    assert r.json()["bag"]["units"] == 1.25
    assert r.json()["bag"]["text"] == "1.25 units"
    assert r.json()["bag"]["mix_text"] == "Handbag, Backpack ×2"
    assert rows(db, "SELECT bag_units, bag_mix FROM scans WHERE code = ?", (KNOWN,)) == [
        (1.25, '{"bag-backpack": 2, "bag-handbag": 1}')
    ]


def test_a_bag_quantity_above_the_cap_is_refused(client, db):
    r = client.post(
        "/bggo/api/scan",
        json={"code": KNOWN, "bags": {"bag-handbag": server_v1.MAX_PER_BAG + 1}},
    )
    assert r.status_code == 422
    assert rows(db, "SELECT * FROM scans") == []


def test_the_mix_is_capped_at_three_items_in_total(client, db):
    """The scenario is "what you carry on", so the ceiling is pieces, not per bag:
    four handbags is as much a no as three trolleys."""
    assert server_v1.MAX_ITEMS == 3
    r = client.post(
        "/bggo/api/scan",
        json={"code": KNOWN, "bags": {"bag-handbag": 2, "bag-backpack": 2}},
    )
    assert r.status_code == 422
    assert rows(db, "SELECT * FROM scans") == []
    # three spread across three bags is fine, and the total is three pieces
    ok = client.post(
        "/bggo/api/scan",
        json={"code": KNOWN, "bags": {"bag-handbag": 1, "bag-backpack": 1, "bag-duffel": 1}},
    )
    assert ok.status_code == 200
    assert ok.json()["bag"]["mix_text"] == "Handbag, Backpack, Duffel bag"
    stored = server_v1.scan_mix(
        rows(db, "SELECT bag_mix FROM scans WHERE code = ?", (KNOWN,))[0][0])
    assert stored == {"bag-handbag": 1, "bag-backpack": 1, "bag-duffel": 1}
    assert sum(stored.values()) == 3


def test_the_total_cap_holds_on_the_form_and_the_gate_too(client, db, controls_token):
    r = client.post(
        f"/bggo/{KNOWN}/bag",
        data={"bag-handbag": "2", "bag-backpack": "2"}, follow_redirects=False,
    )
    assert r.status_code == 422
    r = client.post("/bggo/api/ctl/bag",
                    json={"code": KNOWN, "bags": {"bag-handbag": 2, "bag-backpack": 2}},
                    headers=ctl(controls_token))
    assert r.status_code == 422
    assert rows(db, "SELECT * FROM scans") == []


def test_the_running_total_counts_items_not_bags():
    """A person carries *items*: the total line counts pieces, and both copies of
    the dialogue say the same word."""
    assert '<b id="total">no items yet</b>' in server_v1.picker_html(KNOWN, None)
    assert '<b id="total">1 item</b>' in server_v1.picker_html(
        KNOWN, {"bag-handbag": 1})
    assert '<b id="total">3 items</b>' in server_v1.picker_html(
        KNOWN, {"bag-handbag": 1, "bag-backpack": 1, "bag-duffel": 1})
    assert '<b id="ctl-total">no items yet</b>' in server_v1.control_picker_html()
    for name in ("app.js", "control.js"):
        js = (server_v1.STATIC_DIR / name).read_text(encoding="utf-8")
        assert '" item"' in js and '" items"' in js, f"{name} still says bags"


def test_the_picker_says_how_many_items_the_scenario_allows(client, controls_token):
    """The rule is on the panel in words, and the number the script clamps to is
    read off the same element (no second copy to drift)."""
    line = f"Choose up to {server_v1.MAX_ITEMS} items for this simulated boarding scenario."
    for page in (client.get(f"/bggo/{KNOWN}").text,
                 client.get("/bggo/controls", params={"k": controls_token}).text):
        assert line in page
        assert f'data-max-items="{server_v1.MAX_ITEMS}"' in page
    for name in ("app.js", "control.js"):
        js = (server_v1.STATIC_DIR / name).read_text(encoding="utf-8")
        assert "dataset.maxItems" in js, f"{name} does not read the total cap"
        assert "items >= " in js, f"{name} does not stop the steppers at the cap"


def test_the_random_bag_fill_respects_both_caps():
    """One deal of 1..3 pieces, each bag inside its own limit — the fill is a
    rehearsal prop, so it must produce a list the gate would accept."""
    for seed in range(200):
        mix = server_v1.ctl_random_mix(random.Random(seed))
        assert 1 <= sum(mix.values()) <= server_v1.MAX_ITEMS, mix
        for key, qty in mix.items():
            assert qty <= next(b.limit for b in server_v1.BAGS if b.icon == key)
        assert server_v1.parse_mix(mix) == mix


def test_a_small_trolley_case_is_capped_at_two(client, db):
    """A rigid case is a whole unit and a whole compartment: three of them is not
    one person's cabin luggage. The cap is per bag, so the soft bags keep 3."""
    caps = {b.icon: b.limit for b in server_v1.BAGS}
    assert caps == {"bag-handbag": 3, "bag-backpack": 3, "bag-duffel": 3,
                    "bag-roller": 2}
    r = client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-roller": 3}})
    assert r.status_code == 422
    assert rows(db, "SELECT * FROM scans") == []
    ok = client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-roller": 2}})
    assert ok.status_code == 200 and ok.json()["bag"]["text"] == "2 units"


def test_the_trolley_cap_also_holds_for_the_form_and_the_gate(client, db, controls_token):
    """Both ways in go through parse_mix, so the cap cannot be sidestepped by
    posting the no-JS form or the controls page's edit."""
    r = client.post(
        f"/bggo/{KNOWN}/bag",
        data={"bag-roller": "3"}, follow_redirects=False,
    )
    assert r.status_code == 422
    r = client.post("/bggo/api/ctl/bag", json={"code": KNOWN, "bags": {"bag-roller": 3}},
                    headers=ctl(controls_token))
    assert r.status_code == 422
    assert rows(db, "SELECT * FROM scans") == []


def test_a_choice_stored_under_an_older_cap_still_renders(client, db):
    """Tightening a cap must not blank an existing scan: the stored three-case
    choice is history, so it reads back even though it could not be made today."""
    client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-handbag": 1}})
    con = sqlite3.connect(db)
    try:
        con.execute("UPDATE scans SET bag_mix = ?, bag_units = 3 WHERE code = ?",
                    ('{"bag-roller": 3}', KNOWN))
        con.commit()
    finally:
        con.close()
    html = client.get(f"/bggo/{KNOWN}").text
    # the counts are the passenger's, the arithmetic is the board's (2026-11-11)
    assert "Small trolley case ×3" in html and "3 units" not in html


def test_an_unknown_bag_key_is_refused(client, db):
    r = client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-hat": 1}})
    assert r.status_code == 422
    assert rows(db, "SELECT * FROM scans") == []


def test_confirming_nothing_is_not_a_choice(client, db):
    r = client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-handbag": 0}})
    assert r.status_code == 422
    assert rows(db, "SELECT * FROM scans") == []


def test_the_bags_are_specific_types_not_airline_categories():
    """Everything here is carried on, so "checked bag" would be a lie, and the
    fabric is the interesting part: a handbag is not a duffel."""
    names = [b.name for b in server_v1.BAGS]
    assert names == BAG_NAMES
    for category in ("cabin", "checked", "oversize", "personal item"):
        assert not any(category in n.lower() for n in names), f"{category} is a category, not a bag"


def test_a_bag_that_is_not_one_of_the_four_is_refused(client, db):
    r = client.post("/bggo/api/scan", json={"code": KNOWN, "bag_units": 3})
    assert r.status_code == 422
    assert rows(db, "SELECT * FROM scans") == []


def test_the_picker_is_a_real_form_so_it_works_without_javascript(client, db):
    """Counts as form fields: with JS off the same panel still posts a choice."""
    r = client.post(
        f"/bggo/{KNOWN}/bag",
        data={"bag-handbag": "2", "bag-backpack": "1", "bag-roller": "0", "bag-duffel": "0"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith(f"/bggo/{KNOWN}")
    assert rows(db, "SELECT bag_units, bag_mix FROM scans WHERE code = ?", (KNOWN,)) == [
        (1.0, '{"bag-backpack": 1, "bag-handbag": 2}')
    ]


def test_the_form_route_refuses_a_bag_that_is_not_on_the_list(client, db):
    r = client.post(f"/bggo/{KNOWN}/bag", data={"bag-handbag": "9"}, follow_redirects=False)
    assert r.status_code == 422
    assert rows(db, "SELECT * FROM scans") == []


def test_an_empty_form_choice_comes_back_to_the_card_not_a_json_error(client, db):
    """With JS off the confirm button is live from the start, so an empty submit is
    a plausible mis-tap: it must land on the card with a reason, not a 422 wall."""
    r = client.post(f"/bggo/{KNOWN}/bag", data={"bag-handbag": "0"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith(f"/bggo/{KNOWN}?bags=none")
    assert rows(db, "SELECT * FROM scans") == []
    assert "Choose at least one bag" in client.get(f"/bggo/{KNOWN}?bags=none").text


def test_card_stops_asking_once_a_bag_is_chosen(client):
    client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-duffel": 1}})
    html = client.get(f"/bggo/{KNOWN}").text
    assert "Duffel bag" in html and "0.75 units" not in html
    assert "hidden" in html.split('id="picker"')[1].split(">")[0]
    assert "Change bag" not in html


def test_a_rescanned_card_keeps_the_counts_it_recorded(client):
    """The picker comes back pre-filled from the stored mix, not from zero."""
    client.post(
        "/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-roller": 2, "bag-handbag": 1}}
    )
    html = client.get(f"/bggo/{KNOWN}").text
    inputs = {
        m.group(1): m.group(0) for m in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*>', html)
    }
    assert 'value="2"' in inputs["bag-roller"]
    assert 'value="1"' in inputs["bag-handbag"]
    assert 'value="0"' in inputs["bag-backpack"]
    assert "3 items" in html, "the total is rendered as a count, not left to the script"
    assert "2.25 units" not in html, "a passenger never sees a unit figure"
    assert "disabled" not in html.split('id="confirm"')[1].split(">")[0]


def test_the_card_carries_no_change_bag_control(client):
    """The passenger view is the card, not an editor: corrections belong to the
    gate's controls page, so the card ships no button, no id and no styling for
    one — and app.js does not look for it."""
    html = client.get(f"/bggo/{KNOWN}").text
    assert "Change bag" not in html and 'id="change"' not in html
    assert "picker" in html  # the picker itself is still rendered (JS off posts it)
    css = (server_v1.STATIC_DIR / "style.css").read_text()
    assert ".change" not in css
    js = (server_v1.STATIC_DIR / "app.js").read_text()
    assert 'getElementById("change")' not in js


def test_rescanning_without_a_bag_keeps_the_choice(client, db):
    """The JSON API's units are optional; a plain re-scan must not erase the bag."""
    client.post("/bggo/api/scan", json={"code": KNOWN, "bag_units": 1})
    client.post("/bggo/api/scan", json={"code": KNOWN})
    assert rows(db, "SELECT bag_units, hits FROM scans WHERE code = ?", (KNOWN,)) == [(1.0, 2)]


def test_board_shows_the_total_units_of_a_mix(client):
    client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-handbag": 3}})
    assert "0.75 units" in client.get("/bggo/live").text


def test_state_exposes_the_bag_for_every_poll(client):
    client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-handbag": 1}})
    state = client.get("/bggo/api/state").json()
    first = state["passengers"][0]
    assert first["bag"]["text"] == "0.25 units"
    assert first["bag"]["mix_text"] == "Handbag"
    assert state["passengers"][1]["bag"] is None


def test_no_booking_page_offers_no_bags(client):
    html = client.get(f"/bggo/{BOGUS}").text
    assert 'id="picker"' not in html


def test_an_unknown_ticket_can_still_be_logged_without_a_bag(client, db):
    client.post("/bggo/api/scan", json={"code": BOGUS})
    assert rows(db, "SELECT known, bag_units FROM scans WHERE code = ?", (BOGUS,)) == [(0, None)]


def test_init_migrates_a_scans_table_from_before_the_picker(tmp_path):
    """The live db predates the picker: init must ALTER it, not need a rebuild
    (`--rebuild` would wipe the board state)."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
            known INTEGER NOT NULL, first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 1,
            session_id TEXT, ua TEXT);
        INSERT INTO scans (code, known, first_seen_at, last_seen_at)
        VALUES ('BGGO-ZWY9-YCJS', 1, '2026-11-06T00:00:00+00:00', '2026-11-06T00:00:00+00:00');
        """
    )
    con.commit()
    con.close()
    server_v1.init_db(path)
    con = sqlite3.connect(path)
    columns = [r[1] for r in con.execute("PRAGMA table_info(scans)")]
    assert "bag_units" in columns and "bag_mix" in columns
    assert con.execute("SELECT bag_units, bag_mix FROM scans").fetchone() == (None, None), "row kept"
    con.close()


def test_the_scripts_only_reach_for_elements_the_pages_render(client):
    """The picker is driven by JS this suite never clicks (the click is exercised
    by hand against the live page). This pins the other half of the contract:
    every id and class the scripts grab is actually rendered, so renaming an
    element fails here instead of silently doing nothing on a phone.
    """

    def reach(script: str) -> tuple[list[str], list[str]]:
        js = (server_v1.STATIC_DIR / script).read_text(encoding="utf-8")
        ids = sorted(set(re.findall(r'getElementById\("([^"]+)"\)', js)))
        classes = sorted(set(re.findall(r'querySelector(?:All)?\("\.([a-zA-Z0-9_-]+)', js)))
        return ids, classes

    for script, page in (("app.js", client.get(f"/bggo/{KNOWN}").text),
                         ("board.js", client.get("/bggo/live").text)):
        ids, classes = reach(script)
        assert ids and classes, f"{script} looks unparsed — check the regexes"
        for name in ids:
            assert f'id="{name}"' in page, f"{script} reaches for #{name}, not rendered"
        for name in classes:
            assert re.search(rf'class="[^"]*\b{name}\b', page), f"{script} reaches for .{name}"


# ------------------------------------------- stage 2: the seat-map view
#
# The cabin is projected from the same state the list uses — no schema change.
# These pin the pure projection first (server_v1.seat_map), then the rendered
# grid and the ?view= switch.

def test_server_seat_letters_match_the_seeder():
    """Two copies of "ABCDEF" cannot drift: the seeder owns the cabin's lettering."""
    assert server_v1.SEAT_LETTERS == seed.SEAT_LETTERS
    assert server_v1.SEATS_PER_SIDE == 3


def test_parse_seat_reads_row_and_letter():
    assert server_v1.parse_seat("12A") == (12, "A")
    assert server_v1.parse_seat(" 2f ") == (2, "F")


def test_parse_seat_rejects_junk():
    for bad in (None, "", "A", "12", "12G", "12AB", "0A", "seat"):
        assert server_v1.parse_seat(bad) is None, bad


def test_most_recent_code_is_none_with_no_scans(client):
    assert server_v1.most_recent_code(client.get("/bggo/api/state").json()["passengers"]) is None


def test_seat_map_draws_one_row_per_occupied_row(client):
    state = client.get("/bggo/api/state").json()
    seats = server_v1.seat_map(state["passengers"], None)
    assert seats["rows"] == 4                      # default deal: rows 1-4
    assert seats["letters"] == list("ABCDEF")
    assert seats["side"] == 3
    assert len(seats["cells"]) == 6 * 4


def test_seat_map_is_row_major_from_the_front(client):
    state = client.get("/bggo/api/state").json()
    labels = [c["seat"] for c in server_v1.seat_map(state["passengers"], None)["cells"]]
    assert labels[:7] == ["1A", "1B", "1C", "1D", "1E", "1F", "2A"]
    assert labels[-1] == "4F"


def test_seat_map_lights_the_scanned_ticket_only(client):
    client.post("/bggo/api/scan", json={"code": KNOWN})     # Au Bai Qiao, 1A
    state = client.get("/bggo/api/state").json()
    seats = server_v1.seat_map(state["passengers"], KNOWN)
    by_seat = {c["seat"]: c for c in seats["cells"]}
    assert by_seat["1A"]["state"] == "recent" and by_seat["1A"]["recent"]
    assert by_seat["1A"]["code"] == KNOWN
    assert by_seat["1B"]["state"] == "waiting" and not by_seat["1B"]["scanned"]
    assert [c["seat"] for c in seats["cells"] if c["state"] != "waiting"] == ["1A"]


def test_seat_map_marks_scanned_but_not_the_newest(client):
    client.post("/bggo/api/scan", json={"code": KNOWN})
    client.post("/bggo/api/scan", json={"code": KNOWN2})
    state = client.get("/bggo/api/state").json()
    seats = server_v1.seat_map(state["passengers"], server_v1.most_recent_code(state["passengers"]))
    by_seat = {c["seat"]: c for c in seats["cells"]}
    assert by_seat["1A"]["state"] == "scanned" and by_seat["1A"]["scanned"]
    assert not by_seat["1A"]["recent"]
    assert by_seat["1B"]["state"] == "recent"


def test_a_seat_with_no_passenger_is_never_lit(client):
    state = client.get("/bggo/api/state").json()
    seats = server_v1.seat_map(state["passengers"], None)
    empties = [c for c in seats["cells"] if c["code"] is None]
    assert empties, "the default deal must leave some rows unfilled"
    assert all(c["state"] == "waiting" for c in empties)


def test_an_unknown_scan_lights_no_seat(client, codes):
    client.post("/bggo/api/scan", json={"code": codes[20]})
    state = client.get("/bggo/api/state").json()
    seats = server_v1.seat_map(state["passengers"], server_v1.most_recent_code(state["passengers"]))
    assert state["counters"]["unknown"] == 1
    assert all(c["state"] == "waiting" for c in seats["cells"])


# ------------------------------------------------------ the rendered cabin

def test_api_state_carries_the_seat_map(client):
    state = client.get("/bggo/api/state").json()
    assert state["seat_rows"] == 4
    assert state["seats"]["rows"] == 4
    assert state["seats"]["side"] == 3
    assert len(state["seats"]["cells"]) == 24


def test_board_renders_both_views_and_defaults_to_the_list(client):
    html = client.get("/bggo/live").text
    assert 'class="board-page view-list"' in html
    assert 'class="board"' in html and 'class="seatmap"' in html
    assert re.search(r'<a[^>]*data-view="seats"', html)


def test_view_param_selects_the_cabin(client):
    assert 'class="board-page view-seats"' in client.get("/bggo/live?view=seats").text


def test_view_and_big_compose(client):
    html = client.get("/bggo/live?view=seats&big=1").text
    assert 'class="board-page view-seats big"' in html


def test_an_unknown_view_falls_back_to_the_list(client):
    assert 'class="board-page view-list"' in client.get("/bggo/live?view=banana").text


def test_switch_links_are_real_and_preserve_theater_mode(client):
    html = client.get("/bggo/live?view=list&big=1").text
    assert 'href="?view=seats&big=1"' in html
    assert 'href="?view=list&big=1"' in html
    assert 'aria-current="page"' in html


def test_cabin_bands_carry_a_legend_six_squares_and_the_row_number(client):
    html = client.get("/bggo/live?view=seats").text
    assert len(re.findall(r'data-seat="', html)) == 24
    for n in range(1, 5):
        assert f'class="cabin-rno">{n}</span>' in html
    # the axis label runs once along the cabin (the aisle) and once at each end
    for letter in "ABCDEF":
        assert html.count(f'class="cabin-letter">{letter}</span>') == 2
    assert 'class="cabin-aisle"' in html
    # one seat block per drawn band: every cabin row, the two letter strips, and
    # the exit band in front of row 1 and behind the last row
    assert html.count('class="cabin-seats"') == 4 + 2 + 2
    assert html.count('class="cabin-row"') == 4
    assert html.count('class="cabin-row cabin-gap"') == 2


def test_no_seat_prints_its_own_code(client):
    """The squares are marks, not labels.

    Position plus the A-F letters along the top and the row numbers down the aisle
    already say which seat a square is; a code printed inside it was noise on the
    projector and unreadable on the card, and the thing a filled square has to say
    is *taken*. The code stays on the element (`data-seat`/`title`) for the row
    that points at it, and the ticket's map draws the same squares, so this pins
    both views at once.
    """
    client.post("/bggo/api/scan", json={"code": KNOWN})     # Au Bai Qiao, 1A
    for path in ("/bggo/live?view=seats", f"/bggo/{KNOWN}"):
        html = client.get(path).text
        inner = re.findall(r'<span class="seat[^"]*"[^>]*>([^<]*)</span>', html)
        assert inner and not any(inner), f"a square still prints its seat id on {path}"
        assert 'data-seat="1A"' in html and 'title="1A"' in html


def test_cabin_does_not_borrow_the_table_data_index(client):
    html = client.get("/bggo/live?view=seats").text
    assert [m for m in re.findall(r'data-index="(\d+)"', html)] == [str(i) for i in range(1, 21)]


def test_the_cabin_is_drawn_as_a_fuselage(client):
    """Doors and labels only — the cabin chrome is decoration, so it is
    aria-hidden, and nothing draws an outline or a silhouette the instruction
    took off the page."""
    html = client.get("/bggo/live?view=seats").text
    # two gutters per band, and only the two exit bands carry a marker: every
    # seat row's gutters are empty spacers
    assert html.count('class="cabin-door"') == 12
    assert html.count('class="cabin-door is-door left"') == 2
    assert html.count('class="cabin-door is-door right"') == 2
    assert 'class="exit">EXIT</span>' in html
    assert 'class="chev"' in html
    for gone in ('cabin-nose', 'cabin-tail', 'cabin-wing'):
        assert gone not in html, f"{gone} was removed from the drawing"


def test_the_cabin_is_a_sequence_of_row_slots():
    """A seat row is a slot and an exit band is a slot too, because a band is one
    row pitch tall — so an exit is part of the cabin's length instead of extra
    space a compartment has to run over."""
    assert server_v1.cabin_slots(4) == [("row", n) for n in range(1, 5)]
    slots = server_v1.cabin_slots(20)
    assert slots[slots.index(("row", 17)) + 1] == ("band", 18)
    assert slots[slots.index(("band", 18)) + 1] == ("row", 18)
    assert ("band", 20) in slots and ("band", 1) not in slots
    # the band behind the last row is outside the binning
    assert ("band", 21) not in slots


def test_zones_run_the_outer_sides_four_rows_each():
    """One rectangle down each wall per four seat rows, running the whole cabin.
    The slots are counted in seat rows, so the exit bands are inside a zone
    (extra height) rather than extra zones."""
    assert server_v1.cabin_bins(1) == [(1, 0, "1", 1)]
    assert server_v1.cabin_bins(2) == [(1, 0, "1+2", 2)]
    assert server_v1.cabin_bins(4) == [(1, 0, "1+2+3+4", 4)]

    bins = server_v1.cabin_bins(40)
    assert len(bins) == 10, "40 rows / 4 = 10 zones a wall"
    assert bins[:4] == [
        (1, 0, "1+2+3+4", 4), (5, 0, "5+6+7+8", 4),
        (9, 0, "9+10+11+12", 4), (13, 0, "13+14+15+16", 4),
    ]
    assert (17, 0, "17+b18+18+19+b20+20", 6) in bins   # both bands inside
    assert bins[-1] == (37, 0, "37+38+39+40", 4)

    html = server_v1.cabin_html(full_cabin(4), scanned=0, allocated=24)
    assert html.count('data-slot="1+2+3+4"') == 2, "a zone on each wall"


def test_the_exit_zone_gains_a_slot_for_each_band_inside_it():
    """The 17-20 zone is six slots tall — four rows plus the two bands — and the
    rectangle's `--bin-slots` says so, so no seam shows where the old
    compartments met."""
    html = server_v1.cabin_html(full_cabin(40), scanned=0, allocated=240)
    assert 'data-slot="17+b18+18+19+b20+20"' in html
    assert 'style="--bin-slots:6"' in html
    assert 'style="--bin-slots:4"' in html
    assert "reaches-up" not in html and "over-band" not in html
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert re.search(r"--exit-gap:\s*1\s*;", css), "a band is one row pitch"
    assert re.search(
        r"height:\s*calc\(var\(--bin-slots,\s*2\)\s*\*\s*var\(--unit\)", css
    ), "the rectangle's height is its slot count"


def test_a_compartment_and_an_exit_marker_are_in_different_lanes():
    """The bin is drawn in its own lane between the door gutter and the seats, so
    it can be exactly level with an EXIT marker without the two overlapping."""
    html = server_v1.cabin_html(full_cabin(40), scanned=0, allocated=240)
    assert re.search(r'class="cabin-lane" aria-hidden="true"><span class="cabin-bin', html)
    assert not re.search(r'class="cabin-door[^"]*"[^>]*><span class="cabin-bin', html)
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    block = re.search(r"\.cabin-lane\s*\{([^}]*)\}", css)
    assert block and "var(--bin-w)" in block.group(1)


def test_a_compartment_costs_the_height_budget_nothing():
    """The bins hang over the rows they belong to instead of adding a band, so
    the exit count (and therefore --unit) is untouched by them."""
    html = server_v1.cabin_html(full_cabin(40), scanned=0, allocated=240)
    assert '--exit-count:4' in html
    assert html.count('class="cabin-bin') == 2 * len(server_v1.cabin_bins(40))
    # pinned in the stylesheet, which is where the overlay would stop being one
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    block = re.search(r"\.cabin-bin\s*\{([^}]*)\}", css)
    assert block, "the .cabin-bin rule moved — this test is now blind"
    assert "position: absolute" in block.group(1)


def test_a_short_cabin_still_gets_both_doors(client):
    """The 4-row test deal is clamped, not stretched: a front and a rear door,
    and no door row past the end of the fuselage."""
    assert server_v1.cabin_doors(4) == [1, 4]
    assert server_v1.cabin_doors(20) == [1, 18, 19, 20]
    assert server_v1.cabin_doors(40) == [1, 18, 19, 40]
    assert server_v1.cabin_doors(0) == [1]


def full_cabin(rows: int = 40) -> dict:
    """A cabin of `rows` empty rows — the live deal is 40, the test deal is 4."""
    cells = [
        {"seat": f"{row}{letter}", "row": row, "letter": letter, "code": None,
         "scanned": False, "recent": False, "state": "waiting", "aisle_after": letter == "C"}
        for row in range(1, rows + 1)
        for letter in server_v1.SEAT_LETTERS
    ]
    return {"rows": rows, "letters": list(server_v1.SEAT_LETTERS), "side": 3, "cells": cells}


def test_a_full_cabin_marks_every_door_on_both_walls():
    """Front door, overwing pair, rear door — each one on both walls."""
    html = server_v1.cabin_html(full_cabin(40), scanned=0, allocated=240)
    for row in (1, 18, 19, 40):
        assert html.count(f'data-door="{row}"') == 2, f"row {row} is a door on both walls"
    assert 'data-door="17"' not in html
    assert 'data-door="20"' not in html
    assert html.count('data-row="') == 40


def test_nothing_draws_a_border_around_the_seats():
    """The seats are the picture: no outline, no walls, no bulkheads. Pinned in
    the stylesheet because that is where the border would come back from."""
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    block = re.search(r"\.cabin-seats\s*\{([^}]*)\}", css)
    assert block, "the .cabin-seats rule moved — this test is now blind"
    assert "border" not in block.group(1)


def test_the_card_draws_the_fuselage_around_the_map():
    """Davis, 2026-11-14: the passenger's map gets one line around the whole
    drawing — nose rounder than tail — while the board's map keeps the bare
    drawing and the seats themselves stay unboxed."""
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    block = re.search(r"\.cabin-card \.seatmap\s*\{([^}]*)\}", css)
    assert block, "the .cabin-card .seatmap rule moved — this test is now blind"
    assert "border: 1px solid var(--rule)" in block.group(1)
    assert "border-radius" in block.group(1)


def test_the_advised_zone_marches_and_rests_when_asked():
    """The suggested zone's dashes travel around it, drawn as four gradient
    edges because an outline's dashes cannot march — and the motion is gated
    behind prefers-reduced-motion, where the same dashes stand still."""
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    ring = re.search(r"\.cabin-card \.cabin-bin\.is-advised::before\s*\{([^}]*)\}", css)
    assert ring, "the advised ring's rule moved — this test is now blind"
    assert "repeating-linear-gradient" in ring.group(1), "the dashes are drawn edges, not an outline"
    assert "@keyframes cabin-ants" in css
    gated = re.search(
        r"@media \(prefers-reduced-motion: no-preference\) \{\s*"
        r"\.cabin-card \.cabin-bin\.is-advised::before \{([^}]*)\}", css)
    assert gated and "animation: cabin-ants" in gated.group(1), (
        "the march must be gated; the resting dashes are the default")


def test_every_exit_marker_sits_in_its_own_band_not_on_a_row():
    """Nothing marks a seat row: the front door is marked in front of row 1, the
    overwing pair in the gaps either side of it, the rear door behind the last
    row. A band is (position, door row), and position rows+1 is behind the last
    row. The height budget is charged one band per entry."""
    assert server_v1.cabin_markers(40) == [(1, 1), (18, 18), (20, 19), (41, 40)]
    assert server_v1.cabin_markers(18) == [(1, 1), (19, 18)]   # no overwing left
    assert server_v1.cabin_markers(4) == [(1, 1), (5, 4)]      # too short for any
    assert server_v1.cabin_markers(1) == [(1, 1), (2, 1)]      # an exit each side

    html = server_v1.cabin_html(full_cabin(40), scanned=0, allocated=240)
    assert '--exit-count:4' in html
    # the bands are in cabin order: front, above the pair, below it, behind the rear
    assert re.search(
        r'cabin-gap" data-exit="1">.*?data-row="1">.*?'
        r'cabin-gap" data-exit="18">.*?data-row="18">.*?data-row="19">.*?'
        r'cabin-gap" data-exit="20">.*?data-row="20">.*?'
        r'data-row="40">.*?cabin-gap" data-exit="41">.*?cabin-foot".*?',
        html,
        re.S,
    )
    # every marker is in a band, so no seat row carries one
    rows_html = re.findall(r'<div class="cabin-row" data-row="\d+">.*?</div>', html, re.S)
    assert len(rows_html) == 40
    assert not [row for row in rows_html if 'is-door' in row or 'EXIT' in row]
    assert html.count('data-door="1"') == 2
    assert html.count('data-door="40"') == 2


def test_hint_outlives_the_view_switch(client):
    for view in ("list", "seats"):
        assert 'class="hint"' in client.get(f"/bggo/live?view={view}").text


# ------------------------------------------------ the overhead-bin prediction
#
# The base rule is one line of geometry — every declared bag in the compartment
# above its owner's seat, on its owner's wall — and the ramp is decided on the
# server so board.js only applies it (R9). The pure projection is pinned first,
# then the rendered colours and the legend.

def bin_pax(seat: str, units: float | None = None) -> dict:
    """A passenger for the bin projection: a seat, and perhaps a declared bag."""
    bag = None if units is None else {"units": units, "text": server_v1.unit_text(units)}
    return {"seat_number": seat, "bag": bag, "scanned": True, "code": "BGGO-BIN-0000"}


def bin_map(passengers: list[dict], rows: int) -> dict:
    return {
        (load["bin"], load["side"]): load
        for load in server_v1.bin_loads(passengers, rows)
    }


def test_a_zone_holds_two_compartments_worth():
    """The map merges two 4.5-unit compartments into one zone (2026-11-10), so a
    zone holds 9 units and the whole ramp doubles with it."""
    assert server_v1.COMPARTMENT_CAPACITY == 4.5
    assert server_v1.BIN_CAPACITY == 9.0


def test_the_ramp_turns_red_exactly_at_the_advice_line():
    """Red and the redirect are one line (decision 2026-11-10): a zone the board
    paints red is exactly one the card sends the passenger past."""
    assert server_v1.STOW_AWAY_UNITS == 8.0
    assert server_v1.bin_level(0) == ("green", False)
    assert server_v1.bin_level(2) == ("green", False)
    assert server_v1.bin_level(2.25) == ("yellow", False)
    assert server_v1.bin_level(7) == ("yellow", False)
    # below the line is yellow, never red — the card still sends you to it
    assert server_v1.bin_level(7.75) == ("yellow", False)
    assert server_v1.bin_level(8) == ("red", False)
    assert server_v1.bin_level(9) == ("red", False)
    # red says "full"; is-over says "does not fit" — the fact the ramp cannot show
    assert server_v1.bin_level(10) == ("red", True)


def test_the_prediction_puts_a_bag_above_its_own_seat():
    """1A's bag goes in the left 1+2 compartment and nowhere else; 1D's in the
    right one. The aisle splits the cabin, so it splits the prediction."""
    loads = bin_map([bin_pax("1A", 0.25)], 4)
    assert loads[(1, "left")]["units"] == 0.25
    assert loads[(1, "right")]["units"] == 0
    loads = bin_map([bin_pax("1D", 1.0)], 4)
    assert loads[(1, "left")]["units"] == 0
    assert loads[(1, "right")]["units"] == 1.0


def test_a_zone_covers_four_seat_rows_and_swallows_the_exit_bands():
    """Two of the old two-slot compartments merge into one four-row zone, and the
    exit bands are anatomy *inside* a zone rather than zones of their own."""
    rows = dict(server_v1.cabin_bin_rows(40))
    assert rows[1] == (1, 2, 3, 4)
    assert rows[17] == (17, 18, 19, 20)          # both overwing bands fall inside
    assert server_v1.bin_seats(40, 1, "left") == [
        "1A", "1B", "1C", "2A", "2B", "2C", "3A", "3B", "3C", "4A", "4B", "4C",
    ]
    assert server_v1.bin_seats(40, 17, "right") == [
        "17D", "17E", "17F", "18D", "18E", "18F",
        "19D", "19E", "19F", "20D", "20E", "20F",
    ]


def test_the_exit_zone_is_six_slots_tall_because_two_bands_are_inside_it():
    """The map draws one rectangle a zone; the 17-20 zone spans six row-pitches
    (four rows plus the two bands), every other zone four. That is what
    `--bin-slots` carries, so there is no seam where the old compartments met."""
    drawn = {anchor: (label, slots) for anchor, _, label, slots in server_v1.cabin_bins(40)}
    assert drawn[1][1] == 4 and drawn[5][1] == 4
    assert drawn[17][1] == 6
    assert "+b18+" in drawn[17][0] and "+b20+" in drawn[17][0]


def test_a_full_zone_sends_the_next_bag_across_the_aisle():
    """Prediction is stowage (R20): the zone fills to 8 of its 9 units, and the
    ninth trolley is *in* the zone the card named — the one opposite."""
    seats = ["1A", "1B", "1C", "2A", "2B", "2C", "3A", "3B", "3C"]
    passengers = [bin_pax(seat, 1.0) for seat in seats]
    bins, decisions = server_v1.stow_plan(passengers, 40)
    left = bin_map(passengers, 40)[(1, "left")]
    assert left["units"] == 8.0                      # eight trolleys stay
    assert left["level"] == "red" and left["over"] is False
    # first come, first served: the input order is the arrival order, so the ninth
    # bag (3C) is the one that walks across the aisle
    assert decisions["1A"]["reason"] == "own"
    assert decisions["3B"]["reason"] == "own"
    facing = payload(bins, 40, decisions, "3C")
    assert decisions["3C"]["reason"] == "opposite"
    assert facing["bin"] == "Row 1-4R"
    assert facing["note"] == (
        "The zone above your seat is full, please use the bin across the aisle."
    )
    assert facing["label"] == "Where to put your bag"
    # and the bag really is in that zone: one trolley, one bag, green
    right = bin_map(passengers, 40)[(1, "right")]
    assert right["units"] == 1.0 and right["bags"] == 1
    assert len(left["seats"]) == 12 and "8 of 9 units" in left["title"]


def test_only_a_declared_bag_loads_a_zone():
    """A passenger who has not chosen a bag has nothing to stow: no units, no bag
    counted, and the zone sits empty and green."""
    load = bin_map([bin_pax("1A", None)], 4)[(1, "left")]
    assert load["units"] == 0 and load["level"] == "green"
    assert load["bags"] == 0
    assert "Zone above 1A–4C · 0 of 9 units · 0 items" in load["title"]
    assert load["seats"][0] == "1A" and load["seats"][-1] == "4C"


def test_a_short_tail_still_gets_its_own_zone():
    """Zones are fixed four-row bands, so a cabin whose row count is not a
    multiple of four ends on a shorter zone rather than a bare row."""
    assert server_v1.cabin_bin_rows(3) == [(1, (1, 2, 3))]
    loads = bin_map([bin_pax("3A", 1.0)], 3)
    assert loads[(1, "left")]["units"] == 1.0


def test_a_junk_seat_does_not_load_a_bin():
    loads = server_v1.bin_loads([{"seat_number": "??", "bag": {"units": 1.0}}], 4)
    assert all(load["units"] == 0 for load in loads)


def test_the_two_walls_are_filled_separately():
    """One zone per wall, two independent numbers: filling the left wall to the
    red line leaves the right wall green."""
    left = [bin_pax(f"{row}{letter}", 1.0)
            for row in (1, 2, 3, 4) for letter in "ABC"][:8]
    passengers = left + [bin_pax("1D", 2.0)]
    bins = server_v1.bin_loads(passengers, 4)
    html = server_v1.cabin_html(full_cabin(4), scanned=len(passengers), allocated=24, bins=bins)

    def classes(side: str) -> str:
        match = re.search(
            rf'class="cabin-bin([^"]*)" data-bin="\d+" data-slot="[^"]*" data-side="{side}"',
            html,
        )
        assert match, f"no zone on the {side} wall in the cabin"
        return match.group(1)

    assert "load-red" in classes("left") and "is-over" not in classes("left")   # 8.0
    assert "load-green" in classes("right")                                     # 2.0


def shed(rows: int, loads: dict | None = None) -> list[dict]:
    """Compartments with the given `{(anchor, side): units}` loads, finished — the
    way to pin the advice against a picture the plan would not produce on its own."""
    bins = server_v1.empty_bins(rows)
    for load in bins:
        load["units"] = (loads or {}).get((load["bin"], load["side"]), 0.0)
    return server_v1.finish_bins(bins)


def payload(bins: list[dict], rows: int, decisions: dict, seat: str) -> dict:
    """The card's payload for one seat, from a plan's decisions: the same call
    `stow_for()` makes, so a test reads the words the card would print rather than
    the raw decision (R22)."""
    return server_v1.stow_payload(
        rows, seat, **decisions[seat], loads=server_v1.bin_index(bins)
    )


def test_when_nothing_nearby_can_take_the_bag_the_emptiest_is_chosen():
    """The one path that goes over capacity: every candidate is too full even for
    a handbag, so the emptiest is used anyway — and that is what `is-over` means,
    not "the bay was busy"."""
    loads = server_v1.bin_index(shed(4, {
        (1, "left"): 8.25, (1, "right"): 8.5,
    }))
    reason, key, state = server_v1.stow_target(loads, server_v1.stow_order(4, 1, "left"), 1.0)
    assert state == "packed" and key == (1, "left")     # emptiest of the candidates
    loads[key]["units"] += 1.0
    bins = server_v1.finish_bins(list(loads.values()))
    html = server_v1.cabin_html(full_cabin(4), scanned=1, allocated=24, bins=bins)
    assert re.search(
        r'class="cabin-bin[^"]*load-red[^"]*is-over[^"]*" data-bin="\d+" data-slot="[^"]*" data-side="left"',
        html,
    )
    assert re.search(
        r'data-side="left" data-load="9.25" data-level="red"', html
    ), "the load rides on the element so the poll never re-derives it"


def test_a_zone_carries_its_load_as_a_tooltip():
    """The lane is about eight pixels wide at 40 rows, so the number cannot be in
    the rectangle — it is in the title."""
    bins = server_v1.bin_loads([bin_pax("1A", 0.25)], 4)
    html = server_v1.cabin_html(full_cabin(4), scanned=1, allocated=24, bins=bins)
    assert 'title="Zone above 1A–4C · 0.25 of 9 units · 1 item"' in html
    assert 'data-bags="1"' in html and 'data-bags="0"' in html
    # green at zero: a zone nobody is stowing in has room
    assert re.search(r'data-side="right" data-load="0" data-level="green"', html)


def test_there_is_no_legend_under_the_cabin_any_more():
    """Colour alone is not a signal, so the words moved into the places that can
    carry them: each rectangle has its own load in its `title`, and the seatmap's
    accessible name carries the counts. The legend block is gone (2026-11-10), so
    nothing under the cabin can disagree with the rectangles."""
    bins = server_v1.bin_loads([bin_pax(f"3{letter}", 1.0) for letter in "ABC"], 4)
    html = server_v1.cabin_html(full_cabin(4), scanned=3, allocated=24, bins=bins)
    assert "bin-legend" not in html and "Predicted bin load" not in html
    assert "overhead zones 1 with room, 1 filling, 0 full" in html  # the a11y name
    assert 'title="Zone above 1A–4C · 3 of 9 units · 3 items"' in html


def test_the_cabin_label_rides_in_the_state_so_a_reset_cannot_leave_it_stale():
    """The bins are painted by board.js and the seatmap's accessible name is now
    the only place the counts are read out, so the words travel with them: the
    server writes them, the page mirrors them, a reset restores them."""
    pax = [bin_pax("3A", 1.0), bin_pax("3B", 1.0), bin_pax("3C", 1.0)]
    bins = server_v1.bin_loads(pax, 4)
    assert server_v1.cabin_meta(bins, scanned=3, allocated=24) == {
        "label": "Cabin seat map: 3 of 24 seats scanned; overhead zones 1 with room, "
                 "1 filling, 0 full"
    }
    heavy = shed(4, {(1, "left"): 10.0})
    assert "1 over capacity" in server_v1.cabin_meta(heavy, scanned=2, allocated=24)["label"]


def test_the_state_carries_that_label_and_it_follows_a_reset(client, board_token):
    first = client.get("/bggo/api/state").json()
    label = first["cabin"]["label"]
    assert "0 over capacity" not in label
    assert label.endswith("with room, 0 filling, 0 full"), label

    client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-duffel": 3}})
    filled = client.get("/bggo/api/state").json()["cabin"]["label"]
    assert filled.endswith("with room, 1 filling, 0 full"), filled

    client.post("/bggo/api/reset", headers={"X-Board-Token": board_token})
    assert client.get("/bggo/api/state").json()["cabin"]["label"] == label


def test_no_seat_square_prints_its_id_even_after_a_poll():
    """A poll repaints the square's *fill* only. Writing `1A` into it drew type
    over an occupied seat on the live board — illegible at card size, and the
    letters along the top plus the aisle numbers already name every position."""
    js = (server_v1.STATIC_DIR / "board.js").read_text(encoding="utf-8")
    assert not re.search(r"seat\.el\.textContent\s*=\s*cell\.", js), \
        "board.js still writes the seat id into a square"
    assert "seat.el.textContent = \"\"" in js, "a repaint still clears any legacy label"
    html = server_v1.cabin_html(full_cabin(4), scanned=3, allocated=24, bins=[])
    for seat in ("1A", "2C", "4F"):
        cell = re.search(rf'<span class="seat[^"]*" data-seat="{seat}"[^>]*></span>', html)
        assert cell, f"{seat} prints a label inside its square"


def test_board_js_mirrors_the_label_and_never_reaches_for_a_legend(client):
    """Mirroring, not re-deriving: the script writes the server's words into the
    seatmap's accessible name, and it must not reach for a legend that no longer
    exists. The element-reach contract would fail on any of these, because the page
    does not render them any more."""
    js = (server_v1.STATIC_DIR / "board.js").read_text(encoding="utf-8")
    assert "cabin.label" in js and ".seatmap" in js
    for gone in ("bin-legend", "bin-key", "bin-count", "bin-over", "cabin.levels"):
        assert gone not in js, f"board.js still reaches for the removed legend: {gone}"
    page = client.get("/bggo/live?view=seats").text
    assert 'class="seatmap"' in page
    assert "bin-legend" not in page and "Predicted bin load" not in page


def test_the_bin_level_is_applied_not_re_derived():
    """The thresholds live in bin_level() on the server (R9): board.js applies
    the level from data.bins exactly as it applies a seat's state."""
    js = (server_v1.STATIC_DIR / "board.js").read_text(encoding="utf-8")
    assert "data.bins" in js
    assert "binCells" in js
    for literal in ("4.5", "BIN_CAPACITY", "STOW_AWAY_UNITS", "4.0"):
        assert literal not in js, f"the script re-derives a threshold: {literal!r}"


def test_the_bin_ramp_uses_brand_tokens_not_a_new_colour():
    """No literal hex and no new hue: the ramp is the three semantic tokens the
    kit already ships, and the swatch uses the same tint as the rectangle."""
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert "--warn: var(--hke-warn)" in css
    for level, token in (("green", "--ok"), ("yellow", "--warn"), ("red", "--error")):
        assert re.search(
            rf"\.cabin-bin\.load-{level}\s*\{{\s*--bin-ink:\s*var\({token}\)", css
        ), f"the {level} level does not use var({token})"
    assert "body.view-list .bin-legend" not in css
    assert "bin-legend" not in css and "bin-swatch" not in css, "the legend came back"


def test_the_board_renders_the_bins_and_no_legend(client):
    html = client.get("/bggo/live?view=seats").text
    assert 'data-bin="1" data-slot="1+2+3+4" data-side="left"' in html
    assert "load-green" in html       # zero units is green, the bottom of the ramp
    # the legend is gone: the words live in each rectangle's title and in the
    # seatmap's accessible name, and nothing repeats them under the cabin
    assert "bin-legend" not in html and "Predicted bin load" not in html


def test_every_bin_the_payload_sends_is_a_rectangle_the_poll_can_find(client):
    """The join defect 1 was: the page keyed a compartment by its slot label
    ("1+2") while the payload keyed it by the anchor row (1). board.js built
    "1|left", found nothing, and never repainted a single bin — the colours froze
    at page load while the legend's numbers ticked. Two functions write those two
    keys, so this is the test that keeps them equal."""
    html = client.get("/bggo/live?view=seats").text
    bins = client.get("/bggo/api/state").json()["bins"]
    in_dom = set(re.findall(r'data-bin="([^"]+)" data-slot=', html))
    assert in_dom == {str(b["bin"]) for b in bins}
    assert in_dom, "the cabin rendered no compartments at all"


def test_api_state_carries_the_bin_loads(client):
    bins = client.get("/bggo/api/state").json()["bins"]
    assert {(b["bin"], b["side"]) for b in bins} == {(1, "left"), (1, "right")}
    assert all(b["units"] == 0 and b["level"] == "green" for b in bins)
    assert bins[0]["capacity"] == 9.0
    assert bins[0]["seats"][:3] == ["1A", "1B", "1C"] and bins[0]["seats"][-1] == "4C"


def test_a_scan_fills_the_bin_above_the_seat(client):
    """End to end: the ticket is scanned with a trolley, and the compartment above
    1A turns from green to yellow the moment its passenger declares a bag."""
    before = client.get("/bggo/api/state").json()["bins"]
    assert next(b for b in before if (b["bin"], b["side"]) == (1, "left"))["units"] == 0
    r = client.post("/bggo/api/scan", json={"code": KNOWN, "bag_units": 1})
    assert r.status_code == 200
    state = client.get("/bggo/api/state").json()
    load = next(b for b in state["bins"] if (b["bin"], b["side"]) == (1, "left"))
    assert load["units"] == 1.0 and load["level"] == "green"      # 1 unit is still green
    assert load["bags"] == 1
    html = client.get("/bggo/live?view=seats").text
    assert re.search(
        r'class="cabin-bin[^"]*load-green[^"]*" data-bin="\d+" data-slot="[^"]*" data-side="left" data-load="1"',
        html,
    )
    assert "Zone above 1A–4C · 1 of 9 units · 1 item" in html


# --------------------------------------------------- the stowage suggestion
#
# The card's advice is the base prediction turned outward: the compartment above
# your seat while it has room, then **opposite, in front, opposite and in front,
# behind last**. Pure projection first, then the card and the live re-paint.

def seat_code(db, seat: str) -> str:
    return rows(db, "SELECT code FROM passengers WHERE seat_number = ?", (seat,))[0][0]


def advice_for(seats: list[str], rows_: int, seat: str) -> dict:
    return server_v1.stow_advice(
        server_v1.bin_loads([bin_pax(s, 1.0) for s in seats], rows_), rows_, seat
    )


def test_a_zone_is_named_by_its_row_span_and_wall():
    assert server_v1.bin_name(40, 1, "left") == "Row 1-4L"
    assert server_v1.bin_name(40, 5, "right") == "Row 5-8R"
    assert server_v1.bin_name(40, 37, "left") == "Row 37-40L"
    # the exit zone's span is its four seat rows, not the six slots it draws over
    assert server_v1.bin_name(40, 17, "left") == "Row 17-20L"


def test_the_card_names_the_compartment_above_your_own_seat_while_it_has_room():
    advice = server_v1.stow_advice(server_v1.bin_loads([bin_pax("1A", 0.25)], 4), 4, "1A")
    assert advice["state"] == "own" and advice["reason"] == "own"
    assert advice["bin"] == "Row 1-4L"
    assert advice["level"] == "green" and advice["level_text"] == "mostly empty"
    assert advice["own_units"] == 0.25
    assert advice["note"] == "Stow your bag in the zone above your seat."
    assert advice["label"] == "Where to put your bag"
    # 2F is on the right wall, so the name and the zone follow the letter
    assert server_v1.stow_advice(
        server_v1.bin_loads([bin_pax("2F", 0.5)], 4), 4, "2F"
    )["bin"] == "Row 1-4R"


def test_a_nearly_full_zone_sends_you_opposite_first():
    """The base prediction stops being advice at STOW_AWAY_UNITS (8 of 9): the
    card says why, names the wall across the aisle, and keeps the old name so the
    sentence can point at it."""
    own = ["1A", "1B", "1C", "2A", "2B", "2C", "3A", "3B"]    # own zone = 8.0
    advice = advice_for(own, 4, "1A")
    assert advice["state"] == "redirect" and advice["reason"] == "opposite"
    assert advice["bin"] == "Row 1-4R"
    assert advice["own_bin"] == "Row 1-4L" and advice["own_units"] == 8.0
    assert advice["note"] == (
        "The zone above your seat is nearly full, please use the bin across the aisle."
    )
    assert advice["level_text"] == "mostly empty", "the state is a word, never the arithmetic"


def test_the_advice_order_is_opposite_then_in_front_then_both_then_behind():
    """Fill the candidates one at a time from 5A and watch the advice walk the
    cabin in the agreed order; `behind` is the last resort, not the first."""
    full = 8.0
    assert server_v1.stow_advice(
        shed(40, {(5, "left"): full}), 40, "5A"
    )["reason"] == "opposite"
    both = {(5, "left"): full, (5, "right"): full}
    assert server_v1.stow_advice(shed(40, both), 40, "5A")["bin"] == "Row 1-4L"
    ahead = {**both, (1, "left"): full}
    assert server_v1.stow_advice(shed(40, ahead), 40, "5A")["bin"] == "Row 1-4R"
    allfour = {**ahead, (1, "right"): full}
    advice = server_v1.stow_advice(shed(40, allfour), 40, "5A")
    assert advice["reason"] == "behind" and advice["bin"] == "Row 9-12L"


def test_a_first_row_seat_has_nothing_in_front_of_it():
    """Row 1 has no zone ahead: the order falls through to the wall across the
    aisle and then to the one behind, with no name invented for a zone that is
    not there."""
    assert server_v1.stow_advice(
        shed(40, {(1, "left"): 8.0}), 40, "1A"
    )["reason"] == "opposite"
    advice = server_v1.stow_advice(
        shed(40, {(1, "left"): 8.0, (1, "right"): 8.0}), 40, "1A"
    )
    assert advice["reason"] not in {"ahead", "opposite-ahead"}
    assert advice["state"] == "redirect" and advice["bin"] == "Row 5-8L"


def test_when_every_nearby_zone_is_full_the_emptiest_is_named_as_a_last_resort():
    """`packed` still points somewhere: the least-loaded of the candidates, because
    "everywhere is full" is not advice. The loads are set by hand here — the plan
    would never leave the window in this state."""
    loads = shed(40, {
        (5, "left"): 9.0,        # own
        (5, "right"): 8.5,       # opposite — the emptiest
        (1, "left"): 8.75,       # ahead
        (1, "right"): 10.0,      # opposite and in front
        (9, "left"): 12.0,       # behind
    })
    advice = server_v1.stow_advice(loads, 40, "5A")
    assert advice["state"] == "packed"
    assert advice["bin"] == "Row 5-8R" and advice["units"] == 8.5
    assert advice["note"] == (
        "Every zone near your seat is nearly full. Please use the bin across the aisle."
    )


def test_a_seat_outside_the_cabin_gets_no_zone_named():
    """A seat the cabin does not cover is admitted rather than given a made-up
    zone."""
    loads = server_v1.bin_loads([bin_pax("99A", 1.0)], 3)
    advice = server_v1.stow_advice(loads, 3, "99A")
    assert advice["state"] == "none" and advice["bin"] == "—" and advice["level"] is None
    assert "No zone" in advice["note"]


def test_the_boarding_pass_carries_the_stowage_advice(client):
    html = client.get(f"/bggo/{KNOWN}").text
    assert 'id="stow"' in html and 'id="stow-bin"' in html and 'id="stow-note"' in html
    assert "Where to put your bag" in html
    assert 'data-state="own"' in html and 'data-level="green"' in html
    assert 'class="stow-bin" id="stow-bin">Row 1-4L<' in html
    assert 'id="stow-level">mostly empty<' in html, "the level is a word as well as a colour"


def test_the_advice_on_the_card_follows_the_stowage(client, db):
    """Same route, twice: the zone fills to 8 of 9 and the next passenger's card
    sends them across the aisle — and says their bag *is* there, because the
    compressed demo stows what it predicts (R20)."""
    fill = ("1A", "1B", "1C", "2A", "2B", "2C", "3A", "3B")   # own zone = 8.0
    for seat in fill:
        assert client.post(
            "/bggo/api/scan", json={"code": seat_code(db, seat), "bag_units": 1}
        ).status_code == 200
    html = client.get(f"/bggo/{KNOWN}").text          # 1A stowed in its own zone
    assert 'data-state="own"' in html and 'id="stow-bin">Row 1-4L<' in html
    assert "Stow your bag in the zone above your seat." in html
    assert 'id="stow-label">Where to put your bag<' in html

    assert client.post(                                  # 3C is the ninth trolley
        "/bggo/api/scan", json={"code": seat_code(db, "3C"), "bag_units": 1}
    ).status_code == 200
    html = client.get(f"/bggo/{seat_code(db, '3C')}").text

    assert 'data-state="redirect"' in html
    assert 'id="stow-bin">Row 1-4R<' in html
    assert "The zone above your seat is full, please use the bin across the aisle." in html
    # the passenger never sees the arithmetic, an em dash or a middot fragment
    block = re.search(r'<section class="stow".*?</section>', html, re.S).group(0)
    assert "unit" not in block and "9" not in block
    assert "—" not in block and "·" not in block
    # and the board counts that bag in Row 1-4R, not in Row 1-4L
    state = client.get("/bggo/api/state").json()
    bins = {(b["bin"], b["side"]): b for b in state["bins"]}
    assert bins[(1, "left")]["units"] == 8.0        # eight stayed
    assert bins[(1, "right")]["units"] == 1.0       # the ninth is here


def test_the_scan_response_carries_the_advice_for_the_card_to_repaint(client):
    body = client.post("/bggo/api/scan", json={"code": KNOWN, "bag_units": 1}).json()
    assert body["stow"]["bin"] == "Row 1-4L" and body["stow"]["state"] == "own"
    assert body["stow"]["capacity"] == 9.0
    assert body["stow"]["declared"] is True
    assert body["stow"]["note"] == "Stow your bag in the zone above your seat."
    # an unknown ticket is nobody's seat: no advice, because there is no seat
    assert "stow" not in client.post("/bggo/api/scan", json={"code": BOGUS}).json()


def test_the_card_script_applies_the_advice_instead_of_re_deriving_it():
    js = (server_v1.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "showStow(data.stow)" in js
    for literal in ("4.5", "STOW_AWAY", "nearly full"):
        assert literal not in js, f"the card's copy or thresholds leaked into the script: {literal!r}"


def test_the_stowage_block_wears_the_bin_ramp_and_no_new_colour():
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    for level, token in (("green", "--ok"), ("yellow", "--warn"), ("red", "--error")):
        assert re.search(
            rf'\.stow\[data-level="{level}"\]\s*\{{\s*--bin-ink:\s*var\({token}\)', css
        ), f"the stowage block has no {level} state"
    assert re.search(r"\.stow-bin\s*\{([^}]*)\}", css)
    assert "var(--figure)" in re.search(r"\.stow-bin\s*\{([^}]*)\}", css).group(1)


# ------------------------------------------------ the cabin, on the card
# The ticket names a zone ("please use the bin across the aisle") and, until now, left the
# geometry to the reader's imagination. The card now carries the *board's own*
# drawing of the cabin under that sentence — not a second one — with the advised
# zone ringed and the passenger's own square filled. These tests hold it to the
# state and to the board, never to markup of its own: if the two views could
# disagree about where a zone is, the card would be worse than the sentence.

def card_map(html: str) -> str:
    """The `<figure>` the card draws, as text, so a test can read its marks."""
    match = re.search(r'<figure class="cabin-card".*?</figure>', html, re.S)
    assert match, "the boarding pass carries no cabin map"
    return match.group(0)


def map_zones(html: str) -> list[dict]:
    """Every zone rectangle on the card, as a dict of its attributes."""
    return [
        dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
        for tag in re.findall(r'<span class="cabin-bin[^"]*"[^>]*>', card_map(html))
    ]


def test_the_card_draws_the_cabin_and_rings_the_zone_it_names(client):
    """One rectangle per bin, one ring, and the ring on the zone the sentence
    above the map names — the drawing and the advice are the same decision."""
    page = client.get(f"/bggo/{KNOWN}").text
    state = client.get("/bggo/api/state").json()
    figure = card_map(page)
    assert 'role="img"' in figure, "the drawing is one image, not 240 tab stops"
    assert figure.count('class="seatmap"') == 1, "the card draws the board's seatmap"
    assert figure.count('data-row="') == state["seats"]["rows"], "a row went missing"
    assert figure.count('class="cabin-letter"') == 2 * len(state["seats"]["letters"])

    zones = map_zones(page)
    assert len(zones) == len(state["bins"]), "a bin the board paints has no zone here"
    ringed = [z for z in zones if "is-advised" in z["class"]]
    assert len(ringed) == 1, "exactly one zone is the zone for this bag"
    assert (ringed[0]["data-name"], ringed[0]["data-side"]) == ("Row 1-4L", "left")
    assert 'id="stow-bin">Row 1-4L<' in page


def test_the_ticket_and_the_board_are_the_same_drawing(client):
    """The sync the whole trial turns on: for one cabin the two views emit the same
    rectangles and the same squares. The card adds a ring to one zone and a fill to
    one seat; it does not get its own geometry, its own ramp or its own lanes."""
    board = client.get("/bggo/live?view=seats").text
    card = card_map(client.get(f"/bggo/{KNOWN}").text)

    def rects(html: str) -> dict:
        out = {}
        for tag in re.findall(r'<span class="cabin-bin[^"]*"[^>]*>', html):
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
            out[(attrs["data-bin"], attrs["data-side"])] = attrs
        return out

    board_rects, card_rects = rects(board), rects(card)
    assert board_rects and board_rects.keys() == card_rects.keys()
    for key, attrs in card_rects.items():
        mine, theirs = dict(attrs), dict(board_rects[key])
        assert mine.pop("class").replace(" is-advised", "") == theirs.pop("class")
        # The one deliberate difference: the board's tooltip carries the load in
        # units, the card's carries the zone's name and its state word instead,
        # because a passenger never reads a unit figure (R25).
        card_title, board_title = mine.pop("title"), theirs.pop("title")
        assert "unit" in board_title and "unit" not in card_title, (board_title, card_title)
        assert card_title.startswith(theirs["data-name"]), card_title
        assert mine == theirs, f"zone {key} is drawn differently on the two views"
    # and the squares: the same seats, in the same order, marked by state
    seats = lambda html: re.findall(r'<span class="seat [^"]*"[^>]*>', html)
    assert [s.replace(" is-you", "") for s in seats(card)] == seats(board)


def test_every_row_of_the_cabin_hangs_under_exactly_one_zone(client):
    """The rectangles tile the cabin nose to tail, on both walls. A zone drawn one
    row too tall would put a bag over a seat it does not hang over."""
    html = client.get(f"/bggo/{KNOWN}").text
    cabin_rows = client.get("/bggo/api/state").json()["seats"]["rows"]
    spans = {"left": 0, "right": 0}
    for zone in map_zones(html):
        spans[zone["data-side"]] += int(re.search(r"--bin-slots:(\d+)", zone["style"]).group(1))
    assert spans == {"left": cabin_rows, "right": cabin_rows}


def test_a_zone_is_one_flat_swatch_in_its_level_colour(client, db):
    """Four trolleys in Row 1-4L is four of nine units: yellow by the ramp, and the
    rectangle is flat in it — the board's swatch, unchanged. Status is the whole
    message; nothing here can grow back into a gauge or a fill bar."""
    for seat in ("1A", "1B", "1C", "2A"):
        assert client.post(
            "/bggo/api/scan", json={"code": seat_code(db, seat), "bag_units": 1}
        ).status_code == 200
    zones = {(z["data-name"], z["data-side"]): z
             for z in map_zones(client.get(f"/bggo/{KNOWN}").text)}
    left = zones[("Row 1-4L", "left")]
    assert (left["data-level"], left["data-load"]) == ("yellow", "4")
    assert re.fullmatch(r"--bin-slots:\d+", left["style"]), left["style"]
    # the wall across the aisle is untouched: same drawing, its own figure
    right = zones[("Row 1-4R", "right")]
    assert (right["data-level"], right["data-load"]) == ("green", "0")
    assert re.fullmatch(r"--bin-slots:\d+", right["style"]), right["style"]


def test_a_redirect_rings_the_zone_across_the_aisle(client, db):
    """The card is allowed to send a bag to the other wall; the ring has to follow
    it there, because the ring is the only thing the sentence can point at."""
    for seat in ("1A", "1B", "1C", "2A", "2B", "2C", "3A", "3B", "3C"):
        assert client.post(
            "/bggo/api/scan", json={"code": seat_code(db, seat), "bag_units": 1}
        ).status_code == 200
    html = client.get(f"/bggo/{seat_code(db, '3C')}").text
    assert 'id="stow-bin">Row 1-4R<' in html and 'data-state="redirect"' in html
    ringed = [z for z in map_zones(html) if "is-advised" in z["class"]]
    assert [(z["data-name"], z["data-side"]) for z in ringed] == [("Row 1-4R", "right")]


def test_the_map_names_itself_in_words_the_server_keeps_true(client):
    """The figure is a single image to a screen reader, so its `aria-label` has to
    say what the marks say — and the scan response carries the same sentence, so a
    check-in that changes the advice cannot leave the old description behind."""
    body = client.post("/bggo/api/scan", json={"code": KNOWN, "bag_units": 1}).json()
    figure = card_map(client.get(f"/bggo/{KNOWN}").text)
    label = re.search(r'aria-label="([^"]*)"', figure).group(1)
    assert "Your seat 1A is marked." in label
    assert "The zone for your bag is Row 1-4L" in label
    assert label == body["cabin_label"], "the page and the response must agree"
    # the one square that is this passenger's, filled and named for the assists
    assert re.search(r'class="seat \w+ is-you" data-seat="1A"', figure)


def test_the_card_adds_its_own_marks_and_no_second_drawing():
    """The card's CSS is allowed a width-solved pitch, its own marks and the map's
    fuselage line (Davis, 2026-11-14). Anything that redraws a rectangle — a lane
    width, a ramp colour, a second cabin — is the drift this trial removed, so
    those are what this test is really about. The accessibility mark lives outside
    this slice, and only inside the coloured rectangle when the reader asks for
    it (`test_colourblind_mode_counts_the_bin_ramp`)."""
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    card = css[css.index("the cabin, on the card"):css.index("bag picker")]
    rules = re.sub(r"/\*.*?\*/", "", card, flags=re.S)   # comments are not declarations
    assert re.search(r"\.cabin-card \.cabin-bin\.is-advised::before\s*{[^}]*var\(--figure\)", css, re.S)
    assert re.search(r"\.cabin-card \.seat\.is-you\s*{[^}]*var\(--accent\)", css, re.S)
    # a taken seat is one flat fill and nothing else: no accent ring, no inset bar
    # down its left edge (they read as a pill with a heavy edge at card size)
    scanned = re.search(r"\n\.seat\.scanned\s*{([^}]*)}", css).group(1)
    assert "box-shadow" not in scanned and "inset" not in scanned, "the taken seat grew an effect"
    assert "border-color: var(--accent)" not in scanned
    assert "background: var(--wash-deep)" in scanned
    assert re.search(r"--wash-deep: color-mix\(in srgb, var\(--accent\) \d+%, var\(--wash\)\)", css), \
        "the taken-seat fill is a mix of tokens, not a new colour"

    # one loud square only: the board's newest check-in is demoted on the card, and
    # the own seat keeps its fill even when it is that newest check-in
    demoted = re.search(r"\.cabin-card \.seat\.recent:not\(\.is-you\)\s*{([^}]*)}", css).group(1)
    assert "box-shadow" not in demoted and "inset" not in demoted
    assert re.search(r"\.cabin-card \.seat\.just-scanned\s*{\s*animation:\s*none", css)
    assert re.search(r"\.cabin-card \.cabin\s*{[^}]*--unit:", css), "the card sizes the board's cabin"
    for own_invention in ("--cm-", "--fill", ".cm-bin", "color-mix"):
        assert own_invention not in rules, f"the card grew its own {own_invention}"
    # the hatch is gone (2026-11-11) and stays gone: the lane carries the level as
    # a flat swatch, and a pattern read as decoration. The marching ring is the one
    # gradient in the file, and it rings a zone — it never fills a rectangle (a
    # bin, a seat) and is never a second drawing.
    bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    gradient_rules = [s.strip() for s in re.findall(r"([^{}]+)\{[^}]*repeating-linear-gradient", bare)]
    assert gradient_rules == [".cabin-card .cabin-bin.is-advised::before"], \
        f"a pattern appears outside the advised ring: {gradient_rules}"
    # the ramp itself still lives in one place: the board's
    for level, token in (("green", "--ok"), ("yellow", "--warn"), ("red", "--error")):
        assert len(re.findall(rf"\.cabin-bin\.load-{level}\s*{{\s*--bin-ink:\s*var\({token}\)",
                              css)) == 1, f"{level} is defined twice"
    # wider than it was, on both views (a .44 hairline was the old number)
    board = re.search(r"--bin-w: calc\(var\(--unit\) \* ([\d.]+)\)", css).group(1)
    assert float(board) >= .8, "the lanes went narrow again"
    assert re.search(r"\.cabin-card \.cabin\s*{[^}]*--bin-w: calc\(var\(--unit\) \* ([\d.]+)\)",
                     css), "the card sets its own lane width (wider than the board's)"
    # and the drawing is one function, not a copy of one
    code = inspect.getsource(server_v1.card_cabin_html).split('"""')[2]
    assert "cabin_html(" in code, "the card must draw with the board's function"
    assert "cabin_bin_rows(" not in code, "the card is tiling zones on its own again"


# --------------------------------------------------------- accessibility
# The ticket page carries one control the board does not: the reader's own text
# size and a colour-blind mode. Both are device preferences applied before paint;
# neither is allowed to invent a colour or a bin level — the size is two root
# font-size rules and the mode is a state that reveals server-rendered icons.

def test_the_card_wears_an_accessibility_control(client):
    html = client.get(f"/bggo/{KNOWN}").text
    assert 'id="a11y-toggle"' in html and 'id="a11y-panel"' in html
    assert re.search(r'id="a11y-toggle"[^>]*aria-expanded="false"', html)
    assert re.search(r'id="a11y-toggle"[^>]*aria-controls="a11y-panel"', html)
    for step in (1, 2):
        assert f'id="a11y-text-{step}"' in html
    assert 'id="a11y-text-3"' not in html, "the third size was removed"
    assert 'id="a11y-cb"' in html
    # the board is a projector the audience does not hold: no reader controls
    assert 'id="a11y-toggle"' not in client.get("/bggo/live").text


def test_the_readers_choice_is_applied_before_paint(client):
    """The size has to be on <html> before the body renders, or the first paint
    is the default size and the card visibly jumps. So the choice is read by an
    inline head script, and app.js only drives the panel afterwards."""
    html = client.get(f"/bggo/{KNOWN}").text
    head = html[: html.index("</head>")]
    assert 'localStorage.getItem("bggo-a11y")' in head
    assert "document.documentElement.dataset.a11yText" in head
    assert "document.documentElement.dataset.a11yCb" in head


def test_text_size_is_two_root_steps():
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    steps = re.findall(r'html\[data-a11y-text="(\d)"\]\s*{\s*font-size:\s*(\d+)%', css)
    assert [value for value, _ in steps] == ["1", "2"]
    assert [int(percent) for _, percent in steps] == [100, 120]
    # the largest size was removed; an old choice of it has to land somewhere sane
    head = server_v1.A11Y_HEAD
    assert 'size === "3"' in head and 'size = "2"' in head
    # the root font-size is what scales, so the body must follow it rather than
    # pinning text at a fixed 16px
    body = re.search(r"\nbody\s*{([^}]*)\}", css).group(1)
    assert "font-size: 1rem" in body and "font-size: 16px" not in body


def test_colourblind_mode_counts_the_bin_ramp():
    """One bag per step — green 1, yellow 2, red 3 — and the cross for the fourth
    fact only: past capacity, not the whole of red."""
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    cb = css[css.index("colour-blind mode"):]
    assert re.search(r'\.cabin-bin\.load-green\s+\.bin-status\s+\.bs-bag:nth-child\(1\)', cb)
    assert re.search(r'\.cabin-bin\.load-yellow\s+\.bin-status\s+\.bs-bag:nth-child\(-n\+2\)', cb)
    assert re.search(r'\.cabin-bin\.load-red\s+\.bin-status\s+\.bs-bag:nth-child\(-n\+3\)', cb)
    assert re.search(r'\.cabin-bin\.is-over\s+\.bin-status\s+\.bs-cross\s*{\s*display:\s*block', cb), \
        "the cross is not tied to past capacity"
    # a mark, not a repaint: figure purple from a token, and no touch of the swatch
    assert "color: var(--figure)" in cb
    assert "mask-image" not in css, "a glyph is inlined with currentColor, not masked"


def test_the_advice_carries_the_past_capacity_flag_the_cross_is_drawn_on():
    """`is-over` is the fourth fact the ramp cannot state; it has to travel with
    the advice so a check-in that pushes a zone over capacity draws the cross."""
    loads = server_v1.bin_index(shed(4, {(1, "left"): 8.0, (1, "right"): 9.5}))
    red = server_v1.stow_payload(4, "1A", "own", "own", (1, "left"), (1, "left"),
                                 loads[(1, "left")]["units"], loads)
    over = server_v1.stow_payload(4, "1A", "own", "own", (1, "right"), (1, "right"),
                                  loads[(1, "right")]["units"], loads)
    assert red["level"] == "red" and red["over"] is False
    assert over["level"] == "red" and over["over"] is True
    js = (server_v1.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'classList.toggle("is-over", !!advice.over)' in js


def test_the_card_carries_the_colourblind_icons_for_every_zone(client):
    figure = card_map(client.get(f"/bggo/{KNOWN}").text)
    zones = re.findall(r'<span class="cabin-bin[^"]*"[^>]*>', figure)
    assert figure.count('class="bin-status"') == len(zones)
    assert figure.count('class="bs-bag"') == 3 * len(zones)
    assert figure.count('class="bs-cross"') == len(zones)
    # one sprite, not one set of paths per bin
    assert figure.count('id="bin-status-bag"') == 1
    assert figure.count('id="bin-status-full"') == 1
    # and the board pays nothing for a phone setting
    board = client.get("/bggo/live?view=seats").text
    assert "bin-status" not in board and "bin-status-bag" not in board


def test_the_accessibility_panel_can_open_while_the_picker_is_up():
    """The picker is what a first-time reader sees, so the control has to sit
    above its scrim or the reader cannot reach it exactly when they need it."""
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    appbar = int(re.search(r"\.appbar\s*{[^}]*z-index:\s*(\d+)", css, re.S).group(1))
    picker = int(re.search(r"\.picker-wrap\s*{[^}]*z-index:\s*(\d+)", css, re.S).group(1))
    assert appbar > picker, "the accessibility control hides under the bag picker"


def test_the_card_script_re_points_the_map_instead_of_redrawing_it():
    js = (server_v1.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "showCabin(data.stow, data.cabin_label)" in js
    assert 'querySelectorAll(".cabin-bin")' in js
    # the level is a class the server names; the script never decides one, and it
    # never measures one either — the colour is the whole of the status
    assert "--fill" not in js and "--cm-" not in js
    for literal in ("BIN_GREEN", "STOW_AWAY", "9.0"):
        assert literal not in js


# ------------------------------------------------------ the live boarding log
# A feed of boardings in the bottom-left corner, read while the cabin is doing
# something else. The sentence is the server's, built from the same decision the
# bins were built from, so a line cannot name a zone the bag is not in. Order is
# **arrival** (the scan row's id), not seat order and not "most recent".

def boarded(seat: str, units: float | None = None, *, scan_id: int = 1,
            seen: str = "2026-11-10T00:00:00+00:00") -> dict:
    """One passenger for `boarding_log()`: a seat, an arrival, maybe a bag."""
    bag = None if units is None else {"units": units, "text": server_v1.unit_text(units)}
    return {"seat_number": seat, "bag": bag, "scanned": True, "scan_id": scan_id,
            "last_seen": seen, "code": f"BGGO-LOG-{scan_id:04d}"}


def log_of(passengers: list[dict], rows: int, limit: int = server_v1.BOARDING_LOG_LIMIT):
    bins, decisions = server_v1.stow_plan(passengers, rows)
    return server_v1.boarding_log(passengers, decisions, rows, limit=limit)


def log_state(client) -> list[dict]:
    return client.get("/bggo/api/state").json()["log"]


def test_the_log_is_empty_and_says_so_before_the_first_scan(client):
    """The panel is in the page from the start: an empty rectangle under a
    boarding screen looks broken, and the first line needs somewhere to land."""
    assert log_state(client) == []
    html = client.get("/bggo/live").text
    assert 'class="log is-empty"' in html
    assert 'aria-label="Boarding log"' in html
    assert "Waiting for the first scan." in html


def test_a_boarding_reads_seat_units_and_the_zone_the_bag_is_in(client):
    """The line the pitch reads out: `[seat] boarded, [units] of carry-on
    [stowed|diverted] to [zone]`."""
    client.post("/bggo/api/scan", json={"code": KNOWN, "bags": {"bag-roller": 1}})
    entry = log_state(client)[0]
    assert entry["seat"] == "1A"
    assert entry["note"] == "boarded, 1 unit of carry-on stowed to Row 1-4L"
    assert entry["text"] == "1A boarded, 1 unit of carry-on stowed to Row 1-4L"
    assert entry["units"] == 1.0
    assert entry["state"] == "own"
    assert entry["zone"] == "Row 1-4L" and entry["text"].endswith(entry["zone"])


def test_a_zone_out_of_room_diverts_and_the_line_names_where_it_went():
    """The log is built from the plan's own decision, so a redirected bag names
    the compartment it is *in* — the one the map paints — and says "diverted"."""
    pax = [boarded("1A", 2.25, scan_id=1), boarded("1B", 2.25, scan_id=2),
           boarded("1C", 2.25, scan_id=3), boarded("2A", 1.25, scan_id=4),
           boarded("2B", 1.0, scan_id=5)]
    log = log_of(pax, 4)
    assert log[0]["note"] == "boarded, 2.25 units of carry-on stowed to Row 1-4L"
    assert log[-1]["note"] == "boarded, 1 unit of carry-on diverted to Row 1-4R"
    assert log[-1]["state"] == "redirect" and log[-1]["zone"] == "Row 1-4R"
    # the zone it names is the zone the load went into, not the one above the seat
    bins, _ = server_v1.stow_plan(pax, 4)
    load = next(b for b in bins if (b["bin"], b["side"]) == (1, "right"))
    assert load["units"] == 1.0 and load["bags"] == 1
    own = next(b for b in bins if (b["bin"], b["side"]) == (1, "left"))
    assert own["units"] == 8.0 and own["bags"] == 4


def test_the_log_reads_the_arrival_order_the_bins_were_built_in():
    """Seats arriving backwards still read forwards: the log follows the scan
    row's id, which is the order `stow_plan()` stows in — first come, served
    first — so the two cannot tell different stories about who was first."""
    pax = [boarded("5C", 1.0, scan_id=2), boarded("1A", 1.0, scan_id=1)]
    assert [e["seat"] for e in log_of(pax, 8)] == ["1A", "5C"]


def test_a_passenger_with_no_bag_still_boards(client):
    """Boarding is the scan row, not the bag: a person carrying nothing is still
    on the plane, and the log says the plain truth rather than inventing 0 units."""
    client.post("/bggo/api/scan", json={"code": KNOWN})
    entry = log_state(client)[0]
    assert entry["text"] == "1A boarded, no carry-on"
    assert entry["units"] == 0.0 and entry["zone"] is None and entry["state"] == "none"


def test_a_repeat_scan_is_one_boarding(client):
    """`hits` is activity, not a second person (D5) — and the bins are not
    re-stowed by it either, so the log must not print the seat twice."""
    client.post("/bggo/api/scan", json={"code": KNOWN, "bag_units": 1})
    client.post("/bggo/api/scan", json={"code": KNOWN, "bag_units": 1})
    state = client.get("/bggo/api/state").json()
    assert len(state["log"]) == 1
    assert state["counters"]["hits"] == 2 and state["counters"]["scanned"] == 1


def test_the_log_keeps_the_newest_lines_and_drops_the_oldest():
    """A log is read at the bottom: the payload carries the tail, so the panel
    shows the last few boardings however long the flight is."""
    pax = [boarded(f"{row}A", 0.25, scan_id=row) for row in range(1, 7)]
    assert [e["seat"] for e in log_of(pax, 40, limit=4)] == ["3A", "4A", "5A", "6A"]
    assert server_v1.BOARDING_LOG_LIMIT >= 10, "a window of three is not a log"
    assert len(log_of(pax, 40)) == 6, "the live limit is not the test's limit"


def test_a_checkout_leaves_the_log_and_a_reset_empties_it(client, controls_token, board_token):
    """The log is derived from `scans`, so it follows the console and the reset
    without a second source of truth to forget about."""
    client.post("/bggo/api/scan", json={"code": KNOWN, "bag_units": 1})
    client.post("/bggo/api/scan", json={"code": KNOWN2, "bag_units": 1})
    assert [e["seat"] for e in log_state(client)] == ["1A", "1B"]

    client.post("/bggo/api/ctl/checkout", json={"code": KNOWN}, headers=ctl(controls_token))
    assert [e["seat"] for e in log_state(client)] == ["1B"]

    client.post("/bggo/api/reset", headers={"X-Board-Token": board_token})
    assert log_state(client) == []


def test_the_board_renders_the_log_in_its_current_state(client):
    """Server-rendered first paint (R9): a JS-less load already shows the
    boardings, and the seat leads the line the way it leads its row."""
    client.post("/bggo/api/scan", json={"code": KNOWN, "bag_units": 1})
    entry = log_state(client)[0]
    html = client.get("/bggo/live").text
    assert 'class="log"' in html and 'class="log is-empty"' not in html
    assert f'data-key="{entry["key"]}"' in html
    assert '<b class="log-seat">1A</b> boarded, 1 unit of carry-on stowed to Row 1-4L' in html
    # newest last, because the log is read at the bottom
    assert html.index('data-key="1"') < html.index("</ol>")


def test_the_log_keys_lines_by_the_scan_row_so_a_poll_does_not_redraw_them():
    """`board.js` compares keys, not text: a key that has not been seen is a new
    line (and animates), one that left the payload is removed, and one whose
    wording the server revised is edited in place."""
    js = (server_v1.STATIC_DIR / "board.js").read_text(encoding="utf-8")
    assert "data.log" in js and "logLines" in js
    assert "appendChild" in js and "scrollTop" in js
    # the sentence itself is the server's; the script must never write one
    for literal in ("boarded", "carry-on", "stowed", "diverted"):
        assert literal not in js, f"board.js writes its own log copy: {literal!r}"


def test_the_log_is_a_quiet_chat_column_on_the_page():
    """The feed is the caption, not the picture: no card, no filled row, no
    accent, no header. On a projector a solid block in the corner outshines the
    cabin, so the column has to read as a lift off the page instead."""
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    log = re.search(r"\.log\s*\{([^}]*)\}", css).group(1)
    assert "position: fixed" in log
    assert "left: 0" in log and "bottom: 0" in log
    # a haze, not a block: two brand tokens mixed, and a hairline edge
    assert "color-mix(" in log and "var(--wash)" in log and "var(--surface)" in log
    assert "border-top: 1px solid var(--rule)" in log
    for plate in ("border-radius", "box-shadow", "background: var(--accent)"):
        assert plate not in log, f"a panel has a {plate}; a chat column does not"
    # the seat is the only mark of the brand it keeps
    seat = re.search(r"\.log-seat\s*\{([^}]*)\}", css).group(1)
    assert "color: var(--accent)" in seat and "font-weight: 700" in seat
    assert "background" not in seat, "no chip: the seat is a word, not a badge"
    assert "font-variant-numeric: tabular-nums" in seat
    # no row is accented — in a chat the newest message is simply the last one,
    # and it is the column moving that says "live"
    assert ":last-child" not in css.split("/* ------------------------------------------------------------ boarding log */")[1].split("/* ------------------------------------------------- theater mode")[0]
    # a whole number of lines tall, so no boarding is cut in half at the top edge
    lines = re.search(r"\.log-lines\s*\{([^}]*)\}", css).group(1)
    assert "overflow-y: auto" in lines
    assert "--row:" in log and "max-height: calc(5 * var(--row))" in lines
    assert "mask-image" not in css


def test_a_new_boarding_floats_up_instead_of_being_stamped_on():
    """The gesture is the movement: the line rises into place and the column
    drifts to it — but only for a line that really is new, only after the first
    paint, and never for a reader who asked for reduced motion."""
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    animation = re.search(r"\.log-line\s*\{\s*animation:([^;]+);", css).group(1)
    assert "log-in" in animation and "ease-out" in animation
    keyframes = re.search(r"@keyframes log-in\s*\{([^}]*)\}", css).group(1)
    assert "translateY" in keyframes and "opacity: 0" in keyframes
    assert "prefers-reduced-motion: no-preference" in css

    js = (server_v1.STATIC_DIR / "board.js").read_text(encoding="utf-8")
    assert 'behavior: "smooth"' in js
    assert "logFirst" in js and "added" in js
    assert "prefers-reduced-motion: no-preference" in js


# ------------------------------------------------------ the control page
# The operator console (/bggo/controls) and its API. It edits `scans` — the
# demo's state — and only `scans`: the module still never writes `passengers`.
# Check-in is idempotent on purpose, so a second click cannot inflate the board.

@pytest.fixture()
def controls_token(db) -> str:
    server_v1.init_db(db)  # idempotent; makes the token readable without a client
    return server_v1.get_controls_token(db_path=db)


def ctl(headers_token: str) -> dict:
    return {"X-Control-Token": headers_token}


def test_controls_token_is_not_the_board_token(db, board_token, controls_token):
    """The board token rides in the open /bggo/live page, so it is public. The
    control token must be a different secret."""
    assert controls_token != board_token


def test_controls_url_is_the_bookmark(client, db, controls_token):
    url = server_v1.controls_url(db_path=db)
    assert url == f"https://sophon1.dedyn.io/bggo/controls?k={controls_token}"


def test_controls_page_requires_the_controls_token(client, controls_token):
    assert client.get("/bggo/controls").status_code == 403
    assert client.get("/bggo/controls", params={"k": "nope"}).status_code == 403
    ok = client.get("/bggo/controls", params={"k": controls_token})
    assert ok.status_code == 200
    assert ok.headers["cache-control"] == "no-store"


def test_controls_page_lists_every_passenger_and_survives_a_search(client, controls_token, db):
    html = client.get("/bggo/controls", params={"k": controls_token}).text
    allocated = rows(db, "SELECT COUNT(*) FROM passengers WHERE full_name IS NOT NULL")[0][0]
    assert html.count('class="ctl-row') == allocated
    for name in ("Au Bai Qiao", "Hui Long Wai"):
        assert name in html
    assert 'id="ctl-search"' in html

def test_controls_page_reuses_the_bag_dialogue_the_qr_code_opens(client, controls_token):
    html = client.get("/bggo/controls", params={"k": controls_token}).text
    assert "Which bags are you bringing?" in html
    assert 'id="ctl-picker"' in html
    assert 'id="confirm"' in html, "style.css keys the confirm button off #confirm"
    for units in ("0.25", "0.5", "0.75", "1"):
        assert f'data-units="{units}"' in html
    for name in ("Handbag", "Backpack", "Duffel bag", "Small trolley case"):
        assert name in html


def test_controls_token_never_reaches_the_board_or_the_card(client, controls_token):
    for path in ("/bggo/live", f"/bggo/{KNOWN}", "/bggo/BGGO-9999-9999"):
        assert controls_token not in client.get(path).text


def test_control_api_requires_the_token(client, controls_token):
    assert client.get("/bggo/api/ctl/passengers").status_code == 403
    assert client.post("/bggo/api/ctl/checkin", json={"code": KNOWN}).status_code == 403
    assert client.get("/bggo/api/ctl/passengers", headers=ctl(controls_token)).status_code == 200


def test_control_list_payload_has_what_the_page_needs(client, controls_token):
    body = client.get("/bggo/api/ctl/passengers", headers=ctl(controls_token)).json()
    one = body["passengers"][0]
    assert set(one) >= {"id", "code", "name", "seat", "checked_in", "hits",
                        "baggage_text", "bag", "mix", "stow"}
    assert one["checked_in"] is False and one["mix"] == {} and one["stow"] is None


def test_control_checkin_lights_the_board_and_is_idempotent(client, db, controls_token):
    r = client.post("/bggo/api/ctl/checkin", json={"code": KNOWN}, headers=ctl(controls_token))
    assert r.status_code == 200 and r.json()["passenger"]["checked_in"] is True
    # second click must not inflate `hits` — that figure means audience scans
    again = client.post("/bggo/api/ctl/checkin", json={"code": KNOWN}, headers=ctl(controls_token))
    assert again.json()["passenger"]["hits"] == 1
    assert client.get("/bggo/api/state").json()["counters"]["scanned"] == 1


def test_control_checkout_deletes_the_scan_row(client, db, controls_token):
    client.post("/bggo/api/ctl/checkin", json={"code": KNOWN}, headers=ctl(controls_token))
    gone = client.post("/bggo/api/ctl/checkout", json={"code": KNOWN}, headers=ctl(controls_token))
    assert gone.json()["passenger"]["checked_in"] is False
    assert rows(db, "SELECT * FROM scans WHERE code = ?", (KNOWN,)) == []


def test_control_checkin_unknown_code_is_404(client, controls_token):
    r = client.post("/bggo/api/ctl/checkin", json={"code": BOGUS}, headers=ctl(controls_token))
    assert r.status_code == 404


def test_control_bag_save_writes_the_pick_and_keeps_the_scan_alone(client, db, controls_token):
    client.post("/bggo/api/ctl/checkin", json={"code": KNOWN}, headers=ctl(controls_token))
    r = client.post("/bggo/api/ctl/bag", json={"code": KNOWN, "bags": {"bag-duffel": 2}},
                    headers=ctl(controls_token))
    p = r.json()["passenger"]
    assert p["bag"]["units"] == 1.5 and p["mix"] == {"bag-duffel": 2}
    assert p["hits"] == 1, "choosing a bag is data entry, not another scan"


def test_control_bag_save_on_an_unscanned_passenger_checks_them_in(client, controls_token):
    p = client.post("/bggo/api/ctl/bag", json={"code": KNOWN2, "bags": {"bag-roller": 1}},
                    headers=ctl(controls_token)).json()["passenger"]
    assert p["checked_in"] is True and p["baggage_text"] == "1 unit"


def test_control_bag_save_rejects_an_empty_choice(client, controls_token):
    r = client.post("/bggo/api/ctl/bag", json={"code": KNOWN, "bags": {"bag-duffel": 0}},
                    headers=ctl(controls_token))
    assert r.status_code == 422


def test_control_checkin_all_only_creates_the_missing_rows(client, db, controls_token):
    client.post("/bggo/api/ctl/checkin", json={"code": KNOWN}, headers=ctl(controls_token))
    body = client.post("/bggo/api/ctl/bulk", json={"action": "checkin_all"},
                       headers=ctl(controls_token)).json()
    allocated = rows(db, "SELECT COUNT(*) FROM passengers WHERE full_name IS NOT NULL")[0][0]
    assert body["affected"] == allocated - 1
    assert rows(db, "SELECT hits FROM scans WHERE code = ?", (KNOWN,))[0][0] == 1
    assert client.get("/bggo/api/state").json()["counters"]["scanned"] == allocated


def test_control_clear_empties_the_scans_table(client, db, controls_token):
    client.post("/bggo/api/ctl/bulk", json={"action": "checkin_all"}, headers=ctl(controls_token))
    client.post("/bggo/api/ctl/bulk", json={"action": "clear"}, headers=ctl(controls_token))
    assert rows(db, "SELECT COUNT(*) FROM scans")[0][0] == 0


def test_control_random_bags_fills_every_checked_in_row(client, controls_token):
    client.post("/bggo/api/ctl/bulk", json={"action": "checkin_all"}, headers=ctl(controls_token))
    body = client.post("/bggo/api/ctl/bulk", json={"action": "random_bags"},
                       headers=ctl(controls_token)).json()
    assert body["affected"] > 0
    passengers = client.get("/bggo/api/ctl/passengers", headers=ctl(controls_token)).json()["passengers"]
    filled = [p for p in passengers if p["checked_in"]]
    assert filled and all(p["bag"] for p in filled)
    assert all(0 < p["bag"]["units"] <= 4 * 3 for p in filled)


def test_control_preboard_ignores_codes_not_on_the_manifest(client, db, controls_token):
    body = client.post(
        "/bggo/api/ctl/bulk",
        json={"action": "preboard", "codes": [KNOWN, BOGUS, "  bggo-hpzh-waj7  "]},
        headers=ctl(controls_token),
    ).json()
    assert body["affected"] == 2
    got = {r[0] for r in rows(db, "SELECT code FROM scans")}
    assert got == {KNOWN, KNOWN2}


def test_control_bulk_rejects_an_unknown_action(client, controls_token):
    r = client.post("/bggo/api/ctl/bulk", json={"action": "nuke"}, headers=ctl(controls_token))
    assert r.status_code == 422


def test_control_css_and_js_carry_no_literal_hex():
    for name in ("control.css", "control.js"):
        text = (server_v1.STATIC_DIR / name).read_text(encoding="utf-8")
        assert re.findall(r"#[0-9a-fA-F]{3,8}\b", text) == [], name


def test_control_page_is_not_reachable_from_the_audience_routes(client, controls_token):
    """/bggo/controls must not be swallowed by the {code} catch-all."""
    r = client.get("/bggo/controls", params={"k": controls_token})
    assert "BaggageGO controls" in r.text


def test_control_page_has_a_stowed_column(client, controls_token):
    html = client.get("/bggo/controls", params={"k": controls_token}).text
    assert "<th>Stowed</th>" in html
    assert 'class="c-stow"' in html


def _ctl_row(client, controls_token, code):
    passengers = client.get("/bggo/api/ctl/passengers", headers=ctl(controls_token)).json()["passengers"]
    return next(p for p in passengers if p["code"] == code)


def test_control_stow_is_empty_until_a_bag_is_on_file(client, controls_token):
    client.post("/bggo/api/ctl/checkin", json={"code": KNOWN}, headers=ctl(controls_token))
    p = _ctl_row(client, controls_token, KNOWN)
    assert p["checked_in"] is True and p["bag"] is None and p["stow"] is None


def test_control_stow_names_a_compartment_once_a_bag_is_on_file(client, controls_token):
    client.post("/bggo/api/ctl/bag", json={"code": KNOWN, "bags": {"bag-roller": 1}},
                headers=ctl(controls_token))
    p = _ctl_row(client, controls_token, KNOWN)
    assert p["stow"] is not None
    assert p["stow"]["bin"].endswith(("L", "R"))
    assert p["stow"]["own_bin"] and p["stow"]["state"] in {"own", "redirect", "packed"}


def test_control_stow_is_the_same_bin_the_card_names(client, controls_token):
    """The console and the ticket must not disagree — both read stow_plan()."""
    client.post("/bggo/api/ctl/bag", json={"code": KNOWN, "bags": {"bag-duffel": 2}},
                headers=ctl(controls_token))
    p = _ctl_row(client, controls_token, KNOWN)
    card = client.get(f"/bggo/{KNOWN}").text
    named = re.search(r'id="stow-bin">([^<]+)<', card)
    assert named and named.group(1) == p["stow"]["bin"]


def test_control_stow_tracks_a_bag_edit_without_a_reload(client, controls_token):
    before = _ctl_row(client, controls_token, KNOWN)["stow"]
    assert before is None
    body = client.post("/bggo/api/ctl/bag", json={"code": KNOWN, "bags": {"bag-backpack": 1}},
                       headers=ctl(controls_token)).json()
    assert body["passenger"]["stow"] is not None


def test_bags_are_stowed_first_come_first_served_in_arrival_order():
    """The walk is a left fold over the arrival order (2026-11-10) — the order
    the bags were recorded, never re-sorted by seat. The last bag in is the one
    that gets displaced when the zone fills."""
    seats = ("1A", "1B", "1C", "2A", "2B", "2C", "3A", "3B", "3C")
    passengers = [bin_pax(seat, 1.0) for seat in seats]
    bins, decisions = server_v1.stow_plan(passengers, 4)
    assert decisions["1A"]["reason"] == "own"
    assert decisions["3B"]["reason"] == "own"
    last = payload(bins, 4, decisions, "3C")
    assert decisions["3C"]["reason"] == "opposite" and last["bin"] == "Row 1-4R"
    placed = {(b["bin"], b["side"]): b for b in bins}
    assert placed[(1, "left")]["units"] == 8.0 and placed[(1, "left")]["bags"] == 8
    assert placed[(1, "right")]["units"] == 1.0 and placed[(1, "right")]["bags"] == 1


def test_a_new_bag_is_rerouted_around_the_bags_already_stowed():
    """First come, first served: appending a passenger's bag cannot move a bag
    already in a zone. The late bag is rerouted around the earlier ones, even when
    its owner sits further back and would have 'boarded first' under the old
    rear-first sort."""
    early = [bin_pax("1A", 3.0), bin_pax("1B", 3.0), bin_pax("1C", 2.0)]
    before_bins = bin_map(early, 40)
    before_bins_plan, before_decisions = server_v1.stow_plan(early, 40)
    assert before_bins[(1, "left")]["units"] == 8.0 and before_bins[(1, "left")]["bags"] == 3
    assert before_bins[(1, "right")]["units"] == 0.0 and before_bins[(1, "right")]["bags"] == 0

    late = early + [bin_pax("2A", 2.0)]          # 2A checks in last
    after_bins = bin_map(late, 40)
    after_plan, after_decisions = server_v1.stow_plan(late, 40)

    # every earlier passenger keeps exactly the zone they were given
    for seat in ("1A", "1B", "1C"):
        assert payload(after_plan, 40, after_decisions, seat)["bin"] == payload(
            before_bins_plan, 40, before_decisions, seat
        )["bin"]
    # and the new bag is the one that moves
    moved = payload(after_plan, 40, after_decisions, "2A")
    assert moved["state"] == "redirect" and moved["bin"] == "Row 1-4R"
    assert after_bins[(1, "left")]["bags"] == 3       # unchanged: nobody was displaced
    assert after_bins[(1, "right")]["units"] == 2.0 and after_bins[(1, "right")]["bags"] == 1


def test_the_board_stows_in_check_in_order_not_seat_order(client, db):
    """First come, first served (2026-11-10): `build_state()` hands the plan its
    bags by scan row id, so a rear-seat passenger who checks in *after* a
    front-seat one is the bag that gets rerouted. Under the old rear-first sort
    the rear bag would have taken the bin and pushed the front one across."""
    order = ("2A", "1A", "1B")                 # 2A checks in before its row-1 peers
    for seat in order:
        client.post("/bggo/api/scan",
                    json={"code": seat_code(db, seat), "bags": {"bag-roller": 2, "bag-duffel": 1}})
    r = client.post("/bggo/api/scan", json={"code": seat_code(db, "1C"), "bags": {"bag-roller": 1}})
    assert r.json()["stow"]["state"] == "redirect" and r.json()["stow"]["bin"] == "Row 1-4R"
    loads = {(b["bin"], b["side"]): b for b in client.get("/bggo/api/state").json()["bins"]}
    assert (loads[(1, "left")]["units"], loads[(1, "left")]["bags"]) == (8.25, 3)
    assert (loads[(1, "right")]["units"], loads[(1, "right")]["bags"]) == (1.0, 1)


def test_the_trigger_sends_a_bag_away_even_when_it_would_fit():
    """The 8-unit line wins over the fit: at 8.25 of 9 a handbag still fits, but
    the zone is past the line the client drew, so the card moves it."""
    advice = server_v1.stow_advice(shed(40, {(5, "left"): 8.25}), 40, "5A")
    assert advice["reason"] == "opposite" and advice["bin"] == "Row 5-8R"
    # ... and just below the line it stays where it is
    advice = server_v1.stow_advice(shed(40, {(5, "left"): 7.75}), 40, "5A")
    assert advice["reason"] == "own" and advice["bin"] == "Row 5-8L"


def test_a_bag_that_cannot_fit_is_not_stowed_where_it_does_not_fit():
    """The fit rule is what makes "predicted = stowed" true: nothing is ever put
    in a compartment it does not fit in unless nothing nearby will take it."""
    full = {(5, "left"): 8.25, (5, "right"): 8.2, (1, "left"): 8.3,
            (1, "right"): 8.1, (9, "left"): 8.4}
    loads = server_v1.bin_index(shed(40, full))
    order = server_v1.stow_order(40, 5, "left")
    # nothing is below the line, so the first that still *fits* gets it: 8.25+0.25
    reason, key, state = server_v1.stow_target(loads, order, 0.25)
    assert (reason, key, state) == ("own", (5, "left"), "own")
    # a trolley fits nowhere at all: the emptiest is named and will go over
    reason, key, state = server_v1.stow_target(loads, order, 1.0)
    assert state == "packed" and key == (1, "right")


def test_the_stowage_copy_moves_you_and_does_not_do_arithmetic():
    """One read, on a phone, in an aisle: two sentences and a state word. The unit
    figure is the board's business, and the passenger's line carries no em dash
    (Davis, 2026-11-10)."""
    def note_when(reason: str, loads: dict) -> str:
        advice = server_v1.stow_advice(shed(40, loads), 40, "5A")
        assert advice["reason"] == reason, f"expected {reason}, got {advice['reason']}"
        return advice["note"]

    own_full = {(5, "left"): 8.0}
    assert note_when("opposite", own_full) == (
        "The zone above your seat is nearly full, please use the bin across the aisle."
    )
    opposite_full = {**own_full, (5, "right"): 8.0}
    assert note_when("ahead", opposite_full) == (
        "The zone above your seat is nearly full, please use the bin in front of you."
    )
    ahead_full = {**opposite_full, (1, "left"): 8.0}
    assert note_when("opposite-ahead", ahead_full) == (
        "The zone above your seat is nearly full, please use the bin opposite and in front of you."
    )
    both_full = {**ahead_full, (1, "right"): 8.0}
    assert note_when("behind", both_full) == (
        "The zone above your seat is nearly full, please use the bin behind you."
    )
    # nothing qualifies any more: the emptiest is named (8.25 is the opposite wall)
    packed = {(5, "left"): 9.0, (5, "right"): 8.25, (1, "left"): 9.25,
              (1, "right"): 9.0, (9, "left"): 10.0}
    advice = server_v1.stow_advice(shed(40, packed), 40, "5A")
    assert advice["state"] == "packed" and advice["bin"] == "Row 5-8R"
    assert advice["note"] == (
        "Every zone near your seat is nearly full. Please use the bin across the aisle."
    )

    # the figure is not lost, it moved: the board's tooltip still carries it
    assert "of 9 units" in server_v1.bin_loads([bin_pax("1A", 0.25)], 4)[0]["title"]


# --------------------------------------------------- state snapshots
# Export a JSON file of the whole board, reset, import it back — the repeatable
# rehearsal. `settings` (the tokens) is deliberately outside the snapshot.

def test_export_is_a_download_of_both_tables_and_no_tokens(client, controls_token, db):
    client.post("/bggo/api/ctl/bag", json={"code": KNOWN, "bags": {"bag-roller": 2}},
                headers=ctl(controls_token))
    r = client.get("/bggo/api/ctl/export", headers=ctl(controls_token))
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.json"')
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    allocated = rows(db, "SELECT COUNT(*) FROM passengers")[0][0]
    assert body["format"] == "baggagego.state" and body["version"] == 1
    assert body["counts"] == {"passengers": allocated, "scans": 1}
    assert KNOWN in {p["code"] for p in body["passengers"]}
    assert body["scans"][0]["code"] == KNOWN
    assert controls_token not in r.text and "board_token" not in r.text


def test_export_clear_import_restores_the_exact_board(client, controls_token, db):
    """The acceptance: partially board, export, reset, import — same state."""
    for code, bags in ((KNOWN, {"bag-roller": 2}), (KNOWN2, {"bag-duffel": 2})):
        client.post("/bggo/api/ctl/bag", json={"code": code, "bags": bags}, headers=ctl(controls_token))
    client.post("/bggo/api/ctl/checkin", json={"code": "BGGO-NZM2-1YQQ"}, headers=ctl(controls_token))
    before = client.get("/bggo/api/state").json()
    assert before["counters"]["scanned"] == 3

    snapshot = client.get("/bggo/api/ctl/export", headers=ctl(controls_token)).json()
    client.post("/bggo/api/ctl/bulk", json={"action": "clear"}, headers=ctl(controls_token))
    assert client.get("/bggo/api/state").json()["counters"]["scanned"] == 0

    imported = client.post("/bggo/api/ctl/import", json=snapshot, headers=ctl(controls_token))
    assert imported.status_code == 200
    allocated = rows(db, "SELECT COUNT(*) FROM passengers")[0][0]
    assert imported.json()["counts"] == {"passengers": allocated, "scans": 3}

    after = client.get("/bggo/api/state").json()
    assert after["passengers"] == before["passengers"]
    assert after["seats"] == before["seats"]
    assert after["bins"] == before["bins"]
    assert after["counters"] == before["counters"]
    # a new scan after the import must not collide with a restored id
    fresh = client.post("/bggo/api/ctl/checkin", json={"code": "BGGO-WBDY-WBAF"},
                        headers=ctl(controls_token))
    assert fresh.status_code == 200 and fresh.json()["passenger"]["hits"] == 1


def test_import_replaces_the_whole_board_not_just_merges(client, controls_token):
    snapshot = client.get("/bggo/api/ctl/export", headers=ctl(controls_token)).json()
    snapshot["scans"] = []                      # a 20-passenger, nobody-boarded board
    client.post("/bggo/api/ctl/checkin", json={"code": KNOWN}, headers=ctl(controls_token))
    client.post("/bggo/api/ctl/import", json=snapshot, headers=ctl(controls_token))
    assert client.get("/bggo/api/state").json()["counters"]["scanned"] == 0


def test_import_rejects_a_snapshot_whose_rows_have_no_code(client, controls_token):
    r = client.post("/bggo/api/ctl/import",
                    json={"passengers": [{"full_name": "nobody"}], "scans": []},
                    headers=ctl(controls_token))
    assert r.status_code == 422
    # and the failed import left the board untouched
    assert client.get("/bggo/api/state").json()["counters"]["scanned"] == 0


def test_import_rejects_a_newer_snapshot_version(client, controls_token):
    r = client.post("/bggo/api/ctl/import",
                    json={"version": 99, "passengers": [], "scans": []},
                    headers=ctl(controls_token))
    assert r.status_code == 422


def test_import_normalises_a_dict_bag_mix(client, controls_token):
    client.post("/bggo/api/ctl/bag", json={"code": KNOWN, "bags": {"bag-backpack": 2}},
                headers=ctl(controls_token))
    snapshot = client.get("/bggo/api/ctl/export", headers=ctl(controls_token)).json()
    snapshot["scans"][0]["bag_mix"] = json.loads(snapshot["scans"][0]["bag_mix"])  # object, not a string
    client.post("/bggo/api/ctl/bulk", json={"action": "clear"}, headers=ctl(controls_token))
    client.post("/bggo/api/ctl/import", json=snapshot, headers=ctl(controls_token))
    p = _ctl_row(client, controls_token, KNOWN)
    assert p["bag"]["units"] == 1.0 and p["mix"] == {"bag-backpack": 2}


def test_export_state_and_import_state_are_plain_functions(db):
    """The CLI paths call these directly, so pin them without a client."""
    server_v1.init_db(db)
    conn = server_v1.connect(db)
    try:
        conn.execute("INSERT INTO scans (code, known, first_seen_at, last_seen_at, hits, bag_units, bag_mix) "
                     "VALUES (?, 1, ?, ?, 1, 1.0, ?)",
                     (KNOWN, server_v1.utcnow_iso(), server_v1.utcnow_iso(), '{"bag-roller": 1}'))
        conn.commit()
        snapshot = server_v1.export_state(conn)
    finally:
        conn.close()
    allocated = rows(db, "SELECT COUNT(*) FROM passengers")[0][0]
    assert snapshot["counts"] == {"passengers": allocated, "scans": 1}
    conn = server_v1.connect(db)
    try:
        server_v1.import_state(conn, snapshot)
        assert conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 1
        assert conn.execute("SELECT value FROM settings WHERE key = 'board_token'").fetchone() is not None
    finally:
        conn.close()


def test_control_ticket_codes_link_to_the_boarding_pass(client, controls_token):
    """The Ticket column is the way into the card — each code is a link."""
    html = client.get("/bggo/controls", params={"k": controls_token}).text
    assert f'href="/bggo/{KNOWN}"' in html
    assert f">{KNOWN}</a>" in html
    # and the link is not swallowed by the old plain-text cell
    assert f'<td class="c-code mono">{KNOWN}</td>' not in html


def test_the_cabin_sizing_keys_off_the_pointer_not_the_width():
    """A browser zoom shrinks the CSS viewport, so a `max-width` query fires at
    high zoom on a desktop and re-sizes the cabin out from under the reader. Phone
    sizing therefore keys off the pointer, and no width query may touch `--unit`.
    """
    css = (server_v1.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert re.search(
        r"@media\s*\(hover: none\)\s*and\s*\(pointer: coarse\)\s*\{[^}]*--unit", css
    ), "the phone cabin rule must be pointer-gated"

    narrow = re.findall(r"@media\s*\(max-width[^{]*\{(.*?)\n\}", css, re.S)
    assert narrow, "the narrow-window block went missing — check this test"
    assert all("--unit" not in block for block in narrow), "a width query resizes the cabin"


# ---------------------------------------------- trickle: random + baggage
# The trickle deals each arrival a random moment inside the window and a random
# load inside the operator's unit range. The range is a page field; the mix is
# generated on the server from the same caps everything else uses.

def test_control_checkin_can_carry_an_explicit_bag_list(client, controls_token):
    p = client.post("/bggo/api/ctl/checkin", json={"code": KNOWN, "bags": {"bag-duffel": 2}},
                    headers=ctl(controls_token)).json()["passenger"]
    assert p["checked_in"] is True and p["bag"]["units"] == 1.5


def test_control_checkin_rejects_an_explicit_list_over_the_caps(client, controls_token):
    # the trolley's own limit is 2, so three of them is not a valid declaration
    r = client.post("/bggo/api/ctl/checkin", json={"code": KNOWN, "bags": {"bag-roller": 3}},
                    headers=ctl(controls_token))
    assert r.status_code == 422


def test_control_checkin_deals_a_random_load_inside_the_unit_range(client, controls_token):
    p = client.post("/bggo/api/ctl/checkin",
                    json={"code": KNOWN, "min_units": 0.25, "max_units": 1.5},
                    headers=ctl(controls_token)).json()["passenger"]
    assert p["checked_in"] is True
    assert p["bag"] is not None and 0.25 <= p["bag"]["units"] <= 1.5


def test_mix_in_range_stays_in_range_and_inside_the_caps():
    rng = random.Random(20261111)
    for _ in range(500):
        mix, units = server_v1.ctl_mix_in_range(0.25, 1.5, rng)
        assert mix, "the trickle must never deal an empty load"
        assert 0.25 - 1e-9 <= units <= 1.5 + 1e-9, units
        # and it is a real declaration: per-bag limits + the MAX_ITEMS total cap
        assert server_v1.parse_mix(mix) == mix


def test_mix_in_range_hits_the_ends_of_the_asked_range():
    rng = random.Random(1)
    assert server_v1.ctl_mix_in_range(1.5, 1.5, rng)[1] == 1.5
    assert server_v1.ctl_mix_in_range(0.25, 0.25, rng)[1] == 0.25
    # a range with no exact rung picks the nearest (1.0 vs 1.25 around 1.15)
    assert server_v1.ctl_mix_in_range(1.1, 1.2, rng)[1] == 1.25


def test_mix_in_range_with_a_zero_ceiling_is_no_bag():
    assert server_v1.ctl_mix_in_range(0.0, 0.0) == (None, 0.0)
    assert server_v1.ctl_mix_in_range(0.25, 0) == (None, 0.0)  # min > max = cleared


def test_control_checkin_with_a_zero_ceiling_checks_in_without_a_bag(client, controls_token):
    p = client.post("/bggo/api/ctl/checkin",
                    json={"code": KNOWN, "min_units": 0, "max_units": 0},
                    headers=ctl(controls_token)).json()["passenger"]
    assert p["checked_in"] is True and p["bag"] is None


def test_control_page_has_the_trickle_baggage_range(client, controls_token):
    html = client.get("/bggo/controls", params={"k": controls_token}).text
    assert 'id="ctl-trickle-min"' in html and 'id="ctl-trickle-max"' in html
    assert 'value="0.25"' in html and 'value="1.5"' in html


def test_the_trickle_is_not_a_metronome_and_carries_baggage():
    """Source-level: the old fixed gap is gone, the schedule is random, and the
    range travels with each check-in."""
    js = (server_v1.STATIC_DIR / "control.js").read_text(encoding="utf-8")
    assert "trickleSchedule" in js and "Math.random() * totalMs" in js
    assert "(secs * 1000) / codes.length" not in js, "the metronome gap is back"
    assert "min_units" in js and "max_units" in js


def test_the_newest_seat_is_not_marked_on_the_seat_map(client):
    """The newest check-in is marked on the **list** (the pinned row), not on the
    cabin: a second loud square in a picture whose one loud mark is the
    passenger's own seat reads as *selected* (requested 2026-11-10). The scan
    still lights the seat — it just is not singled out."""
    client.post("/bggo/api/scan", json={"code": KNOWN})      # Au Bai Qiao, 1A

    state = client.get("/bggo/api/state").json()
    assert state["seats"]["cells"], "the payload must still carry the cabin"
    assert not [c for c in state["seats"]["cells"] if c["recent"]]
    assert not [c for c in state["seats"]["cells"] if c["state"] == "recent"]
    assert {c["state"] for c in state["seats"]["cells"] if c["scanned"]} == {"scanned"}

    html = client.get("/bggo/live?view=seats").text
    assert 'class="seat recent"' not in html
    assert re.search(r'class="seat scanned" data-seat="1A"', html)
    assert re.search(r'class="seat waiting" data-seat="1B"', html)

    # and the list keeps its pin, so removing the cabin's mark removed only that
    listed = client.get("/bggo/live").text
    assert re.search(r'class="row scanned recent"[^>]*data-code="' + KNOWN, listed)
