# Deploying email-triage to AWS

This directory creates the AWS side: one EC2 box (`t4g.small`, Ubuntu 24.04 arm64, us-east-2), its security group, its IAM role, and the SSM parameter names. ngrok gives the app its public address, so there is no Elastic IP, DNS record in AWS, or load balancer.

What runs on the box:

- `api` (FastAPI) and `web` (Next.js) containers from `deploy/compose.yml`, published on `127.0.0.1` only.
- The ngrok agent (`deploy/systemd/ngrok.service`). It serves `https://triage.prakhargaming.com` with `deploy/traffic-policy.yml`, plus the internal endpoints `triage-api.internal` and `triage-web.internal`.
- An hourly systemd timer (`email-triage-digest.timer`). The job picks each user whose local notify hour has passed and who has no successful run today.

Every secret lives in SSM Parameter Store under `/email-triage/prod/`. The containers read it at startup through the instance role.

This is the plan's fallback runtime, the plain ngrok agent. It works without Ship. See [ngrok Ship](#ngrok-ship-unconfirmed-check-ship-docs) for what Ship's public docs do and do not say.

## 1. Tools

```sh
brew install terraform awscli
brew install --cask session-manager-plugin   # needed by `aws ssm start-session`
```

## 2. AWS credentials

Use the AWS account that already hosts `api.prakhargaming.com`. Everything here is named `email-triage*` and tagged `project=email-triage`, so it can't collide with that backend.

```sh
aws login                  # AWS CLI v2.32+; or: aws configure sso / aws configure
export AWS_PROFILE=default # whichever profile you just set up
aws sts get-caller-identity   # prints the account id; confirms you're in the right account
```

## 3. Terraform: init, plan, apply

Terraform compares the `.tf` files with what it created last time (the state file, `infra/terraform.tfstate`, local and gitignored) and shows a plan before it changes anything.

```sh
cd infra
terraform init     # downloads the AWS provider, once
terraform plan     # read-only: lists what would be created
terraform apply    # shows the same plan, type "yes" to create it
```

It prints two outputs:

- `instance_id`
- `ssm_session_command`, the command that opens a shell on the box

Run `terraform output` to see them again later. Re-running `terraform apply` is safe. It changes only what differs from the files. It never resets parameter values (`ignore_changes = [value]`), and a newer Ubuntu image does not replace the box.

Do not put secret values in `.tf` files. They would be stored in plain text in the state file.

## 4. Secrets

Terraform creates every secret as `CHANGE_ME`. The app and `deploy/render-ngrok.sh` refuse to start while any value is still `CHANGE_ME`. Set the real values once:

```sh
P=/email-triage/prod
R=us-east-2
put() { aws ssm put-parameter --region $R --overwrite --name "$P/$1" --value "$2" --type "$3"; }

put DATABASE_URL         'postgresql+psycopg://postgres.PROJECT:PASSWORD@aws-0-us-east-2.pooler.supabase.com:6543/postgres' SecureString
put GOOGLE_CLIENT_ID     'YOUR_CLIENT_ID.apps.googleusercontent.com' SecureString
put GOOGLE_CLIENT_SECRET 'GOCSPX-...'        SecureString
put TOKEN_ENCRYPTION_KEY 'BASE64_32_BYTES'   SecureString   # the SAME key as local .env, or stored Gmail tokens can't be decrypted
put LLM_API_KEY          'NGROK_AI_GATEWAY_ACCESS_KEY' SecureString
put NGROK_AUTHTOKEN      'NGROK_AGENT_AUTHTOKEN'       SecureString
put LLM_MODEL            'FLASH_MODEL_NAME_FROM_app.ngrok.ai/models' String
```

`put` does not echo values. Your shell history will still contain them, so run `history -d` afterwards, or prefix each line with a space if `HIST_IGNORE_SPACE` is set.

Terraform already set `LLM_BASE_URL=https://gateway.ngrok.ai`, `ALLOWED_EMAILS=prakhar530@gmail.com,nguyen26.bn@gmail.com` and `ENV=prod`. Overwrite them the same way to change them.

Check that the names are right without printing the values:

```sh
aws ssm get-parameters-by-path --region $R --path $P/ --query 'Parameters[].[Name,Type,LastModifiedDate]' --output table
```

To change a secret later, run `put` again and then restart whatever reads it. `deploy/deploy.sh` restarts everything.

## 5. ngrok

