# Deploying the demo to Google Cloud

**Goal:** run the demo on Google Cloud *as well*, while `sophon1.dedyn.io` keeps
resolving to the homelab and keeps serving the printed boarding passes. Two hosts,
two audiences, one frozen URL that never moves.

```
printed QR  ──►  https://sophon1.dedyn.io/bggo/<CODE>   →  homelab (NPM → :8091)     ← UNTOUCHED
submission  ──►  https://<cloud run url>/bggo/live      →  Google Cloud
```

That means: **no DNS change, no NPM change, no `systemctl` change.** Nothing in this
directory writes to the homelab, and nothing here touches the frozen payload
(`make_qr_v1.BASE_URL`) or the app's `PUBLIC_BASE` default.

There is one model where the frozen name *does* move — a failover front door on
the VM that answers for `sophon1.dedyn.io` and falls back to the cloud copy when
the homelab stops answering. See "If the printed passes must survive the homelab"
below; it is opt-in and it is one DNS edit, once.

## Two boards, two states — say it out loud

The board's state is a SQLite file. The homelab and the Cloud each have their own,
so **the two hosts show different cabins** and neither one's scans appear on the
other. That is the deal: the homelab copy is the one the audience's printed passes
feed, the Cloud copy is the one a judge or a reviewer opens. If you ever want one
shared board, that is Postgres (Cloud SQL) plus a `store.py` that speaks it — a code
change, not a deployment change.

State travels between them only by hand, with the snapshot they already have — or
automatically, one way, with [`sync.sh`](sync.sh):

```bash
SOURCE_URL=http://127.0.0.1:8091 MIRROR_URL=https://<cloud url> ./deploy/gcp/sync.sh --install-cron
```

