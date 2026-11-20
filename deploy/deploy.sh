#!/usr/bin/env bash
# Run on the box as root (sudo), from an SSM session. Safe to re-run.
set -euo pipefail
if [[ -n ${BASH_SOURCE[0]:-} ]]; then
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
fi

if (( $# > 1 )) || { (( $# == 1 )) && [[ ! $1 =~ ^[0-9a-f]{40}$ ]]; }; then
    echo "Usage: deploy.sh [full commit SHA]" >&2
    exit 2
fi

exec 9>.git/deploy.lock
flock -n 9 || { echo "Another backend deployment is running" >&2; exit 1; }

if [[ -n $(git status --porcelain) ]]; then
    echo "The deployment checkout must be clean" >&2
    exit 1
fi

git fetch origin refs/heads/main:refs/remotes/origin/main
commit=${1:-$(git rev-parse refs/remotes/origin/main)}
if ! git merge-base --is-ancestor "$commit" refs/remotes/origin/main ||
   ! git merge-base --is-ancestor HEAD "$commit"; then
    echo "Commit $commit must be on origin/main and include the current checkout" >&2
    exit 1
fi
git merge --ff-only "$commit"
deployed_commit=$(git rev-parse HEAD)
[[ "$deployed_commit" == "$commit" ]]

echo "Building backend commit $deployed_commit"
docker compose -f deploy/compose.yml build
docker run --rm --env-file deploy/prod.env email-triage-api:latest alembic upgrade head
docker compose -f deploy/compose.yml up -d --remove-orphans --wait --wait-timeout 60

install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile
systemctl reload-or-restart caddy
install -m 644 deploy/systemd/email-triage-digest.service deploy/systemd/email-triage-digest.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now email-triage-digest.timer
docker image prune -f
echo "Deployed backend commit $deployed_commit"
