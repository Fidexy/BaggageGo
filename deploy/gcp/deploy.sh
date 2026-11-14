#!/usr/bin/env bash
# Ship a new build to the demo VM. One command, and the same command rolls back.
#
#   PROJECT=… ./deploy/gcp/deploy.sh                          # build, push, roll out
#   PROJECT=… ./deploy/gcp/deploy.sh --no-build               # ship TAG as-is
#   TAG=20261111-101500 PROJECT=… ./deploy/gcp/deploy.sh --no-build   # roll back
#
# First-time infrastructure (static IP, firewall, VM, first stack) is
# deploy-vm.sh's job. This one only builds and ships, so the whole cutover and
# every later update is a single line.
#
# It builds from the WORKING TREE via Cloud Build — no Git, no commit needed — so
# what runs on Cloud is exactly what is on disk right now.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT=<your project id>}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-${REGION}-b}"
VM="${VM:-bggo-demo}"
AR_REPO="${AR_REPO:-bggo}"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"
REMOTE_DIR="${REMOTE_DIR:-/opt/baggagego}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/demo:${TAG}"
SITE_ADDRESS="${SITE_ADDRESS:-}"     # optional override for the VM's .env

BUILD=1
for arg in "$@"; do
    case "$arg" in
        --no-build) BUILD=0 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${HERE}/../.." && pwd)"

say() { printf '\n== %s ==\n' "$*"; }
ssh_vm() { gcloud compute ssh "$VM" --zone="$ZONE" --command="$1"; }

gcloud config set project "$PROJECT" >/dev/null

if [ "$BUILD" = "1" ]; then
    say "building ${TAG} in Cloud Build (context: the working tree)"
    gcloud builds submit --tag "$IMAGE" "$APP_DIR"
else
    say "shipping the existing image ${TAG} (no build)"
fi

say "reading the VM's current state"
PREV="$(ssh_vm "grep -h '^IMAGE=' ${REMOTE_DIR}/.env 2>/dev/null || true" | cut -d= -f2- || true)"
if [ -z "$PREV" ]; then
    echo "no ${REMOTE_DIR}/.env on ${VM} — run deploy/gcp/deploy-vm.sh first" >&2
    exit 3
fi
echo "  was: ${PREV}"
echo "  now: ${IMAGE}"

say "updating ${REMOTE_DIR}/.env on the VM"
ssh_vm "cd ${REMOTE_DIR} && sed -i '/^IMAGE=/d' .env && printf 'IMAGE=%s\n' '${IMAGE}' >> .env"
if [ -n "$SITE_ADDRESS" ]; then
    ssh_vm "cd ${REMOTE_DIR} && sed -i '/^SITE_ADDRESS=/d' .env && printf 'SITE_ADDRESS=%s\n' '${SITE_ADDRESS}' >> .env"
fi

say "pulling and restarting"
ssh_vm "cd ${REMOTE_DIR} && sudo docker compose pull --quiet && sudo docker compose up -d"

SITE="$(ssh_vm "grep -h '^SITE_ADDRESS=' ${REMOTE_DIR}/.env 2>/dev/null || true" | cut -d= -f2- | tr -d '[:space:]' || true)"
case "$SITE" in
    http://*|https://*) URL="$SITE" ;;
    *.*)                URL="https://$SITE" ;;
    *)                  URL="" ;;
esac

# A failed probe after a site change is expected — ACME cannot complete until the
# name resolves here (CUTOVER.md steps 3-5). A failed probe with no change is a
# regression, and the ship is reported as failed.
REGRESSION=4
if [ -n "$SITE_ADDRESS" ]; then
    REGRESSION=0
    say "SITE_ADDRESS was just set to ${SITE_ADDRESS} — expect TLS to be pending until DNS points here"
fi

if [ -z "$URL" ]; then
    say "no usable SITE_ADDRESS in .env — skipping the smoke check"
    CODE="skipped"
else
    say "smoke check: ${URL}/bggo/live"
    CODE=""
    for _ in $(seq 1 20); do
        CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${URL}/bggo/live" || true)"
        [ "$CODE" = "200" ] && break
        sleep 3
    done
    echo "  HTTP ${CODE:-none}"
fi

if [ "$CODE" != "200" ] && [ "$CODE" != "skipped" ]; then
    echo "  not serving yet. Caddy's own view:" >&2
    ssh_vm "cd ${REMOTE_DIR} && sudo docker compose logs --tail=30 caddy" >&2 || true
    if [ "$REGRESSION" != "0" ]; then
        exit "$REGRESSION"
    fi
    echo "  (continuing: the site address just changed, see CUTOVER.md)" >&2
fi

cat <<EOF

== shipped ==
image     : ${IMAGE}
previous  : ${PREV}
board     : ${URL:-<set SITE_ADDRESS in .env>}/bggo/live
one card  : ${URL:-<set SITE_ADDRESS in .env>}/bggo/<CODE>
board now : curl -s ${URL:-<url>}/bggo/api/state | python3 -c "import json,sys;print(json.load(sys.stdin)['counters'])"

roll back : PROJECT=${PROJECT} REGION=${REGION} TAG=${PREV##*:} ./deploy/gcp/deploy.sh --no-build
logs      : gcloud compute ssh ${VM} --zone ${ZONE} --command 'cd ${REMOTE_DIR} && sudo docker compose logs -f caddy'
EOF
