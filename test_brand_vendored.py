"""The vendored brand kit must stay byte-identical to the kit it came from.

``pitch-demo/brand/`` exists so the demo stands alone: a container build, a
folder copied out of the repo, no sibling checkout. The kit is still the source
of truth (``brand.json`` -> ``build.py`` -> ``brand.css``), so a snapshot is only
trustworthy while it matches — and a stale one is invisible, because the demo
would keep serving yesterday's tokens and yesterday's lockup quite happily.

This file is the guard. It fails the moment ``./brand/`` drifts from a
``../brand/`` that is present, and skips with a reason when the kit is absent —
which is exactly the standalone case the snapshot exists for. Re-vendoring is a
copy, not a merge:

    cp brand/brand.css brand/fonts/Montserrat-var.ttf brand/fonts/OFL.txt \\
       brand/icons/svg/*.svg pitch-demo/brand/{brand.css,fonts/,icons/svg/}
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
VENDORED = HERE / "brand"
KIT = HERE.parent / "brand"

# Exactly what the app serves: /bggo/brand/brand.css, /bggo/brand/fonts/… and
# /bggo/brand/icons/*. The three routes in server_v1 read the first two by path;
# the third mounts the whole icons/svg directory, so every mark in it is served
# and every mark in it is compared. The OFL licence is not served but must travel
# with the font (OFL 1.1, condition 2), so it is held to the same standard.
SERVED_FILES = (
    "brand.css",
    "fonts/Montserrat-var.ttf",
    "fonts/OFL.txt",
    "icons/svg/icon-bag.svg",
    "icons/svg/icon-check.svg",
    "icons/svg/icon-plane.svg",
    "icons/svg/icon-tag.svg",
    "icons/svg/baggagego-icon.svg",
    "icons/svg/baggagego-icon-white.svg",
    "icons/svg/baggagego-wordmark.svg",
    "icons/svg/baggagego-wordmark-white.svg",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_snapshot_holds_every_file_the_app_serves():
    """Every route must have a real file behind it — a missing icon 404s
    silently on a page, and a missing brand.css leaves the UI tokenless."""
    missing = [rel for rel in SERVED_FILES if not (VENDORED / rel).is_file()]
    assert missing == [], f"pitch-demo/brand/ is missing {missing}"


@pytest.mark.parametrize("rel", SERVED_FILES)
def test_vendored_file_is_byte_identical_to_the_kit(rel):
    if not (KIT / rel).is_file():
        pytest.skip(f"no ../brand/{rel} — standalone checkout, nothing to compare against")
    assert digest(VENDORED / rel) == digest(KIT / rel), (
        f"pitch-demo/brand/{rel} has drifted from brand/{rel}; re-vendor it "
        f"(see this file's docstring) rather than editing the snapshot"
    )


def test_vendored_css_is_the_generated_tokens_file():
    """A hand-edited snapshot is the failure this whole directory invites: the
    kit's rule is that brand.json is the only file holding a literal hex, so if
    the vendored css stops looking generated, someone has been at it."""
    css = (VENDORED / "brand.css").read_text(encoding="utf-8")
    assert "GENERATED" in css.upper(), "brand.css looks hand-written, not build.py output"
    assert "--hke-purple:#702B91" in css
