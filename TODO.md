# TODO — BaggageGO

Scope: the demo is feature-complete as of 2026-11-13. `~/BaggageGo` was re-synced to the
dev tree (`639c847`) and `deploy/publish.sh --check` is green again. Work from here is
**hosting on Google Cloud**. Details and commands: `AGENTS.md`.

This file is identical in both copies; items that only make sense on one side say so.

## Decide first — these two block the rest

- [ ] **Which hosting model.** `deploy/gcp/README.md` Path A/B assume *two hosts side by
      side* and keep `sophon1.dedyn.io` on the homelab. `deploy/gcp/CUTOVER.md` (and the
      `failover/` front door) **move the frozen name to the cloud**. Different runbooks,
      different exposure on D9 — pick one before touching DNS.
- [ ] **Whether the source moves.** `deploy/publish.sh` treats `~/Hackathon/pitch-demo` as
      `SRC` and `~/BaggageGo` as derived; `deploy/HOMELAB.md` and `CUTOVER.md` say the
      same, and `CUTOVER.md` keeps dev work on the homelab even after serving moves. If
      hosting work should happen *in `~/BaggageGo`* instead, change `SRC`/`DST` in the
      script and both docs in one commit — otherwise the first edit there reads as drift
      and `--check` will refuse it (it did exactly that on 2026-11-13, for `AGENTS.md`).

## Then

- [ ] `gcloud` is **not installed on this host** (`docker` is). Install + `gcloud auth
      login`, or drive the VM path from elsewhere.
- [ ] No `deploy/gcp/.env` yet — create it from `.env.example` (never commit it).
- [ ] Stand the host up (Path A VM or Path B Cloud Run) with the frozen name **not yet**
      involved.
- [ ] `deploy/gcp/backup.sh` on the VM *before* any state moves.
- [ ] Move board state once, from the homelab **container's** `bggo-data` volume — not
      from the repo's `passengers.db` (that is only the seed).
- [ ] Pick the `REPO_BASE` the submitted README/tickets should point at, then
      `REPO_BASE=… ./deploy/publish.sh --push` from the source so the link boxes agree.
- [ ] Push `639c847` — `~/BaggageGo` is `ahead 1` of `origin/main`, local only.
- [ ] D9: keep `sophon1.dedyn.io` resolving, or move it **once** and verify from a phone
      on mobile data (wifi may resolve to the LAN and prove nothing).

## Later

- [ ] `deploy/HOMELAB.md` is homelab-scoped but carried in both copies because
      `README.md` links it. Keep, or split it out when the homelab stops serving.
- [ ] If the two-copy setup is ever dropped for a single tree, retire
      `deploy/publish.sh` in the same commit — a lone tree makes its whole premise moot.
