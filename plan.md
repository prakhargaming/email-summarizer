# Project: Gmail Action-Item Triage App (single-user, extensible to few users)

## Goal
A private web app that reads one person's Gmail every morning, uses an LLM to extract the emails that need action, and stores them as a persistent list she can Accept or Reject. A web push notification goes out each morning only if there are new action items. Accept/reject decisions can optionally be turned into long-term rules that become part of the agent's system prompt.

## Stack (decided, don't substitute)
- **Backend:** FastAPI (Python 3.12+) on a single AWS EC2 instance (`t4g.nano`) behind Caddy (see "Hosting: Vercel + Caddy"). It owns the HTTP API, the Gmail pipeline, LLM calls, and push sending.
- **Python package management:** `uv`. Dependencies are declared in `pyproject.toml` with a committed `uv.lock`; use `uv add` / `uv sync` / `uv run` (no `pip install`, no `requirements.txt`). Pin the Python version with `.python-version`.
- **LLM:** Google Gemini via the official `google-genai` Python SDK, routed through the ngrok AI Gateway.
- **Database:** Supabase, used **only as managed Postgres** (the system of record). Supabase Auth, the Data API (PostgREST), Edge Functions, and pg_cron are not used.
- **Frontend:** Next.js (App Router, TypeScript) on **Vercel** at `triage.prakhargaming.com`. PWA-capable.
- **Hosting + routing:** Vercel serves the frontend. A catch-all Next.js route handler proxies `/api/*` server-to-server to FastAPI at `https://triage-api.prakhargaming.com` (Caddy on the EC2 box, automatic TLS).
- **Login:** Google sign-in with **NextAuth (`next-auth` v4)** on Vercel, which also requests Gmail read-only access. FastAPI has no login code; it accepts only short-lived tokens signed by the Next.js proxy.
- **Scheduled job:** A systemd timer on the EC2 box runs the digest job as a separate CLI process.
- **Notifications:** Web Push (VAPID) via a service worker, sent from the backend with `pywebpush`; the app is installable as a PWA.

