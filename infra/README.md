# Deploying email-triage

Two halves:

- **AWS (this directory):** one EC2 box (`t4g.nano`, 0.5 GB RAM plus 2 GB swap, Ubuntu 24.04 arm64, us-east-2), its Elastic IP, a security group open on 443 and 80 only, its IAM role, and the SSM parameter names. It serves the API at `https://triage-api.prakhargaming.com`.
- **Vercel:** the Next.js frontend at `https://triage.prakhargaming.com`, with Google login (NextAuth) and the `/api/*` proxy that signs a short-lived token for FastAPI. See [step 7](#7-vercel).

What runs on the box:

- The `api` (FastAPI) container from `deploy/compose.yml`, published on `127.0.0.1:8000` only.
- Caddy (apt package) with `deploy/Caddyfile`. It gets a Let's Encrypt certificate for `triage-api.prakhargaming.com` and forwards only `/api/*` and `/healthz` to the container. Everything else is a 404.
- An hourly systemd timer (`email-triage-digest.timer`). The job picks each user whose local notify hour has passed and who has no successful run today.

Every secret lives in SSM Parameter Store under `/email-triage/prod/`. The container reads it at startup through the instance role. FastAPI is public, so it trusts nothing but a token signed with `API_SIGNING_SECRET`, which only it and Vercel hold.

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

It prints three outputs:

- `instance_id`
- `public_ip`, the Elastic IP for the DNS record in step 5
- `ssm_session_command`, the command that opens a shell on the box

Run `terraform output` to see them again later. Re-running `terraform apply` is safe. It changes only what differs from the files. It never resets parameter values (`ignore_changes = [value]`), and a newer Ubuntu image does not replace the box.

Do not put secret values in `.tf` files. They would be stored in plain text in the state file.

## 4. Secrets

Terraform creates every secret as `CHANGE_ME`. The app refuses to start while any value is still `CHANGE_ME`. Set the real values once:

```sh
P=/email-triage/prod
R=us-east-2
put() { aws ssm put-parameter --region $R --overwrite --name "$P/$1" --value "$2" --type "$3"; }

put DATABASE_URL         'postgresql+psycopg://postgres.PROJECT:PASSWORD@aws-0-us-east-2.pooler.supabase.com:6543/postgres' SecureString
put GOOGLE_CLIENT_ID     'YOUR_CLIENT_ID.apps.googleusercontent.com' SecureString
put GOOGLE_CLIENT_SECRET 'GOCSPX-...'        SecureString
put TOKEN_ENCRYPTION_KEY 'BASE64_32_BYTES'   SecureString   # the SAME key as local .env, or stored Gmail tokens can't be decrypted
put LLM_API_KEY          'NGROK_AI_GATEWAY_ACCESS_KEY' SecureString
put API_SIGNING_SECRET   'SAME_VALUE_AS_VERCEL'        SecureString   # python -c "import secrets;print(secrets.token_urlsafe(48))"
put LLM_MODEL            'FLASH_MODEL_NAME_FROM_app.ngrok.ai/models' String
```

`put` does not echo values. Your shell history will still contain them, so run `history -d` afterwards, or prefix each line with a space if `HIST_IGNORE_SPACE` is set.

Terraform already set `LLM_BASE_URL=https://gateway.ngrok.ai`, `ALLOWED_EMAILS=prakhar530@gmail.com,nguyen26.bn@gmail.com` and `ENV=prod`. Overwrite them the same way to change them.

Check that the names are right without printing the values:

```sh
aws ssm get-parameters-by-path --region $R --path $P/ --query 'Parameters[].[Name,Type,LastModifiedDate]' --output table
```

To change a secret later, run `put` again and then restart whatever reads it. `deploy/deploy.sh` restarts everything.

## 5. DNS

`prakhargaming.com` uses `ns-cloud-d*.googledomains.com`, which are Google Cloud DNS nameservers (checked with `dig NS prakhargaming.com`). Add the record by hand, in the console (Network services, then Cloud DNS, then your zone) or with:

```sh
gcloud dns record-sets create triage-api.prakhargaming.com. --zone=YOUR_ZONE --type=A --ttl=300 \
  --rrdatas=$(terraform -chdir=infra output -raw public_ip)
```

Do not touch `api.prakhargaming.com`. Wait until `dig +short triage-api.prakhargaming.com` prints the Elastic IP. Caddy retries the certificate on its own until DNS resolves. The `triage` record for Vercel is in step 7.

## 6. Get the code onto the box and deploy

The box has no git credentials. Push this repo to a private GitHub repo first, then give the box read access, for example with a read-only deploy key:

```sh
$(terraform -chdir=infra output -raw ssm_session_command)   # opens a shell on the box

# on the box
sudo -i
cloud-init status --wait          # first boot only: waits for Docker, Caddy, git, aws-cli
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

It pulls, builds the API image on the box, runs `alembic upgrade head`, restarts the container, installs the Caddyfile and reloads Caddy, installs the systemd units, and enables the hourly timer. The first build on a nano is slow (it leans on swap); later builds reuse the dependency layer unless `uv.lock` changed.

To see what's going on:

```sh
docker compose -f /opt/email-triage/deploy/compose.yml ps
docker compose -f /opt/email-triage/deploy/compose.yml logs -f api
journalctl -u caddy -f
```

## 7. Vercel

1. Import the GitHub repo in Vercel with **Root Directory** `web`. The framework preset is Next.js, and nothing else needs changing.
2. Set these environment variables (Production):
   | Name | Value |
   |---|---|
   | `NEXTAUTH_URL` | `https://triage.prakhargaming.com` |
   | `NEXTAUTH_SECRET` | `openssl rand -base64 32` (a new value, not the local one) |
   | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | the same client as SSM |
   | `API_URL` | `https://triage-api.prakhargaming.com` |
   | `API_SIGNING_SECRET` | the same value as SSM |
3. Add the domain `triage.prakhargaming.com` in the Vercel project (Settings, then Domains). Vercel shows a `CNAME` target; add it in Cloud DNS like step 5.
4. Google OAuth client (Google Cloud console, Credentials): add the redirect URIs `https://triage.prakhargaming.com/api/auth/callback/google` and `http://localhost:3000/api/auth/callback/google`, and the authorized domain `prakhargaming.com`. Keep the same client: the stored Gmail refresh tokens only work with the client that issued them. `https://idp.ngrok.com/oauth2/callback` can be removed.

Signing in stores the Gmail refresh token (NextAuth's `signIn` callback posts it to `/api/gmail/connect`), so the API must be up before the first sign-in.

## 8. Verify (Milestone 5)

Run these from your laptop unless they say "on the box".

1. `/healthz` loads without login, and nothing else on the API host does.
   ```sh
   curl -s https://triage-api.prakhargaming.com/healthz              # {"ok":true}
   curl -s -o /dev/null -w '%{http_code}\n' https://triage-api.prakhargaming.com/docs   # 404
   curl -s -o /dev/null -w '%{http_code}\n' http://triage-api.prakhargaming.com/healthz # 308 to https
   ```
2. FastAPI rejects anything without a valid signed token, including a forged `X-User-Email`.
   ```sh
   curl -s -w ' %{http_code}\n' -H 'X-User-Email: prakhar530@gmail.com' https://triage-api.prakhargaming.com/api/items  # 401
   curl -s -w ' %{http_code}\n' https://triage.prakhargaming.com/api/items                                              # 401 (no session)
   curl -s -w ' %{http_code}\n' -X POST https://triage.prakhargaming.com/api/gmail/connect                              # 404 (browser can't store tokens)
   ```
3. A non-allowlisted account is refused at sign-in. In a private window, open `https://triage.prakhargaming.com` and sign in with a Google account that is not in the allowlist. Expect NextAuth's "Access denied" page.
4. FastAPI is reachable only over HTTPS through Caddy.
   ```sh
   aws ec2 describe-security-groups --region us-east-2 --filters Name=group-name,Values=email-triage \
     --query 'SecurityGroups[0].IpPermissions[].FromPort'   # [80, 443]
   curl -m 5 http://$(terraform -chdir=infra output -raw public_ip):8000/healthz; echo "exit $?"   # times out (exit 28)
   ```
   On the box, the API must be bound to loopback only:
   ```sh
   ss -ltn | grep ':8000 '                                 # 127.0.0.1:8000 only
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
6. Uptime. Point a free external monitor (UptimeRobot or Better Stack) at `https://triage-api.prakhargaming.com/healthz` every 5 minutes, expecting `200` and the body `{"ok":true}`. It needs no login.

## Teardown

```sh
cd infra
terraform destroy
```

This deletes the box, its Elastic IP, the role, the security group and all `/email-triage/prod/*` parameters, including their secret values. The Supabase data, the Vercel project and the DNS records are not managed here and stay. Delete the `triage-api` A record too, or it will point at an IP AWS may hand to someone else.

## Costs and tradeoffs

- The box has a public IPv4 (the Elastic IP) for inbound HTTPS and for reaching Supabase, Google and GitHub without a NAT gateway (about $32/month). AWS bills it at about $3.60/month. A t4g.nano is about $3/month.
- The API is on the open internet. The signed token is the only gate (Vercel's egress IPs aren't fixed, so an IP allowlist isn't possible). Rotate `API_SIGNING_SECRET` in SSM and Vercel together if it ever leaks.
- Remote S3 state is not set up. The state file in `infra/` is the only record of what Terraform created, so don't delete it. It holds no secret values.
