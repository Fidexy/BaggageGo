#!/bin/sh
# Container entrypoint for the BaggageGO passenger demo.
#
# Two jobs, in order:
#
#   1. Seed the data volume. `passengers.db` ships inside the image as the
#      *seed*, but the running app writes to it (scans, bag choices, settings
#      tokens), and a container filesystem is thrown away on every recreate.
#      So the writable copy lives on a volume at $BGGO_DB and is created from the
#      seed exactly once, on first start. WAL mode means the volume must stay
#      writable: SQLite writes -wal/-shm beside the db.
#   2. Hand off to the server. The demo must bind 0.0.0.0 in a container --
#      `python server_v1.py` hardcodes 127.0.0.1 and would be unreachable from
#      the host, so the CLI form is the one that runs here.
#
# Any arguments are exec'd instead of the server, which is how the test suite is
# run inside the image:  docker compose run --rm demo pytest -q
#
# The port resolves Cloud-Run-style: $PORT wins when the platform sets one (Cloud
# Run injects PORT=8080), otherwise $BGGO_PORT, otherwise 8091. The Dockerfile's
# healthcheck resolves the same way.
set -eu

if [ ! -f "${BGGO_DB}" ]; then
    cp /app/passengers.db "${BGGO_DB}"
    echo "entrypoint: seeded ${BGGO_DB} from the image (first start)"
fi

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

exec uvicorn server_v1:app \
    --host 0.0.0.0 \
    --port "${PORT:-${BGGO_PORT:-8091}}" \
    --log-level info
