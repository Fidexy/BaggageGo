#!/usr/bin/env bash
# Keep a second instance showing the same board as the one you are running.
#
#   SOURCE_URL=http://127.0.0.1:8091 MIRROR_URL=https://you.example ./deploy/gcp/sync.sh
#   … ./deploy/gcp/sync.sh --dry-run          # show the delta, send nothing
#   … ./deploy/gcp/sync.sh --install-cron     # every 5 minutes
#
# Why this exists: the demo can be hosted twice — the one the audience's printed
# passes feed, and a Cloud copy a judge opens. They are separate databases, so
# without this they show different cabins. This copies the live board across.
#
# It uses the export/import pair that already ships, which is deliberately
# **settings-free**: the snapshot carries passengers and scans and nothing else,
# so the mirror's own board and control tokens are never touched, and a sync
# cannot lock you out of the instance it wrote to.
#
# ONE-WAY, SOURCE -> MIRROR. The mirror's own scans are replaced by the source's,
# so point it at a copy nobody is meant to interact with.
#
# Tokens come from the environment or from ~/.bggo-sync.env (chmod 600), which is
# what the cron entry reads — a token should not sit in a crontab line:
#
#   SOURCE_TOKEN=…        # source: python server_v1.py --controls-token
#   MIRROR_TOKEN=…        # mirror: python server_v1.py --controls-token
set -euo pipefail

SOURCE_URL="${SOURCE_URL:-http://127.0.0.1:8091}"
MIRROR_URL="${MIRROR_URL:-}"
ENV_FILE="${ENV_FILE:-$HOME/.bggo-sync.env}"
EVERY="${EVERY:-*/5 * * * *}"

if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
fi

DRY=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --install-cron)
            : "${MIRROR_URL:?set MIRROR_URL first}"
            LINE="${EVERY} SOURCE_URL=${SOURCE_URL} MIRROR_URL=${MIRROR_URL} ${BASH_SOURCE[0]//$HOME/\$HOME} >> \$HOME/.bggo-sync.log 2>&1"
            { crontab -l 2>/dev/null | grep -v 'deploy/gcp/sync.sh' || true; echo "$LINE"; } | crontab -
            echo "installed: ${LINE}"
            echo "tokens are read from ${ENV_FILE} (must exist and be chmod 600)"
            exit 0 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

: "${MIRROR_URL:?set MIRROR_URL=<the instance that should mirror the board>}"
: "${SOURCE_TOKEN:?set SOURCE_TOKEN — on the source: python server_v1.py --controls-token}"
: "${MIRROR_TOKEN:?set MIRROR_TOKEN — on the mirror: python server_v1.py --controls-token}"

counters() {
    curl -fsS --max-time 15 "$1/bggo/api/state" |
        python3 -c "import json,sys; d=json.load(sys.stdin)['counters']; print(d['scanned'], d['hits'])"
}

echo "source : ${SOURCE_URL}  (scanned/hits = $(counters "$SOURCE_URL"))"
echo "mirror : ${MIRROR_URL}  (scanned/hits = $(counters "$MIRROR_URL"))"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

curl -fsS --max-time 30 -H "X-Control-Token: ${SOURCE_TOKEN}" \
    "${SOURCE_URL}/bggo/api/ctl/export" -o "$TMP"
python3 - "$TMP" <<'PY'
import json, sys
snapshot = json.load(open(sys.argv[1]))
counts = snapshot["counts"]
print(f"snapshot: {counts['passengers']} passengers, {counts['scans']} scans, "
      f"format {snapshot['format']} v{snapshot['version']}")
PY

if [ "$DRY" = 1 ]; then
    echo "--dry-run: nothing was sent to the mirror"
    exit 0
fi

curl -fsS --max-time 30 -X POST \
    -H "X-Control-Token: ${MIRROR_TOKEN}" \
    -H 'content-type: application/json' \
    --data-binary "@${TMP}" \
    "${MIRROR_URL}/bggo/api/ctl/import" > /dev/null

AFTER="$(counters "$MIRROR_URL")"
SOURCE_NOW="$(counters "$SOURCE_URL")"
echo "mirror now: scanned/hits = ${AFTER}"

if [ "$AFTER" = "$SOURCE_NOW" ]; then
    echo "in sync"
else
    echo "NOT in sync: mirror ${AFTER} vs source ${SOURCE_NOW}" >&2
    exit 1
fi
