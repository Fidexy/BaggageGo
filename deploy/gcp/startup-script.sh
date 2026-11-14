#!/bin/bash
# VM first boot: Docker + the compose plugin, from Docker's own apt repo.
# Deliberately knows nothing about the app — the stack is shipped by
# deploy-vm.sh, so a redeploy never needs the VM to reboot.
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y ca-certificates curl gnupg

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable --now docker

# The image is pulled by the VM's default service account, which needs to read
# Artifact Registry. The default compute service account has Editor, so this is
# a no-op — kept here so the failure mode is visible if the SA was narrowed.
gcloud --version >/dev/null 2>&1 || true

touch /var/lib/bggo-vm-ready
