#!/bin/bash
# First boot only. Installs the runtime; the app itself is cloned and deployed by hand (see infra/README.md).
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

curl -fsSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc -o /etc/apt/trusted.gpg.d/ngrok.asc
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" > /etc/apt/sources.list.d/ngrok.list
apt-get update
apt-get install -y docker.io docker-compose-v2 docker-buildx git ngrok python3
snap install aws-cli --classic
systemctl enable --now docker

# 2 GB of RAM is tight for `next build`.
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
