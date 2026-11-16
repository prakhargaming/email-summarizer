#!/usr/bin/env bash
# Runs the real Traffic Policy in front of local FastAPI (:8000) and Next.js (:3000).
set -euo pipefail
cd "$(dirname "$0")/.."

uv run --env-file .env python - <<'PY'
import os
policy = open("deploy/traffic-policy.yml").read()
for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
    policy = policy.replace(f"<{key}>", os.environ[key])
fd = os.open("deploy/.traffic-policy.local.yml", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, policy.encode())
PY

exec ngrok start --all --config "$HOME/Library/Application Support/ngrok/ngrok.yml,deploy/ngrok.dev.yml"
