"""tickets.md must not claim to be the printed payload when it is not.

The printed QRs encode one frozen hostname; the generated link list can be pointed
at a *different* host (a Cloud copy, say) with `BGGO_PUBLIC_BASE`. The codes are
host-independent, so pointing the list elsewhere is safe — but the file has to say
so, or it reads as "this is what is on the paper", which would be false.

The other half of the contract is structural: two renderings with different bases
must be **identical apart from the base string**, so a copy of the list served from
another host can be shown to differ from the printed one in its links and nothing
else.
"""
from __future__ import annotations

import pytest

import make_ticket_links as links

OTHER_BASE = "https://baggagego-abc123.run.app"

ROWS = [
    {"id": 1, "seat_number": "1A", "full_name": "Au Bai Qiao", "code": "BGGO-ZWY9-YCJS"},
    {"id": 7, "seat_number": "2A", "full_name": "Aaron Lam", "code": "BGGO-PEN8-Z5Q2"},
]


def both_bases_erased(text: str) -> str:
    """Erase either hostname, so only the structure is left to compare."""
    return text.replace(links.PRINTED_BASE, "<BASE>").replace(OTHER_BASE, "<BASE>")


def test_links_use_the_frozen_printed_base_by_default():
    text = links.render(ROWS)
    assert links.PRINTED_BASE == "https://sophon1.dedyn.io"
    assert f"[open]({links.PRINTED_BASE}/bggo/BGGO-ZWY9-YCJS)" in text
    assert "host-independent" in text  # true whether or not the two lines match


@pytest.mark.parametrize("base", [OTHER_BASE, "http://localhost:8091"])
def test_an_overridden_base_is_named_not_implied(base):
    text = links.render(ROWS, base=base)

    assert f"[open]({base}/bggo/BGGO-ZWY9-YCJS)" in text
    assert f"{base}/bggo/1A" not in text  # the link is the code, never the seat

    # the printed payload is still stated, and the two hosts are not conflated
    assert f"`{links.PRINTED_BASE}/bggo/<CODE>`" in text
    assert f"`{base}/bggo/<CODE>`" in text


def test_two_renderings_differ_only_in_the_base():
    """The invariant the host-base rewrite rests on, checked on the real files."""
    printed = links.render(ROWS)
    elsewhere = links.render(ROWS, base=OTHER_BASE)

    assert printed != elsewhere
    assert both_bases_erased(printed) == both_bases_erased(elsewhere), (
        "the renderings must differ in the base string and nothing else, or the "
        "published repo cannot be shown to differ from the working copy only in links"
    )


def test_the_link_is_the_code_never_the_seat():
    text = links.render(ROWS)
    for row in ROWS:
        assert f"[open]({links.PRINTED_BASE}/bggo/{row['code']})" in text
