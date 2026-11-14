#!/usr/bin/env bash
# Build the demo image with Cloud Build, then stand up one small VM that runs it
# behind Caddy with automatic TLS for the frozen hostname.
#
#   PROJECT=my-proj REGION=asia-east1 ./deploy/gcp/deploy-vm.sh
#
# Idempotent: re-run it to ship a new image — the IP, firewall rule and VM are
# reused, only the image tag changes. Nothing is built locally, so this works
# from any machine with gcloud authenticated.
#
# Prereqs: gcloud CLI, `gcloud auth login`, a billing account on the project.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT=<your project id>}"
REGION="${REGION:-asia-east1}"
ZONE="${ZONE:-${REGION}-b}"
VM="${VM:-bggo-demo}"
MACHINE="${MACHINE:-e2-micro}"
# Empty by default: resolved after the static IP is known to http://<ip>.
# The printed-QR hostname is deliberately NOT a default here — see the guard below.
SITE_ADDRESS="${SITE_ADDRESS:-}"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"
AR_REPO="${AR_REPO:-bggo}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/demo:${TAG}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${HERE}/../.." && pwd)"   # pitch-demo/

say() { printf '\n== %s ==\n' "$*"; }

gcloud config set project "$PROJECT" >/dev/null
say "project $PROJECT · region $REGION · image :$TAG"

say "1/7 enabling APIs"
gcloud services enable compute.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com

say "2/7 Artifact Registry"
gcloud artifacts repositories describe "$AR_REPO" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$AR_REPO" --repository-format=docker --location="$REGION" \
    --description="BaggageGO demo images"

say "3/7 building the image in Cloud Build (context: pitch-demo/)"
gcloud builds submit --tag "$IMAGE" "$APP_DIR"

say "4/7 static IP"
gcloud compute addresses describe bggo-ip --region="$REGION" >/dev/null 2>&1 || \
  gcloud compute addresses create bggo-ip --region="$REGION"
IP="$(gcloud compute addresses describe bggo-ip --region="$REGION" --format='value(address)')"
echo "static IP: $IP"

# Where Caddy answers. The default is this VM's own address over plain HTTP,
# because no DNS points here — and the frozen printed hostname belongs to the
# homelab, which keeps serving it.
SITE_ADDRESS="${SITE_ADDRESS:-http://$IP}"
case "${SITE_ADDRESS,,}" in
  sophon1.dedyn.io|http://sophon1.dedyn.io|https://sophon1.dedyn.io)
    if [ "${ALLOW_FROZEN:-0}" != "1" ]; then
      echo "refusing SITE_ADDRESS=$SITE_ADDRESS" >&2
      echo "  that is the hostname printed inside every QR, and it must keep resolving" >&2
      echo "  to the homelab; Caddy also cannot obtain a certificate for a name whose" >&2
      echo "  DNS points at another machine." >&2
      echo "  use the default (this VM's address), or a NEW subdomain such as" >&2
      echo "  bggo.sophon1.dedyn.io pointing here, or ALLOW_FROZEN=1 to override." >&2
      exit 2
    fi ;;
esac
echo "site address: $SITE_ADDRESS"

say "5/7 firewall: 80/443 only, tagged"
gcloud compute firewall-rules describe bggo-web >/dev/null 2>&1 || \
  gcloud compute firewall-rules create bggo-web \
    --allow=tcp:80,tcp:443 --target-tags=bggo-web \
    --description="BaggageGO demo — Caddy (HTTP-01 challenge + TLS)"

say "6/7 VM $VM ($MACHINE)"
if ! gcloud compute instances describe "$VM" --zone="$ZONE" >/dev/null 2>&1; then
  gcloud compute instances create "$VM" \
    --zone="$ZONE" \
    --machine-type="$MACHINE" \
    --image-family=debian-12 --image-project=debian-cloud \
    --boot-disk-size=20GB --boot-disk-type=pd-balanced \
    --tags=bggo-web --address="$IP" \
    --metadata-from-file=startup-script="${HERE}/startup-script.sh"
fi

say "waiting for Docker to appear on the VM (first boot does apt + install)"
for _ in $(seq 1 60); do
  if gcloud compute ssh "$VM" --zone="$ZONE" --command='command -v docker' >/dev/null 2>&1; then
    echo "docker present"
    break
  fi
  sleep 10
done

say "7/7 shipping the stack to /opt/baggagego"
gcloud compute ssh "$VM" --zone="$ZONE" --command='mkdir -p /opt/baggagego'
gcloud compute scp --zone="$ZONE" \
  "${HERE}/compose.yaml" "${HERE}/Caddyfile" "$VM:/opt/baggagego/"
printf 'IMAGE=%s\nSITE_ADDRESS=%s\n' "$IMAGE" "$SITE_ADDRESS" \
  | gcloud compute ssh "$VM" --zone="$ZONE" --command='cat > /opt/baggagego/.env'
gcloud compute ssh "$VM" --zone="$ZONE" \
  --command='cd /opt/baggagego && sudo docker compose up -d'

# A bare hostname serves https (Caddy's certificate); an http:// address is
# already a scheme.
SITE_URL="$SITE_ADDRESS"
case "$SITE_URL" in
  http://*|https://*) ;;
  *) SITE_URL="https://$SITE_URL" ;;
esac

cat <<EOF

== the demo is up on Google Cloud ==
board     : ${SITE_URL%/}/bggo/live
one card  : ${SITE_URL%/}/bggo/<CODE>      (codes in pitch-demo/tickets.md)
console   : gcloud compute ssh ${VM} --zone ${ZONE} --command 'cd /opt/baggagego && sudo docker compose exec demo python server_v1.py --controls-url'

== and the homelab is untouched ==
https://sophon1.dedyn.io/bggo/live still answers from your server, for the printed
passes. Nothing in this script wrote to NPM, DNS or the systemd unit — the two
hosts are independent, each with its own board state.

optional nicer URL: add a NEW record at deSEC, e.g.
    bggo.sophon1.dedyn.io. A <ttl> ${IP}
then re-run with SITE_ADDRESS=bggo.sophon1.dedyn.io to get real TLS for it.

logs      : gcloud compute ssh ${VM} --zone ${ZONE} --command 'cd /opt/baggagego && sudo docker compose logs -f caddy'
EOF
