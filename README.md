# BaggageGO

Lightweight passenger boarding demo for Cathay Hackathon 2026. Audience members open a
boarding-pass link, choose their bags, and the live board updates for the pitch demo. It
runs as a FastAPI application backed by SQLite and is packaged as a single Docker
container.

## Demo

The hosted base URL is <!--repo-base-->https://sophon1.dedyn.io<!--/repo-base-->.
Append `/bggo/live` for the live board or `/bggo/<CODE>` for a passenger card. Sample
ticket links are listed in [`tickets.md`](tickets.md). For the pitch demo, boarding passes with ticket links are printed.
The server is hosted on a homelab environment.

## Run with Docker

Docker Compose builds and starts the app on `http://localhost:8091`:

```bash
docker compose up --build
```

Open the board at [http://localhost:8091/bggo/live](http://localhost:8091/bggo/live).
Passenger cards use `http://localhost:8091/bggo/<CODE>`; use a code from `tickets.md`.

The Compose port is bound to `127.0.0.1` by default. To choose another host port:

```bash
BGGO_HOST_PORT=8095 docker compose up --build
```

The live board and operator console are separate: the board is open, while the console
requires its token. Print the console bookmark from the running Compose project with:

```bash
docker compose run --rm demo python server_v1.py --controls-url
```

Treat the token in that URL as a secret. Do not share or commit it.

### Data persistence

Compose stores live scans, bag choices, and settings in the `bggo-data` named volume.
They survive container restarts and recreation. `passengers.db` in the repository is a
seed copied into that volume on first start; it is not the live database after startup.

To stop the app without removing its data, press `Ctrl+C` or run `docker compose down`.
`docker compose down -v` also deletes the volume and its live data.

## Run locally

Python 3.13 or newer is recommended (the Docker image uses Python 3.13).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn server_v1:app --host 127.0.0.1 --port 8091
```

Then visit [http://localhost:8091/bggo/live](http://localhost:8091/bggo/live). The
application defaults to `passengers.db` in the project directory when run directly;
set `BGGO_DB` to use a different SQLite file.

## Tests

Run the test suite in the same container environment used by the demo:

```bash
docker compose run --rm demo pytest -q
```

Or, after installing the requirements locally:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python test_brand_vendored.py
.venv/bin/python test_make_ticket_links.py
```

## Configuration

- `BGGO_DB` selects the SQLite database file (default: `passengers.db` locally; the
  Compose container uses `/data/passengers.db`).
- `PORT` overrides the listening port when supplied by a hosting platform; otherwise
  the container uses `BGGO_PORT` (default: `8091`).
- `BGGO_BRAND_DIR` selects the vendored brand asset directory (default: `brand/`).
- `BGGO_PUBLIC_BASE` changes the base URL used for generated ticket links. It does not
  change the URL encoded in already-printed QR codes.

## Project layout

- `server_v1.py` — FastAPI application and SQLite-backed board.
- `seed_passengers.py`, `passengers.db`, `passenger_codes.txt` — passenger seed data.
- `make_ticket_links.py`, `tickets.md` — generate and list passenger links.
- `static/`, `brand/` — app assets and vendored brand kit.
- `docker/`, `Dockerfile`, `docker-compose.yml` — container startup and local deployment.
- `test_*.py` — automated tests.
- `deploy/` — host and cloud deployment notes and scripts.

For host operations, see [`deploy/HOMELAB.md`](deploy/HOMELAB.md). For Google Cloud
options and their persistence trade-offs, see [`deploy/gcp/README.md`](deploy/gcp/README.md).
