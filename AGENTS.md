# BaggageGO — repo scope

The BaggageGO pitch demo: FastAPI + one SQLite file + a single container. Audience-facing
surfaces are the passenger card (`/bggo/<CODE>`), the live board (`/bggo/live`), and the
operator console (`/bggo/controls?k=…`). Submitted repo:
`git@github.com:Fidexy/BaggageGo.git` (branch `main`).

**This file is identical in both copies of the demo** (see below) — it is about the pair,
not about which one you happen to be standing in.

## The two copies

| path | role |
|---|---|
| `~/Hackathon/pitch-demo` | the **source**. The container on our host builds from it; edits happen here, then are published |
| `~/BaggageGo` | the **submitted repo**, derived from the source — reaching it by hand-copy is not the path |
| `~/Hackathon` | the repo *containing* pitch-demo; its own `AGENTS.md`/`TODO.md` are agent scope, not this demo |

They must be byte-identical **except for the link base**, and one script owns that
invariant. Run it *from the source* — its `SRC` defaults to the script's own parent:

```bash
cd ~/Hackathon/pitch-demo && ./deploy/publish.sh --check   # verify, change nothing
REPO_BASE=https://<cloud url> ./deploy/publish.sh --push    # sync + set the link base
```

`--check` names every file that differs for a reason other than the link base, and is
safe to run any time. Two files may differ *only* in the link base (`README.md`'s
`<!--repo-base-->…<!--/repo-base-->` marker and the generated `tickets.md`), and even
those are compared with both bases erased. Do not hand-copy between the trees: the
reason is that drift here is invisible — on 2026-11-13 the two were 11 files / ~16 h
apart and nobody noticed until asked.

## Hosting

| file | what it is |
|---|---|
| `deploy/gcp/README.md` | the two first paths: **A** a persistent VM (+ TLS), **B** a Cloud Run URL in ten minutes. Default model is *two hosts side by side* — the frozen name stays on the homelab |
| `deploy/gcp/CUTOVER.md` | the runbook that **moves the frozen name to the cloud** (homelab → VM, Caddy + own Let's Encrypt cert, state moved once, rollback) |
| `deploy/gcp/failover/` | opt-in front door on the VM: answers for the frozen name, falls back to the cloud copy when the homelab stops answering |
| `deploy/gcp/sync.sh` | board state between two hosts, **one-way source → mirror**, settings-free snapshot (never carries tokens) |
| `deploy/gcp/backup.sh` | daily snapshot of the `bggo-data` volume — the only irreplaceable thing |
| `deploy/HOMELAB.md` | how it runs on our own host today: route, DNS trap, unit, checklist |

State of the world 2026-11-13: **nothing is deployed.** No `deploy/gcp/.env`, no
`.run.app` URL baked anywhere, and `gcloud` is **not installed on this host** (`docker`
is). The link base is still `https://sophon1.dedyn.io` in both copies.

`CUTOVER.md` keeps *dev / agent work* on the homelab after the serving moves — so
"deploy to Cloud" does not by itself change which copy is the source. That stays
`publish.sh`'s decision until it is deliberately changed in both the script and
`deploy/HOMELAB.md`.

## D9 — the printed passes

`server_v1.py:PUBLIC_BASE` (default `https://sophon1.dedyn.io`) + `BASE` are baked into
every QR payload (`boarding-pass/make_qr_v1.py:BASE_URL` is the other frozen half), and
the passes **already exist on paper**. If that name stops resolving, every printed
ticket dies. Exactly two options: leave the name on the homelab, or move it **once** with
`deploy/gcp/CUTOVER.md` (or the failover front door). Never reprint. `BGGO_PUBLIC_BASE`
overrides the host in generated output (`make_ticket_links.py`) — use it for a *deploy*,
never for the passes in the room.

## Commands

```bash
docker compose up                       # the whole demo on :8091 (board, cards, API)
docker compose run --rm demo pytest -q  # the suite, inside the image
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn server_v1:app --host 127.0.0.1 --port 8091
.venv/bin/python test_brand_vendored.py && .venv/bin/python test_make_ticket_links.py
```

Gates verified 2026-11-13 in both copies: pytest **229 passed / 11 skipped**; both
standalone scripts exit 0; `publish.sh --check` exit 0.

## Gotchas

- `passengers.db` in the repo is **only the seed the image carries**. Live state is in
  the container's `bggo-data` volume — it stopped being this file when the demo moved
  into Docker (2026-11-11). Export/import from the running container.
- `snapshots/` is presenter scratch (board exports) and is gitignored — not part of the
  demo, and its absence from one copy is not drift.
- Zones are named `Row 1-4L`, not `1-4L`: the bare figure reads like a code where a
  person has to find it on a card. `bin_name()` is the single line holding the convention.
- The passenger card prints **no unit figures** — counts and a verb. The board keeps
  every figure, because the projector is where the arithmetic is actionable.
