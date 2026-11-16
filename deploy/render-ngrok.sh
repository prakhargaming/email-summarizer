#!/usr/bin/env bash
# Writes root-only /etc/ngrok/{agent,traffic-policy}.yml from SSM. Runs as ngrok.service's ExecStartPre.
set -euo pipefail
export PATH="$PATH:/snap/bin"  # the AWS CLI is a snap; systemd PATH lacks /snap/bin
cd "$(dirname "$0")/.."
set -a; . deploy/prod.env; set +a

get() { aws ssm get-parameter --with-decryption --name "${SSM_PREFIX}$1" --query Parameter.Value --output text; }
export GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET NGROK_AUTHTOKEN
GOOGLE_CLIENT_ID=$(get GOOGLE_CLIENT_ID)
GOOGLE_CLIENT_SECRET=$(get GOOGLE_CLIENT_SECRET)
NGROK_AUTHTOKEN=$(get NGROK_AUTHTOKEN)

umask 077
mkdir -p /etc/ngrok
python3 - <<'PY'
import json, os
keys = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "NGROK_AUTHTOKEN")
for k in keys:
    if os.environ[k] in ("", "CHANGE_ME"):
        raise SystemExit(f"SSM parameter {k} is not set")
policy = open("deploy/traffic-policy.yml").read()
for k in keys[:2]:
    policy = policy.replace(f'"<{k}>"', json.dumps(os.environ[k]))  # a JSON string is valid YAML
for path, body in (("/etc/ngrok/traffic-policy.yml", policy),
                   ("/etc/ngrok/agent.yml", f"version: 3\nagent:\n  authtoken: {os.environ['NGROK_AUTHTOKEN']}\n")):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, body.encode())
    os.close(fd)
PY
