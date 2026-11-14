#!/usr/bin/env bash
# Snapshot the board's state off the demo VM.
#
# The image can be rebuilt from the repo at any time; the data volume cannot. That
# makes this the only irreplaceable thing on the VM, so it is worth a daily file.
#
#   ./backup.sh                  # run it on the VM (or via gcloud compute ssh)
#   ./backup.sh --install-cron   # daily 03:15, keeps the last 14 snapshots
#
# Restore:
#   sudo docker compose cp backups/board-<stamp>.json demo:/tmp/board.json
#   sudo docker compose exec -T demo python server_v1.py --import /tmp/board.json
set -euo pipefail

REMOTE_DIR="${REMOTE_DIR:-/opt/baggagego}"
KEEP="${KEEP:-14}"
DEST="${DEST:-${REMOTE_DIR}/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [ "${1:-}" = "--install-cron" ]; then
    mkdir -p "$DEST"
    # Replaces any previous entry, so this is safe to run twice.
    LINE="15 3 * * * cd ${REMOTE_DIR} && ./backup.sh >> ${DEST}/cron.log 2>&1"
    { crontab -l 2>/dev/null | grep -v 'backup.sh' || true; echo "$LINE"; } | crontab -
    echo "installed: ${LINE}"
    echo "check it with: crontab -l"
    exit 0
fi

cd "$REMOTE_DIR"
mkdir -p "$DEST"

sudo docker compose exec -T demo python server_v1.py --export "/tmp/board-${STAMP}.json" >/dev/null
sudo docker compose cp "demo:/tmp/board-${STAMP}.json" "${DEST}/board-${STAMP}.json" >/dev/null
sudo docker compose exec -T demo rm -f "/tmp/board-${STAMP}.json" >/dev/null || true

# Keep the newest $KEEP, drop the rest old-to-new.
ls -1t "${DEST}"/board-*.json 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

COUNT="$(ls -1 "${DEST}"/board-*.json 2>/dev/null | wc -l)"
echo "wrote ${DEST}/board-${STAMP}.json (${COUNT} snapshot(s) kept, limit ${KEEP})"
