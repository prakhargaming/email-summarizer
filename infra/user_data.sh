#!/bin/bash
# First boot only. Installs the runtime; the app itself is cloned and deployed by hand (see infra/README.md).
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y docker.io docker-compose-v2 docker-buildx git caddy
snap install aws-cli --classic
systemctl enable --now docker

# 0.5 GB of RAM is too little for `uv sync` during the image build.
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