1. Reserve the domain. In the ngrok dashboard, go to Universal Gateway, then Domains, then New Domain, and add `triage.prakhargaming.com`. ngrok shows a CNAME target (something like `xxxx.yyyy.ngrok-cname.com`).
2. Create the CNAME. `prakhargaming.com` uses `ns-cloud-d*.googledomains.com`, which are Google Cloud DNS nameservers (checked with `dig NS prakhargaming.com`). Add the record in Cloud DNS, either in the console (Network services, then Cloud DNS, then your zone) or with:
   ```sh
   gcloud dns record-sets create triage.prakhargaming.com. --zone=YOUR_ZONE --type=CNAME --ttl=300 --rrdatas=TARGET_FROM_NGROK.
   ```
   Do not touch `api.prakhargaming.com`. Wait until `dig +short CNAME triage.prakhargaming.com` prints the target and ngrok shows the certificate as issued.
3. Authtoken. Create a dedicated agent authtoken for this box (dashboard, then Authtokens) and store it as `NGROK_AUTHTOKEN` (step 4).
4. Google OAuth client. Its redirect URI must be `https://idp.ngrok.com/oauth2/callback`, as in local development. Nothing new is needed for the custom domain.

## 6. Get the code onto the box and deploy

The box has no git credentials. Push this repo to a private GitHub repo first, then give the box read access, for example with a read-only deploy key:

```sh
$(terraform -chdir=infra output -raw ssm_session_command)   # opens a shell on the box

# on the box
sudo -i
cloud-init status --wait          # first boot only: waits for Docker, ngrok, git, aws-cli
ssh-keygen -t ed25519 -N '' -f /root/.ssh/id_ed25519
cat /root/.ssh/id_ed25519.pub     # add it in GitHub: repo Settings, Deploy keys (read-only)
ssh-keyscan github.com >> /root/.ssh/known_hosts
git clone git@github.com:YOU/email-summarizer.git /opt/email-triage
/opt/email-triage/deploy/deploy.sh
```

The code must live at `/opt/email-triage`, because the systemd units point there. `deploy/deploy.sh` is safe to re-run, and every later deploy is just:

```sh
sudo /opt/email-triage/deploy/deploy.sh
```

It pulls, builds both images on the box, runs `alembic upgrade head`, restarts the containers, installs the systemd units, enables the hourly timer, and restarts ngrok. That restart re-renders `/etc/ngrok/traffic-policy.yml` and `/etc/ngrok/agent.yml` (mode 600, root only) from SSM.

To see what's going on:

```sh
docker compose -f /opt/email-triage/deploy/compose.yml ps
docker compose -f /opt/email-triage/deploy/compose.yml logs -f api
journalctl -u ngrok -f
```

## 7. Verify (Milestone 5)

Run these from your laptop unless they say "on the box".

1. `/healthz` and the manifest load without login.
   ```sh
   curl -s https://triage.prakhargaming.com/healthz            # {"ok":true}
   curl -s -o /dev/null -w '%{http_code}\n' https://triage.prakhargaming.com/manifest.webmanifest
   ```
   The manifest arrives in milestone 6. Until then this prints `404` from Next.js. A 3xx would mean it went to the login, which is wrong.
2. A spoofed `X-User-Email` is ignored. Without an ngrok session cookie, the edge answers with a redirect to login, not with data.
   ```sh
   curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' -H 'X-User-Email: prakhar530@gmail.com' https://triage.prakhargaming.com/api/items
   ```
   Expect a 3xx pointing at ngrok/Google login. A `200` or any JSON is a failure.
3. A non-allowlisted account is blocked at the edge. In a private browser window, open `https://triage.prakhargaming.com` and sign in with a Google account that is not in the allowlist. Expect ngrok's 403 page. Then visit `https://triage.prakhargaming.com/ngrok/logout?auth_id=triage`.
4. FastAPI is reachable only through ngrok.
   ```sh
   aws ec2 describe-security-groups --region us-east-2 --filters Name=group-name,Values=email-triage \
     --query 'SecurityGroups[0].IpPermissions'            # []  (no inbound rules)
   IP=$(aws ec2 describe-instances --region us-east-2 --instance-ids $(terraform -chdir=infra output -raw instance_id) \
     --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
   curl -m 5 http://$IP:8000/healthz; echo "exit $?"      # times out (exit 28)
   ```
   On the box, both ports must be bound to loopback only:
   ```sh
   ss -ltn | grep -E ':(8000|3000) '                      # 127.0.0.1:8000 and 127.0.0.1:3000 only
   ```