## Architecture
- **Login happens in one place: NextAuth on Vercel.** The browser holds only the NextAuth session cookie (a JWT with the email; never a Google token). It calls same-origin `/api/*` on Vercel.
- **The proxy:** `web/app/api/[...path]/route.ts` reads the NextAuth session (401 if none), signs a token (HS256 with `API_SIGNING_SECRET`, `sub` = email, 60-second expiry), and forwards the request to `API_URL` with `Authorization: Bearer <token>`. NextAuth's own routes (`/api/auth/*`) are more specific and never reach the proxy.
- FastAPI verifies that token in `current_user`, checks the email against `ALLOWED_EMAILS`, and upserts a row in its own `users` table to get a `user_id`. FastAPI is on the public internet, so **the signed token is the only thing it trusts**; any `X-User-Email` header is ignored.
- FastAPI is the single API for all reads and writes. It connects to Supabase Postgres directly (use the pooler connection string) and **must filter every query by `user_id`**.
- The browser only talks to `/api/*` on the same origin. It never sees the Gmail token, any database credential, or any LLM key.
- Pages fetch data from client components with plain same-origin `fetch('/api/...')`, so the session cookie is sent automatically. Only the proxy route handler and the NextAuth `signIn` callback call FastAPI.
- **Local development:** `next dev` with the real Google login (add `http://localhost:3000/api/auth/callback/google` as a redirect URI) and `API_URL=http://localhost:8000`. For backend-only work (curl, scripts), FastAPI accepts a `DEV_USER_EMAIL` setting in place of the token, only when `ENV=dev` (refuse to start if both `DEV_USER_EMAIL` is set and `ENV` isn't `dev`).
- Code layout: `app/api` (routers), `app/core` (config, auth, crypto), `app/llm` (Gemini client wrapper), `app/pipeline` (gmail fetch -> prompt -> LLM -> persist), `app/push`, `app/jobs/run_digest.py` (CLI entrypoint), `app/db` (models, Alembic migrations). The API process and the job process share `app/pipeline` and `app/llm`.

## LLM: Gemini through the ngrok AI Gateway
- Add `google-genai` (`uv add google-genai`). Create **one client factory** in `app/llm` and use it everywhere (digest pipeline, rule distillation, rule consolidation). Use the async client (`client.aio`) inside FastAPI request handlers.
- Gateway client configuration:
```python
from google import genai
from google.genai import types

client = genai.Client(
    api_key=settings.llm_api_key,   # the ngrok access key
    http_options=types.HttpOptions(
        base_url=settings.llm_base_url,   # https://gateway.ngrok.ai
        api_version="v1beta",
    ),
)
```
- Use `generate_content` (GenerateContent API). Do not use the newer Interactions API.
- Pass the assembled system prompt via `types.GenerateContentConfig(system_instruction=...)`. Put the email batch in `contents`, inside clear delimiters.
- **Structured output:** set `response_mime_type="application/json"` and `response_schema=<Pydantic model>` in the config, then still validate with Pydantic on our side. Discard output that fails validation and record the run as failed.
- **Handle blocked or empty responses.** Gemini can return no text because of safety filters or a non-STOP finish reason. Check `finish_reason`/prompt feedback, retry once with a smaller batch, and otherwise record the run as `partial` with an error code rather than crashing or silently producing zero items.
- Use `temperature` low (around 0.2) for the extraction call.
- The model is a config value (`LLM_MODEL`). The gateway's Models page (https://app.ngrok.ai/models) lists valid Gemini model names; use a fast, cheap Flash-tier model by default and don't hardcode a name.
- **Direct fallback:** if `LLM_BASE_URL` is unset, the same factory builds a plain client with `api_key=settings.gemini_api_key` and no custom base URL, so the app can bypass the gateway for debugging.
- Credentials: the ngrok access key must allow the Google provider. If I attach my own Google key to the gateway, Google bills me directly; otherwise gateway credits are used. Either way the code is identical.
- Server-side only. No LLM key ever reaches the browser.
- Docs: https://ngrok.com/docs/ai-gateway/sdks/google and https://ngrok.com/docs/ai-gateway/providers/google

## Hosting: Vercel + Caddy (routing and login)
- **Frontend on Vercel** at `https://triage.prakhargaming.com` (a `CNAME` to Vercel, added by hand at my DNS). Vercel env vars: `NEXTAUTH_URL`, `NEXTAUTH_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `API_URL` (`https://triage-api.prakhargaming.com`), `API_SIGNING_SECRET`.
- **API on EC2** at `https://triage-api.prakhargaming.com`: an Elastic IP with an `A` record added by hand at my DNS, and Caddy (apt package) for automatic Let's Encrypt TLS. The security group opens 443, plus 80 for certificate issuance and redirects. Caddy forwards only `/api/*` and `/healthz` to uvicorn on `127.0.0.1:8000` and answers 404 for everything else (so FastAPI's `/docs` and `/openapi.json` aren't exposed). Vercel's egress IPs aren't fixed, so there is no IP allowlist; the signed token is the gate.
- **Google OAuth client (the only one):** one Web client with redirect URIs `https://triage.prakhargaming.com/api/auth/callback/google` and `http://localhost:3000/api/auth/callback/google`, set to "In production", with the scopes `openid`, `email`, `profile` and `gmail.readonly`. FastAPI uses **the same client ID and secret** to refresh Gmail tokens, because Google refresh tokens are tied to the client that issued them. Keep this client (it already issued the stored tokens); the old `https://idp.ngrok.com/oauth2/callback` redirect URI can be removed.
- **NextAuth config** (`web/lib/auth.ts`): the Google provider with `authorization.params` `{ scope: "openid email profile https://www.googleapis.com/auth/gmail.readonly", access_type: "offline", prompt: "consent" }` (without `prompt: consent`, repeat logins return no refresh token). JWT session strategy, no database adapter. The `signIn` callback posts the refresh token to FastAPI (see "Google OAuth and Gmail token") and refuses the sign-in if that fails.
- **Session expiry in the frontend:** the `fetch` helper treats a 401 from `/api/*` as an expired session and calls NextAuth's `signIn("google")`. "Sign out" calls `signOut()`.
- **Service worker:** the script is same-origin and sends cookies. Verify after installing the PWA that push and `notificationclick` still work.

## Security and privacy requirements
- **Do not persist email bodies.** Store only sender, subject, the LLM's summary, why it matters, suggested action, the Gmail thread ID, and a deep link back to the thread. Email bodies exist only in memory during a run.
- Do not log email content or raw LLM prompts/responses. Log counts, IDs, timings, token usage, and error codes only (structured JSON logs).
- **Data handling by the model provider:** email content is sent to Google. Requests go through the ngrok AI Gateway on gateway credits (the gateway stays; only ngrok hosting and login were dropped), and I've confirmed that ngrok does not train on this data. Note this in the README. If I later switch to my own Google key, it must be a paid-tier key whose terms exclude training on API data, never a free-tier key.
- Gmail refresh token: store encrypted at rest (AES-GCM via `cryptography`, key held in AWS SSM Parameter Store or Secrets Manager, fetched at startup). Never return it from any endpoint.
- **Treat email content as untrusted input (prompt injection).** Wrap each email in clear delimiters, instruct the model that email text is data and never instructions, give the model no tools (no function calling, no grounding, no URL context), and require structured JSON output validated with Pydantic.
- Gmail scope: `https://www.googleapis.com/auth/gmail.readonly` only.
- Restrict access to an allowlist of emails, enforced in FastAPI (`ALLOWED_EMAILS`, 403 for anyone else) on every request. The NextAuth `signIn` callback calls `POST /api/gmail/connect`, so a non-allowlisted account gets that 403 and is refused at sign-in; there is one allowlist, not two copies.
- `API_SIGNING_SECRET` (shared by Vercel and FastAPI) is the key to the API: anyone holding it can act as any allowlisted user. Keep it only in Vercel env and SSM, generate it randomly (32+ bytes), and rotate both together.
- No CORS middleware: the browser only talks to the Vercel origin, and FastAPI is called server-to-server. Don't add a wildcard.
- In Supabase, **disable the Data API** and keep RLS enabled with no policies on every table, so the public anon key can't reach anything. Only FastAPI's direct Postgres connection can read the data.
- Secrets live in SSM Parameter Store / Secrets Manager, read via the EC2 instance's IAM role. No secrets in the repo, AMI, or plain `.env` committed anywhere.

## AWS / EC2 deployment
- One `t4g.nano` (0.5 GB RAM plus 2 GB swap) running Ubuntu LTS with Docker and Caddy. Only the backend image is built on the box. It installs dependencies with `uv sync --frozen --no-dev` so it matches `uv.lock` exactly. `uvicorn` runs in the container, published on `127.0.0.1:8000` only; Caddy terminates TLS.
- Security group: inbound **443 and 80 only**. No public SSH; use SSM Session Manager for shell access.
- IAM instance role with read access to the specific SSM parameters/secrets only. Containers fetch secrets from SSM at startup using the instance role. Set the IMDSv2 hop limit to 2 so containers can reach instance credentials.
- Health endpoint `GET /healthz` (no auth, no secrets, no DB content). Add a simple uptime check on it.
- The digest job runs as a **separate process** via a systemd timer on the host every hour, running the job with the same backend image (`docker run --rm <backend image> python -m app.jobs.run_digest`). Do *not* use an in-process scheduler like APScheduler inside the API, because multiple uvicorn workers would run the job multiple times.
- Include deployment artifacts: the Caddyfile, the compose file, the systemd timer + service for the digest job, and an example environment file with placeholder values.

## Google OAuth and Gmail token
- On sign-in, NextAuth's `signIn` callback receives `account.refresh_token` server-side. It posts it once, server-to-server, to FastAPI `POST /api/gmail/connect` with body `{"refresh_token": "..."}` and the same signed token the proxy uses. If that call fails (no refresh token, not allowlisted, API down), the callback returns `false` and the sign-in is refused, so she never ends up logged in without working Gmail.
- **Never put the refresh or access token in the NextAuth JWT or session.** The `jwt` callback keeps only the default claims (email, name, picture).
- `connect` rejects an empty token (with a clear error), encrypts it, and stores it in `gmail_credentials`. The token never touches the browser.
- The frontend no longer calls `connect`. It checks `GET /api/gmail/status`; if Gmail isn't connected or `needs_reauth` is set, it shows the "Reconnect Gmail" banner, whose button calls `signIn("google")`. That forces fresh consent, and the `signIn` callback stores the new token.
- **Re-verify Milestone 1 on the new login:** sign in, check the token is stored, then use it from FastAPI after the access token has expired (more than an hour later).
- `gmail.readonly` is a Google **restricted** scope. Set the Google Cloud OAuth app to **"In production"**, because in "Testing" mode refresh tokens expire after 7 days and the job will silently fail. The plan assumes it can stay **unverified** for personal use (under 100 users, with Google's "unverified app" warning screen). **Not yet confirmed:** check this against Google's current rules when creating the client. If Google requires verification for a production app with this scope, stop and ask me before falling back to "Testing" mode, which would mean logging in again every 7 days.
- If the refresh exchange returns `invalid_grant`, set `needs_reauth = true`, skip the user, and send a push notification asking her to sign in again. The UI shows a "Reconnect Gmail" banner, which signs in again with fresh consent.

## Data model (Postgres, managed with Alembic)
```sql
users(                                   -- created on first request, keyed by the verified email
  id uuid primary key default gen_random_uuid(),
  email text unique not null,            -- stored lowercase
  created_at timestamptz default now()
)

gmail_credentials(
  user_id uuid primary key references users,
  refresh_token_encrypted bytea not null,
  needs_reauth boolean default false,
  updated_at timestamptz default now()
)

agent_settings(
  user_id uuid primary key references users,
  focus_text text default '',            -- her free-text "what I care about"
  timezone text not null default 'America/New_York',
  notify_hour_local int not null default 7,
  last_run_at timestamptz
)

agent_rules(                              -- long-term learned rules
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users,
  rule_text text not null,               -- short natural-language rule
  kind text check (kind in ('prioritize','ignore','note')),
  source_item_id uuid,                   -- action item that produced it
  active boolean default true,
  created_at timestamptz default now()
)

action_items(
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users,
  gmail_thread_id text not null,
  gmail_message_id text,
  sender text, subject text,
  summary text, why_it_matters text, suggested_action text,
  priority smallint,                     -- 1 (highest) to 3
  due_hint text,
  status text not null default 'pending'
    check (status in ('pending','accepted','rejected','done')),
  decided_at timestamptz,
  decision_note text,
  run_id uuid,
  created_at timestamptz default now(),
  unique (user_id, gmail_thread_id)      -- dedupe threads across days
)

agent_runs(
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  started_at timestamptz default now(), finished_at timestamptz,
  emails_seen int, items_created int,
  status text,                           -- success | partial | failed
  error_code text
)

push_subscriptions(
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users,
  endpoint text unique not null, p256dh text not null, auth text not null,
  user_agent text, created_at timestamptz default now()
)
```
Enable RLS on all tables with **no policies** (deny-all for Supabase's API roles). FastAPI connects as the database owner, which bypasses RLS, so every query must still filter by `user_id` in code.

## API surface (all require a valid proxy-signed token for an allowlisted user unless noted)
- `GET /healthz` (no auth)
- `POST /api/gmail/connect` body `{refresh_token}` (called by the NextAuth `signIn` callback only), `GET /api/gmail/status`
- `GET /api/items?status=pending|accepted|rejected|done`
- `POST /api/items/{id}/decision` body: `{decision: "accepted"|"rejected", remember: bool, note?: string}`. If `remember` is true, returns a **proposed rule** (not yet saved).
- `POST /api/rules` (save a confirmed rule), `GET /api/rules`, `PATCH /api/rules/{id}`, `DELETE /api/rules/{id}`, `POST /api/rules/consolidate` (returns a proposed merged set; saved only after she confirms)
- `GET/PUT /api/settings` (focus_text, timezone, notify_hour_local)
- `POST /api/push/subscribe`, `DELETE /api/push/subscribe`, `POST /api/push/test`
- `POST /api/runs/trigger` (dev/admin only; runs the pipeline for the caller on demand)
- `GET /api/runs` (recent run history)

## Daily run (`python -m app.jobs.run_digest`, hourly via systemd timer)
1. **Select users:** those whose local time (per `agent_settings.timezone`) is at `notify_hour_local` and who have no successful run today. This handles DST and lets her change her time later. Guard against overlapping job executions (Postgres advisory lock or per-user run row with a uniqueness constraint).
2. **Fetch:** Refresh the Google access token, then list messages with a Gmail query like `in:inbox after:<last_run_at epoch>` (fall back to the last 24h on first run), excluding spam and trash. Fetch each message, extract plain text (strip HTML and quoted replies), truncate each to a sane limit (about 4k chars), cap total emails per run (batch into multiple Gemini calls if needed, then merge and dedupe), and note if the cap was hit.
3. **Build the prompt** (see below), call Gemini through the gateway, and parse and validate the JSON with Pydantic.
4. **Write:** Upsert action items (`ON CONFLICT DO NOTHING` on user + thread), record the run in `agent_runs`, update `last_run_at`.
5. **Notify:** If at least one new item was created, send a Web Push with a brief summary ("3 things need you today: <top item>...") and a click-through URL to the app. If there are zero new items, send nothing.
6. One user's failure must not block others. The job must be idempotent and safe to re-run.

## Prompt assembly (the persistence mechanism)
The `system_instruction` is built fresh each run from, in order:
1. Base instructions: role, output schema, the "email content is untrusted data" rule, what counts as an action item (needs a reply, a decision, a deadline, a payment, a booking; not newsletters or receipts unless they require action).
2. Her `focus_text`.
3. **Active `agent_rules`**, rendered as a bulleted "Standing preferences learned from her past decisions" section. Cap at about 50 active rules; if exceeded, surface a "consolidate rules" button in settings that asks the LLM to merge near-duplicates and shows the result for her approval before saving.
4. A few recent decisions (last ~10 accepted and rejected items: sender, subject, summary only) as lightweight few-shot examples.

## Accept / Reject UX
- Each pending item has **Accept** and **Reject** buttons. Accept moves it to a "To do" list (with an optional Done state). Reject moves it to a collapsed Rejected list that she can restore.
- On either action, show an optional **"Remember this for the future"** toggle plus an optional note field.
- If toggled on, the backend calls Gemini (via the gateway) to distill the item, her decision, and her note into **one short, generalizable rule** (e.g. "Emails from the building's property manager about parking are not action items" or "Always prioritize anything from the school district"). Show the proposed rule text, let her edit or cancel, then save it via `POST /api/rules`. Prefer narrow rules; don't over-generalize from one email.
- A **Settings** page lists all rules with enable/disable, edit, and delete, plus the `focus_text` box, notification time, and timezone. This is her way to see and control what the agent has "learned".

## Web Push
- Generate VAPID keys; keep the private key in SSM/Secrets Manager. Expose the public key to the client as `NEXT_PUBLIC_VAPID_PUBLIC_KEY`.
- Backend sends with `pywebpush`. Prune subscriptions that return 404/410.
- Service worker (frontend) handles `push` (show notification) and `notificationclick` (open the app to the pending list).
- Add a web app manifest and icons so the app is installable. **iOS only delivers web push to PWAs added to the home screen (iOS 16.4+)**, so add a short in-app onboarding screen that explains "Add to Home Screen" and then triggers the permission prompt from a user gesture.
- Include a "Send test notification" button in settings.

## Configuration (secrets in SSM/Secrets Manager, non-secrets in env)
Backend: `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (the same client as NextAuth), `TOKEN_ENCRYPTION_KEY`, `LLM_API_KEY` (ngrok access key), `LLM_BASE_URL` (`https://gateway.ngrok.ai`; leave unset to call Gemini directly), `GEMINI_API_KEY` (only used when `LLM_BASE_URL` is unset), `LLM_MODEL`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (mailto:), `ALLOWED_EMAILS`, `API_SIGNING_SECRET`, `APP_URL` (`https://triage.prakhargaming.com`, used for push click-through links), `ENV`, and `DEV_USER_EMAIL` (dev only).
Frontend (Vercel env, all server-side except the VAPID key): `NEXTAUTH_URL`, `NEXTAUTH_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (same client as the backend), `API_URL`, `API_SIGNING_SECRET` (same value as the backend), `NEXT_PUBLIC_VAPID_PUBLIC_KEY`. The browser still only calls same-origin `/api`.

## Milestones (build in this order, verify each before moving on)
1. **Auth + Gmail plumbing:** run the ngrok agent locally with the Traffic Policy (Google login with gmail.readonly, allowlist, path routing). FastAPI reads `X-User-Email` and upserts `users`, plus encrypted refresh-token storage via `/api/gmail/connect` and a page that lists the subjects of the last 24h of mail via the API. Verify that ngrok forwards a usable refresh token (see "Google OAuth and Gmail token"), and that token refresh works after the access token expires. **Rework the existing milestone-1 code:**
   - `app/core/auth.py`: replace Supabase JWT verification with the header, `DEV_USER_EMAIL`, allowlist, and `users` upsert. Remove `pyjwt` and the Supabase settings from `app/core/config.py`.
   - `app/db`: add the `users` model. Point the `gmail_credentials` FK at `users` (edit `0001` if it was never applied to a real database; otherwise add `0002`).
   - `app/api/gmail.py`: `connect` reads the refresh token from the `x-gmail-refresh-token` header (set by the Traffic Policy) instead of the request body.
   - `app/main.py`: remove `CORSMiddleware`.
   - `web/`: delete `lib/supabase/`, `app/auth/callback/`, `app/auth-buttons.tsx`, and the Supabase session logic in `proxy.ts`. Remove the `@supabase/*` packages. Change `page.tsx` to a client-side `fetch('/api/gmail/recent')` and add the dev rewrite in `next.config.ts`.
   - Update both `.env.example` files and the tests.
2. **Pipeline:** `app/llm` client factory + `app/pipeline` + validated structured output + `action_items` table, runnable manually via the CLI and `POST /api/runs/trigger`. First do a one-off smoke test that a plain `generate_content` call succeeds through the gateway. Test with real and synthetic emails, including one with an injection attempt ("ignore previous instructions...") to confirm it is treated as data, and one that triggers an empty/blocked response path. Add pytest coverage with the LLM and Gmail mocked.
3. **UI:** Pending / To do / Rejected lists, Accept/Reject, Settings with `focus_text`.
4. **Learned rules:** "Remember this" flow, rules management page, rules injected into the system prompt. Verify a saved rule visibly changes next run's output.
5. **Deployment + scheduling:** Provision the instance with Terraform (Elastic IP, 443/80 ingress), Caddy in front of FastAPI, SSM-backed config, the frontend on Vercel with NextAuth and the `/api` proxy, the digest systemd timer, local-time gating, and the no-duplicate-run guard. Re-verify Milestone 1 on the new login (token stored, refresh works over an hour later). Verify that a non-allowlisted Google account is refused at sign-in, that FastAPI rejects requests without a valid signed token (including a forged `X-User-Email`), that `/healthz` loads without login, that FastAPI is reachable only over HTTPS on 443 and only on `/api/*` and `/healthz`, and that the hourly timer runs exactly once per user per day.
6. **Web Push:** subscription flow, notification only when new items exist, `invalid_grant` re-auth notification, stale subscription pruning.
7. **Hardening:** a second allowlisted test user cannot read the first user's data (API tests), and the Supabase anon key can't read any table, error states in the UI, run history view, README with setup steps (Google Cloud console (one OAuth client shared by NextAuth and FastAPI), Supabase (Postgres only, Data API off), SSM parameters, gateway/Gemini config, VAPID, EC2, Caddy, Vercel, systemd timer).

## Out of scope for now
WhatsApp/Telegram, other mail providers, replying to or sending email, multi-tenant billing, storing email bodies, Supabase Auth, Supabase Data API, Supabase Edge Functions, Gemini tools/grounding, the Interactions API.

## Decisions (answered)
- **Timezone and notification time:** she is in Decatur, GA, so the default timezone is `America/New_York` and the default notification time is 7:00 AM local (`notify_hour_local = 7`). Both are only defaults: the Settings page must let her change the timezone and the notification hour.
- **Infrastructure as code:** use **Terraform** for the AWS setup. Put it in `infra/` and include a short README that explains the `terraform init` / `plan` / `apply` workflow for someone new to Terraform. It should manage the EC2 instance (with `user_data` that installs Docker and Caddy), its Elastic IP, the security group (inbound 443 and 80 only), IAM instance role and policy, and SSM parameter *names*. DNS records are added by hand (not in Route 53). **Do not put secret values in Terraform**, because they would end up in the state file. Terraform creates SSM parameters with placeholder values and `lifecycle { ignore_changes = [value] }`, and real secrets are set afterwards with `aws ssm put-parameter`. Use a local state file for now (gitignored), and leave remote S3 state as an optional later step.
- **LLM credentials:** Gemini runs on an **ngrok access key using gateway credits** (no Google key of my own attached).
- **Domains:** the app lives at **`triage.prakhargaming.com`** on Vercel (`CNAME` to Vercel) and the API at **`triage-api.prakhargaming.com`** on the EC2 box (`A` record to the Elastic IP). `api.prakhargaming.com` is already taken by my personal site's backend, so don't touch it. DNS for `prakhargaming.com` is not in Route 53 (its nameservers are `ns-cloud-d*.googledomains.com`), so I add both records by hand.
- **AWS account and region:** use my existing AWS account (the same one hosting `api.prakhargaming.com`) in `us-east-2` (Ohio). Use separate, clearly named resources (tag everything `project = email-triage`) so nothing collides with the existing backend.
- **Hosting and login (changed 2026-11-16, replacing ngrok Ship and edge OAuth):** frontend on Vercel, Google login with NextAuth, FastAPI on a `t4g.nano` behind Caddy, trusting only proxy-signed tokens. Supabase Auth stays dropped. See "Hosting: Vercel + Caddy".
- **Allowed users:** `prakhar530@gmail.com` (me) and `nguyen26.bn@gmail.com` (her), in `ALLOWED_EMAILS`.
- **ngrok account:** I work at ngrok. Only the AI Gateway is still used.
- **Database:** keep Supabase as managed Postgres only. It's free, has backups and a dashboard, and is already set up. It could later be swapped for any Postgres (Neon, RDS) by changing `DATABASE_URL`.
- **Data handling:** I've confirmed that ngrok does not train on the data sent through the gateway. Note this in the README alongside the gateway-credits setup.
- **When connect is called:** only by the NextAuth `signIn` callback, on every sign-in. The frontend never calls it; when Gmail isn't usable it shows the "Reconnect Gmail" banner, which signs in again.
