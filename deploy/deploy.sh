#!/usr/bin/env bash
# Run on the box as root (sudo), from an SSM session. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only
docker compose -f deploy/compose.yml build
docker run --rm --env-file deploy/prod.env email-triage-api:latest alembic upgrade head
docker compose -f deploy/compose.yml up -d --remove-orphans

install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile
systemctl reload-or-restart caddy
install -m 644 deploy/systemd/email-triage-digest.service deploy/systemd/email-triage-digest.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now email-triage-digest.timer
docker image prune -f
