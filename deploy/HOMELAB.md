# Running the demo on our own host

Operational notes for the machine the demo actually lives on. A judge does not need
any of this — it is here so the facts survive a reboot, a season, or a new person.
The generic instructions are in the top-level [`README.md`](../README.md); the Cloud
runbook is [`deploy/gcp/CUTOVER.md`](gcp/CUTOVER.md).

## What runs where

| | |
|---|---|
| container | `pitch-demo-demo-1` (image `baggagego:local`), `restart: unless-stopped` |
| listens on | `127.0.0.1:8091` — loopback only, never on the LAN or WAN |
| the live board | the volume `pitch-demo_bggo-data`, mounted at `/data` |
| the repo's `passengers.db` | **only the seed.** It stopped being the live data on 2026-11-11, when the demo moved from systemd into the container |
| public URL | https://sophon1.dedyn.io/bggo/live |
| ar-demo (bag check) | **systemd, not docker**: unit `ar-demo` on `127.0.0.1:8092` — https://sophon1.dedyn.io/bggo/ar/ |

```bash
cd ~/Hackathon/pitch-demo
docker compose ps                      # should be healthy
docker compose up -d --build           # rebuild and reload (the volume survives)
docker compose logs -f demo            # uvicorn's own lines
docker compose restart demo            # restart without rebuilding
```

## The public route