5. The hourly timer runs once per user per day. On the box:
   ```sh
   systemctl list-timers email-triage-digest.timer
   journalctl -u email-triage-digest --since today -o cat | grep -E 'job_finished|job_skipped|run_finished'
   ```
   Most ticks log `{"event": "job_finished", "users": 0, ...}`. The first tick at or after 07:00 New York time runs each user. A tick that overlaps a still-running job logs `job_skipped`. To force a run for one person, use `docker run --rm --env-file /opt/email-triage/deploy/prod.env email-triage-api python -m app.jobs.run_digest --email her@example.com`.

   In the Supabase SQL editor, `ok_runs` must be 1 on every row. `all_runs` can be higher on a day when failures were retried.
   ```sql
   select r.user_id,
          (r.started_at at time zone coalesce(s.timezone, 'America/New_York'))::date as local_date,
          count(*) filter (where r.status in ('success', 'partial')) as ok_runs,
          count(*) as all_runs
   from agent_runs r left join agent_settings s on s.user_id = r.user_id
   group by 1, 2 order by 2 desc, 1;
   ```
6. Uptime. Point a free external monitor (UptimeRobot, Better Stack, or a Synthetic check in your ngrok account if available) at `https://triage.prakhargaming.com/healthz` every 5 minutes, expecting `200` and the body `{"ok":true}`. It needs no login.

## ngrok Ship (unconfirmed, check Ship docs)

Ship's public material doesn't yet document an app config file, a host daemon install command, or scheduled jobs. Sources read on 2026-11-15:

- https://ngrok.com/ship (landing page). It says "Your deploy config lives in your repo" and the daemon "opens a single outbound connection". It names no file, schema or install command, and mentions no cron or scheduled jobs.
- https://ngrok.com/docs/llms-full.txt. The only Ship content is the `ERR_NGROK_SHIP_*` error reference (https://ngrok.com/docs/errors/). The error messages hint at the API shape: apps with `environments` (each with a `branch`), `build_config` with provider `dockerfile` or `automatic` and a `dockerfile_path`, `endpoint_templates` (one must be named `primary`), environment variables (ngrok reserves `PORT`), up to 10 health checks each with a `path` and `port`, `replica_count`, and compute `pools` with a `join_key`. These are API fields, not a documented file format.

So nothing in this repo writes Ship config. If you have early-access docs, the likely steps are these. All are unconfirmed:

1. Install the Ship daemon on the box from the Ship dashboard, and join it to a pool. The plan's `user_data.sh` would install it on first boot, but there is no public install command to put there.
2. Create a repository app from the GitHub repo with two services:
   - The API uses a Dockerfile build with `dockerfile_path` `Dockerfile`, port 8000, a health check on `/healthz`, an internal endpoint `https://triage-api.internal`, and the env vars `SSM_PREFIX=/email-triage/prod/` and `AWS_DEFAULT_REGION=us-east-2`.
   - The web app uses a Dockerfile build with `web/Dockerfile` (build context `web/`), port 3000, and the internal endpoint `https://triage-web.internal`.
3. Create the public cloud endpoint `https://triage.prakhargaming.com` with `deploy/traffic-policy.yml`, putting the Google client id and secret in place of the placeholders (or using ngrok secrets if Ship supports them).
4. Keep the digest on the host's systemd timer. It needs the backend image on the box. If Ship builds remotely, either pull Ship's image in `email-triage-digest.service` or keep `docker compose build api` in the deploy step.

If you switch to Ship, stop the fallback first, or the two will fight over the same endpoint URLs:

```sh
sudo systemctl disable --now ngrok
sudo docker compose -f /opt/email-triage/deploy/compose.yml down
```

## Teardown

```sh
cd infra
terraform destroy
```

This deletes the box, the role, the security group and all `/email-triage/prod/*` parameters, including their secret values. The Supabase data, the ngrok domain and the DNS record are not managed here and stay.

## Costs and tradeoffs

- The box gets an auto-assigned public IPv4 so it can reach ngrok, Supabase, Google and GitHub without a NAT gateway (about $32/month). AWS bills public IPv4 at about $3.60/month. The security group allows no inbound traffic, so the address accepts no connections.
- Remote S3 state is not set up. The state file in `infra/` is the only record of what Terraform created, so don't delete it. It holds no secret values.