The snapshot is **settings-free** by design, so a sync never carries the board or
control tokens across, and it can never lock you out of the instance it wrote to
(proven: a mirror with its own rotated token still opens its own console after a
sync, while the source's token gets 403). It is **one-way** — the mirror's own scans
are replaced — so point it at a copy nobody is meant to interact with. Manual form:

```bash
pitch-demo/.venv/bin/python pitch-demo/server_v1.py --export /tmp/board.json   # from either side
# …then import it on the other (see "Moving the board's state" below)
```

## If the printed passes must survive the homelab dying

This is the one model where the frozen name moves — once — to a front door that can
fail over:

```
phone --https--> front door (VM) --https--> homelab (NPM)     normal
                                  \--http--> demo (cloud)      homelab down
```

Files: [`failover/compose.yaml`](failover/compose.yaml),
[`failover/Caddyfile`](failover/Caddyfile), [`failover/.env.example`](failover/.env.example).
The QR payload still never changes — nothing is reprinted; but the A record for
`sophon1.dedyn.io` now points at the VM.

**One-time setup, in this order:**

1. Give the homelab a name that keeps tracking its home IP. Its A record is
   maintained today by *something* (almost certainly the router's DDNS client) —
   repoint that updater at a NEW record, e.g.
   `home.sophon1.dedyn.io  A  <home ip>`, and leave it to it.
2. Point `sophon1.dedyn.io` A → the VM's static IP. This is the only time that
   record moves.
3. Deploy the front door:

   ```bash
   cd ~/Hackathon/pitch-demo   # the repo root
   # .env next to failover/compose.yaml:
   #   SITE_HOST=sophon1.dedyn.io
   #   HOMELAB_UPSTREAM=https://home.sophon1.dedyn.io   (the DDNS name)
   #   HOMELAB_SNI=sophon1.dedyn.io                     (what NPM's cert is for)
   #   FALLBACK_UPSTREAM=demo:8091
   #   IMAGE=…                                          (same image as the plain stack)
   gcloud compute scp deploy/gcp/failover/compose.yaml deploy/gcp/failover/Caddyfile \
     bggo-demo:/opt/baggagego/ --zone=us-central1-b
   gcloud compute ssh bggo-demo --zone=us-central1-b \
     --command 'cd /opt/baggagego && sudo docker compose up -d'
   ```

   Caddy issues its own certificate for the frozen name (HTTP-01), so the printed
   URL stays real HTTPS. No NPM change is needed — the hop presents Host/SNI
   `sophon1.dedyn.io`, which is the certificate NPM already holds.

4. Check both directions:

   ```bash
   curl -sI https://sophon1.dedyn.io/bggo/live | head -1   # 200 — the homelab answered
   sudo systemctl stop baggagego                          # on the homelab
   curl -sI https://sophon1.dedyn.io/bggo/live | head -1   # 200 — the cloud answered
   sudo systemctl start baggagego
   ```

⚠️ **The fallback is a different board.** If the homelab dies mid-pitch, the next
scans land on the cloud copy while the presenter's wall still shows the homelab's
cabin. That is continuity of *service*, not of *state* — both sides start from the
same seeded image, and a failover means "the demo keeps working, the count
restarts". Prefer the homelab-first ordering (as configured) so normal operation
never mixes the two.

Rollback is two record edits: `sophon1.dedyn.io` → the home IP, and the updater
back onto it. Nothing is destroyed.

**Verified locally** (both origins faked, front door real, `caddy validate` on the
shipped file): with both up, the homelab answers; stopped, the *same*
`/bggo/<CODE>` is served by the cloud copy; restarted, the homelab takes over again
— with no front-door restart in between.

### Cheaper than all of the above

The `<CODE>` in a URL is host-independent. If the homelab dies on the day, a slide
or a phone showing a QR for `https://<cloud url>/bggo/<CODE>` is as good for a
*new* scan as the printed pass is. Nothing to build.

## Which path

| | **VM** (recommended for a lasting parallel host) | **Cloud Run** (quickest URL) |
|---|---|---|
| URL | `http://<static IP>/bggo/live`, or a new subdomain with real TLS | `https://baggagego-xxxx.run.app/bggo/live` — HTTPS immediately, zero DNS |
| board state | on the VM's disk: survives redeploys and reboots | in the instance: **lost on every new revision** |
| cost | `e2-micro` is in the Always Free tier in `us-west1`/`us-central1`/`us-east1` | `min-instances=1` is instance-based billing — tens of $/month |
| setup | `deploy-vm.sh` | `deploy-cloud-run.sh` |

Both run the exact same image as `docker compose up` on a laptop.

## Path A — a persistent second host on a VM

Prereqs: [`gcloud` CLI](https://cloud.google.com/sdk/docs/install), `gcloud auth
login`, a billing account on the project. No local Docker: the image is built by
Cloud Build from the repo root.

```bash
cd ~/Hackathon/pitch-demo   # the repo root
PROJECT=your-project-id REGION=us-central1 ./deploy/gcp/deploy-vm.sh
```

The script: enables the APIs → creates the Artifact Registry repo → builds
`pitch-demo/` in Cloud Build → reserves a static IP → firewall for 80/443 → creates
the VM (first boot installs Docker) → copies `compose.yaml`, `Caddyfile` and a
`.env` to `/opt/baggagego` → `docker compose up -d`.

By default `SITE_ADDRESS` is the VM's own address over plain HTTP, because no DNS
points at it yet:

```bash
IP=$(gcloud compute addresses describe bggo-ip --region=us-central1 --format='value(address)')
curl -sI "http://$IP/bggo/live" | head -1                                    # 200
curl -s  "http://$IP/bggo/api/state" | python3 -c "import json,sys;print(json.load(sys.stdin)['counters'])"
```

That is already a working second host. Everything below is optional polish.

### Optional: a nicer URL, on a name that is NOT the frozen one

Add a **new** record at deSEC — a subdomain, either on the same zone or a new one:

```
bggo.sophon1.dedyn.io.   A   300   <static IP>
```

…then re-run with that name, and Caddy will obtain a real Let's Encrypt cert for it
(HTTP-01 needs the name to resolve here, which is why it must be a new record and
not `sophon1.dedyn.io`):

```bash
PROJECT=your-project-id REGION=us-central1 SITE_ADDRESS=bggo.sophon1.dedyn.io ./deploy/gcp/deploy-vm.sh
curl -sI https://bggo.sophon1.dedyn.io/bggo/live | head -1
```

The script **refuses** `SITE_ADDRESS=sophon1.dedyn.io` on purpose: that name belongs
to the printed passes, and a cert cannot be issued for it while DNS points at the
homelab. `ALLOW_FROZEN=1` overrides the refusal if you genuinely mean to move it —
which is a different project, not this one.

### Operating it

```bash
SSH="gcloud compute ssh bggo-demo --zone=us-central1-b --command"
$SSH 'cd /opt/baggagego && sudo docker compose ps'
$SSH 'cd /opt/baggagego && sudo docker compose logs --tail=50 caddy'
$SSH 'cd /opt/baggagego && sudo docker compose exec demo python server_v1.py --controls-url'
$SSH 'cd /opt/baggagego && sudo docker compose restart demo'    # the board survives
```

Ship a new build, in place, no reboot:

```bash
PROJECT=your-project-id REGION=us-central1 ./deploy/gcp/deploy-vm.sh
```

## Path B — a Cloud Run URL in ten minutes

```bash
cd ~/Hackathon/pitch-demo   # the repo root
PROJECT=your-project-id ./deploy/gcp/deploy-cloud-run.sh
```

It prints the service URL; `/bggo/live`, `/bggo/<CODE>` and the controls page all
work there immediately, over HTTPS, with no DNS at all.

`--min-instances=1 --max-instances=1` is deliberate: one warm instance, one SQLite
writer, consistent board. Even so **a redeploy resets the board** — export first if
it matters. Optional cosmetic step: give it a subdomain with
`gcloud run domain-mappings create --service baggagego --domain bggo.<your zone>
--region <region>` and add the records it prints at deSEC. Again: a new name, never
`sophon1.dedyn.io`.

## Moving the board's state (either path)

```bash
# snapshot whatever is currently live. On the homelab the live board is in the
# container's volume, so take it out of the running container (the repo's
# passengers.db is only the seed):
cd pitch-demo
docker compose exec -T demo python server_v1.py --export /tmp/board.json
docker compose cp demo:/tmp/board.json /tmp/board.json

# VM
gcloud compute scp /tmp/board.json bggo-demo:/tmp/board.json --zone=us-central1-b
gcloud compute ssh bggo-demo --zone=us-central1-b --command \
  'cd /opt/baggagego && sudo docker compose cp /tmp/board.json demo:/tmp/board.json && sudo docker compose exec -T demo python server_v1.py --import /tmp/board.json'

# Cloud Run: use the controls page's Snapshot/Import block (needs the control token)
```

The image is built from the repo's `passengers.db`, so a fresh deploy already
carries whatever scans were in the tree at build time. `--export`/`--import` is for
state that moved on afterwards.

## Costs and egress — the one number to know

`/bggo/api/state` is **112 KB** and the board polls it **once a second**: ~112 KB/s
per open board, ~400 MB per hour of presenting, **~9.6 GB/day if you leave the wall
open overnight**. A pitch is comfortably inside the free 1 GB/month, but close the
board when you are not presenting — especially on a free-tier US region, where
egress past 1 GB is billed.

## Local dry run of the TLS front

The Caddy layer is testable with no domain and no cloud account:

```bash
cd pitch-demo
IMAGE=baggagego:local SITE_ADDRESS=http://localhost \
  CADDY_HTTP_PORT=8088 CADDY_HTTPS_PORT=8444 \
  docker compose -f deploy/gcp/compose.yaml up -d
curl -sI http://localhost:8088/bggo/live | head -1               # 200 through the proxy
curl -sI http://localhost:8088/ | head -2                        # 302 -> /bggo/live
IMAGE=baggagego:local SITE_ADDRESS=http://localhost \
  CADDY_HTTP_PORT=8088 CADDY_HTTPS_PORT=8444 \
  docker compose -f deploy/gcp/compose.yaml down -v
```

`CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT` exist because a laptop often already has 80/443
taken (the homelab's NPM is host-network).

## The cutover runbook

Moving the live demo from the homelab to the VM — including the DNS repoint, the
state migration, verification from a phone, backups and the rollback — is
[`CUTOVER.md`](CUTOVER.md). Read it before touching deSEC.

Two scripts do the day-to-day work there:

| | |
|---|---|
| `./deploy/gcp/deploy.sh` | build the working tree in Cloud Build, ship it to the VM, restart, smoke-check the public URL, and print the exact rollback line |
| `./deploy/gcp/backup.sh` | snapshot the board's state off the VM (`--install-cron` for a daily one — the data volume is the only thing that cannot be rebuilt from the repo) |
| `./deploy/gcp/sync.sh` | copy the live board onto a *second* instance so both show the same cabin (`--install-cron`, `--dry-run`). One-way, source → mirror, and settings-free, so the mirror's own tokens survive |

`deploy.sh` builds from the **working tree**, not from Git, so an agent's
uncommitted change ships without a commit in between. `--no-build` ships an
existing image tag, which is how a rollback is one command.

## The frozen-link rules this kit obeys

1. `sophon1.dedyn.io` keeps resolving to the homelab, and `baggagego.service` keeps
   running there. **Do not** point that A record here, and do not disable the unit.
2. Something re-asserts that A record today (`59.188.165.173`, the homelab's public
   IP, with no deSEC token on the box — almost certainly the router's DDNS client).
   Leave it alone; it is now a feature, not a hazard.
3. The QR payload is frozen in `boarding-pass/make_qr_v1.py:BASE_URL`. Nothing in
   `deploy/gcp/` reads or writes it.
4. The Cloud host gets its **own** name (or none). Never a second host answering for
   the frozen one — two machines serving the same hostname is how a live demo dies
   half-read.

## Why not Cloud Run + Cloud Storage for the database

Because SQLite does not belong on `gcsfuse`: WAL needs shared memory the FUSE mount
does not provide, and there are no POSIX locks, so two writers can corrupt the file.
A single writer (`max-instances=1`) makes it *survive*, not *safe*.