nginx-proxy-manager (host-networking container) holds proxy host **1** =
`sophon1.dedyn.io`, with a custom location `/bggo` → `http` → `127.0.0.1` → `8091`
and an empty forward path, so the URI survives the hop and the app still sees
`/bggo/...`. Result: `/data/nginx/proxy_host/1.conf`. TLS is NPM cert `npm-1`
(Let's Encrypt, valid to **2027-01-22**); `ssl_forced` is 0 on that host, so
`https://` works and plain `http://` is not redirected — the QR uses `https://`
explicitly.

`web-site-1` (the static site on `:8090`) is exited and unrelated. Do **not** route
the demo through it: coupling them means the demo dies with the marketing site.

## DNS, and the trap in it

`sophon1.dedyn.io` is a deSEC zone whose A record points at this host's public IP. It
is **not** updated from this machine — there is no deSEC token on the box — so
whatever maintains it is elsewhere, almost certainly the router's DDNS client.

Consequences worth remembering:

- **Do not** point that record at anything else casually. It is the hostname inside
  every printed QR; the payload is frozen, but the record is editable, so moving it
  is a one-line change that either fixes or breaks the paper.
- If a Cloud copy ever needs that hostname, the DDNS client has to stop claiming it
  first (give it `home.sophon1.dedyn.io` instead), or it will drag the record back
  within minutes. See `deploy/gcp/CUTOVER.md`.
- Rollback from any such move: point the record back here.

## The live board's state

Everything irreplaceable is in the volume. To move or back it up:

```bash
cd ~/Hackathon/pitch-demo
docker compose exec -T demo python server_v1.py --export /tmp/board.json   # inside the container
docker compose cp demo:/tmp/board.json ~/board-$(date +%Y%m%d).json        # and out
# restore:
docker compose cp ~/board-20261111.json demo:/tmp/board.json
docker compose exec -T demo python server_v1.py --import /tmp/board.json
```

The snapshot carries passengers and scans and deliberately **not** `settings`, so
importing never rotates the board or control tokens.

## The systemd unit is the rollback path

`deploy/baggagego.service` is stopped **and disabled**, so it cannot take `8091`
back on a reboot. It is kept because it is a working way to run the app if the
container ever becomes the problem:

```bash
docker compose down                     # only one of them can own 8091
sudo systemctl enable --now baggagego   # the venv has to exist and be up to date
```

Its `ExecStart` points at `.venv/bin/uvicorn` inside this directory, and a moved or
recreated venv breaks it (the shebangs embed an absolute path).

## Operator bits

```bash
docker compose exec -T demo python server_v1.py --controls-url   # the ?k= bookmark
docker compose exec -T demo python server_v1.py --token          # the Reset header
curl -s http://127.0.0.1:8091/bggo/api/state | head -c 200       # is it alive
docker compose exec -T demo python seed_passengers.py --check    # db invariants, safe while live
```

## Publishing to the submitted repo

There is one tree, and it is the repo. `~/Hackathon/pitch-demo` is both the tree this
container builds from and the submitted repo
(`git@github.com:Fidexy/BaggageGo-A.git`, branch `main`), so publishing is a commit and
a push (Davis, 2026-11-15):

```bash
cd ~/Hackathon/pitch-demo
git add <explicit paths>     # two pi sessions commit here: never `git add -A`
git commit -m "…" && git push origin main
```

The older model — a derived `~/BaggageGo` kept byte-identical by `deploy/publish.sh` —
is gone: the script is deleted and `~/BaggageGo` is a dead copy to ignore.

The one idea worth keeping from it is the **link base**. `tickets.md` is generated, and
`BGGO_PUBLIC_BASE` renders it against another host (a Cloud copy, say) without touching
the codes; the printed QRs stay frozen on `sophon1.dedyn.io` no matter what the list
says. `README.md` names the host once, inside `<!--repo-base-->…<!--/repo-base-->`.

## Pitch-day checklist

1. `docker compose ps` → healthy; `curl -s https://sophon1.dedyn.io/bggo/live | head -c 40`
   from a phone **on mobile data**, not wifi (wifi may resolve to the LAN and prove
   nothing about the path the audience will take).
2. Scan a printed pass for real with the phone's camera app: the card opens, choosing
   bags lights the row on the board.
3. Check the operator console loads with `?k=` (and that you have the bookmark).
4. Print last (D9). The passes encode a frozen hostname; anything printed after a DNS
   change you did not mean is wasted paper.
5. If a rehearsal filled the board, clear it from the console (**Clear all scans and
   bags**) — passengers, codes and seats are never touched by it.

## ar-demo — the bag check (added 2026-11-12)

A separate, standalone prop: recognises one enrolled bag with OpenCV in the browser
and draws a frame on it. Rundown in `~/Hackathon/ar-demo/README.md`. It is **not**
part of the demo container and must not be coupled to it — if `pitch-demo` is down,
the bag check still works, and vice versa.

| | |
|---|---|
| runs as | `ar-demo.service` (systemd, `Restart=unless-stopped`, enabled) |
| command | `python3 ~/Hackathon/ar-demo/server.py --host 127.0.0.1 --port 8092 --base /bggo/ar` |
| public URL | https://sophon1.dedyn.io/bggo/ar/ |
| the model | `~/Hackathon/ar-demo/static/references.json` — drop it in, **no restart needed** (served `no-store`) |

```bash
systemctl status ar-demo
journalctl -u ar-demo -n 20 --no-pager
sudo systemctl restart ar-demo        # after editing server.py
```

`--base /bggo/ar` is what lets nginx pass the URI through untouched; the app strips
the prefix itself, so the URL must keep its trailing slash (`/bggo/ar` 301s to it).
HTTPS is not decoration here — `getUserMedia` needs a secure context, which is exactly
why the phone can use the public hostname with no TLS flags.

### #gotcha the route is NOT in the NPM UI

nginx-proxy-manager (2.15.1, container `npm`) will not accept this route from either a
second **Custom Location** or the **Advanced** tab. Verified: after editing
`proxy_host.locations` / `advanced_config` in `/opt/stacks/npm/data/database.sqlite`
**and restarting the container**, `/data/nginx/proxy_host/1.conf` was regenerated with
the old content — even though the container's own `better-sqlite3` read back the new
values. Editing the generated `1.conf` directly is not durable either.

So the location lives in NPM's documented global include:

    /opt/stacks/npm/data/nginx/custom/server_proxy.conf

which the generated configs already pull in via
`include /data/nginx/custom/server_proxy[.]conf;`. That file is included in **every**
proxy host's server block, so the location is guarded with
`if ($host != "sophon1.dedyn.io") { return 404; }` — confirmed: `comfyui.sophon1.dedyn.io/bggo/ar/`
returns 404 while the real host returns 200. Reload after editing:

```bash
docker exec npm nginx -t && docker exec npm nginx -s reload
```
