#!/usr/bin/env bash
# The quick path: one managed container, no VM, no TLS work — Cloud Run gives you
# an https URL immediately and can map the frozen hostname itself.
#
#   PROJECT=my-proj ./deploy/gcp/deploy-cloud-run.sh
#
# READ THE CAVEAT, it is the whole reason the VM path exists: the board's state
# is a SQLite file, and on Cloud Run it lives in the instance's filesystem. With
# --max-instances=1 there is exactly one writer, and with --min-instances=1 there
# is no cold start, but the file is still lost whenever the revision changes
# (a new deploy) and can be lost if the instance is recycled. Snapshot the board
# with /bggo/api/ctl/export before a redeploy, or accept a refreshed empty cabin.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT=<your project id>}"
REGION="${REGION:-asia-east1}"
SERVICE="${SERVICE:-baggagego}"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"
AR_REPO="${AR_REPO:-bggo}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/demo:${TAG}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${HERE}/../.." && pwd)"

say() { printf '\n== %s ==\n' "$*"; }

gcloud config set project "$PROJECT" >/dev/null

say "1/4 enabling APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com

say "2/4 Artifact Registry"
gcloud artifacts repositories describe "$AR_REPO" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$AR_REPO" --repository-format=docker --location="$REGION" \
    --description="BaggageGO demo images"

say "3/4 building the image in Cloud Build (context: pitch-demo/)"
gcloud builds submit --tag "$IMAGE" "$APP_DIR"

say "4/4 deploying Cloud Run service $SERVICE"
# --port 8080: Cloud Run injects PORT=8080 and the entrypoint prefers it.
# min=max=1: keep one warm instance and one SQLite writer.
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 --memory 512Mi \
  --min-instances 1 --max-instances 1 \
  --execution-environment gen2

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
cat <<EOF

== live on Cloud Run ==
board   : ${URL}/bggo/live
cards   : ${URL}/bggo/<CODE>
console : ${URL}/bggo/controls?k=<token>   (token: cd pitch-demo && .venv/bin/python server_v1.py --controls-token)

note    : this is a SECOND host with its OWN board state — the homelab keeps
          https://sophon1.dedyn.io/bggo/live for the printed passes, untouched.
          A redeploy here resets this board, so export it first if it matters.

optional: a nicer name, without touching the frozen one —
    gcloud run domain-mappings create --service ${SERVICE} --domain bggo.<your zone> --region ${REGION}
  then add the records it prints at deSEC. A NEW name, never sophon1.dedyn.io:
  that one belongs to the homelab and the boarding passes on paper.
EOF
