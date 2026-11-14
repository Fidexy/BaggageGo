# Cutover: homelab → Google Cloud, without reprinting anything

The printed QRs encode `https://sophon1.dedyn.io/bggo/<CODE>`. That string is
frozen; the **A record** for that name is not. This runbook moves the name onto the
Cloud VM so the demo survives the homelab going away — the code, the pass and the
payload never change.

Read `README.md` first for the model and the caveats. This file is just the order
of operations and the checks.

## What moved, and what does not

| | before | after |
|---|---|---|
| serving the frozen name | homelab NPM → `127.0.0.1:8091` | Caddy on the VM (own Let's Encrypt cert) |
| board state | homelab `passengers.db` | the VM's `bggo-data` volume — **the copy made in step 2** |
| DNS | deSEC `sophon1` → home IP, kept there by a DDNS client | `sophon1` → static GCP IP, nothing updating it |
| dev / agent work | homelab (unchanged) | homelab (unchanged) |

After the cutover the homelab's board is a **frozen snapshot**: it keeps working on
the LAN but nobody's scans reach it. That is the point — one canonical board.

## Preflight

- [ ] `gcloud auth login` works and the project has a billing account.
- [ ] You know **what updates the `sophon1` A record today** (almost certainly the
      router's DDNS client). You will repoint it at `home.sophon1.dedyn.io` in
      step 4 — if you skip that, it will drag the name back to the home IP.
- [ ] The board is quiet (no one is scanning). Any scan after step 2 lives only on
      the homelab.
- [ ] Lower the `sophon1` A record's TTL to `300` at deSEC, so a rollback is
      minutes rather than an hour.

## 1 — Stand up the Cloud host (name not yet involved)

```bash
cd ~/Hackathon/pitch-demo
PROJECT=… REGION=us-central1 ./deploy/gcp/deploy-vm.sh          # IP, firewall, VM, stack
```

Check it — this is a working second host already, and the frozen name is untouched:

```bash
IP=$(gcloud compute addresses describe bggo-ip --region=us-central1 --format='value(address)')
curl -sI "http://$IP/bggo/live" | head -1                       # 200
```

## 2 — Move the board's state across (once)

```bash
# on the homelab. The live state is in the CONTAINER's volume, not in the repo's
# passengers.db — that file stopped being the live data when the demo moved into
# Docker (2026-11-11) and is now only the seed the image carries.
cd ~/Hackathon/pitch-demo
docker compose exec -T demo python server_v1.py --export /tmp/board.json   # inside the container
docker compose cp demo:/tmp/board.json /tmp/board.json                     # bring it out
docker compose stop demo      # after the copy: the two boards can no longer diverge

# onto the VM
gcloud compute scp /tmp/board.json bggo-demo:/tmp/board.json --zone=us-central1-b
gcloud compute ssh bggo-demo --zone=us-central1-b --command \
  'cd /opt/baggagego && sudo docker compose cp /tmp/board.json demo:/tmp/board.json && sudo docker compose exec -T demo python server_v1.py --import /tmp/board.json'
```

Confirm the counts match on both sides (`/bggo/api/state` → `counters`), and leave
the homelab's stack stopped — from here the VM's board is the real one.

## 3 — Tell Caddy to answer for the frozen name (still nothing outward changes)

```bash
PROJECT=… REGION=us-central1 SITE_ADDRESS=sophon1.dedyn.io \
  ./deploy/gcp/deploy.sh --no-build
```

Caddy starts serving that name and begins ACME. It **cannot** get a certificate yet
— DNS still points home — and it retries on its own. Nothing outward changes: the
name still resolves to the homelab, which is still serving.

## 4 — Repoint DNS

1. On the router (or wherever the DDNS client lives): change the record it updates
   from `sophon1.dedyn.io` to **`home.sophon1.dedyn.io`**. Leave it running — that
   name is how you reach the homelab, and how the front-door/failover model would
   find it.
2. At deSEC: `sophon1.dedyn.io A 300 <static IP>`.

## 5 — Verify from outside

```bash
curl -sI https://sophon1.dedyn.io/bggo/live | head -1
echo | openssl s_client -connect sophon1.dedyn.io:443 -servername sophon1.dedyn.io 2>/dev/null \
  | openssl x509 -noout -issuer -dates          # Let's Encrypt, issued just now
curl -s https://sophon1.dedyn.io/bggo/api/state | python3 -c \
  "import json,sys;print(json.load(sys.stdin)['counters'])"
```

Then the real test: **a printed pass, scanned by a phone on mobile data** (not
wifi, which may still resolve to the LAN). The card must open and the row must
light up on the board. That is the whole acceptance criterion.

If the certificate has not been issued yet, Caddy is still retrying — check
`sudo docker compose logs caddy` on the VM; a failed HTTP-01 usually means port 80
is not reaching the VM (the firewall rule allows it; the name has to resolve there).

## 6 — Backups and the update loop

```bash
# on the VM: daily snapshot of the only irreplaceable thing
gcloud compute ssh bggo-demo --zone=us-central1-b --command 'cd /opt/baggagego && ./backup.sh --install-cron'
```

Every later update, from the working tree, one line:

```bash
PROJECT=… ./deploy/gcp/deploy.sh                     # build + ship + smoke + rollback hint
PROJECT=… TAG=<previous> ./deploy/gcp/deploy.sh --no-build   # undo a bad ship
```

## Rollback

1. deSEC: `sophon1.dedyn.io A <home IP>`, and point the DDNS client back at
   `sophon1.dedyn.io`.
2. `docker compose start demo` on the homelab.
3. Optionally `docker compose down` on the VM.

⚠️ Rollback is **not** lossless for state: any scan that happened on the Cloud
board since step 2 exists only there. If you need it, `backup.sh` or
`--export` from the VM and `--import` it back into the homelab.
