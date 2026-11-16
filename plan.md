# Project: Gmail Action-Item Triage App (single-user, extensible to few users)

## Goal
A private web app that reads one person's Gmail every morning, uses an LLM to extract the emails that need action, and stores them as a persistent list she can Accept or Reject. A web push notification goes out each morning only if there are new action items. Accept/reject decisions can optionally be turned into long-term rules that become part of the agent's system prompt.

## Stack (decided, don't substitute)
- **Backend:** FastAPI (Python 3.12+) on a single AWS EC2 instance, deployed with **ngrok Ship** (see "Hosting: ngrok Ship"). It owns the HTTP API, the Gmail pipeline, LLM calls, and push sending.
- **Python package management:** `uv`. Dependencies are declared in `pyproject.toml` with a committed `uv.lock`; use `uv add` / `uv sync` / `uv run` (no `pip install`, no `requirements.txt`). Pin the Python version with `.python-version`.
- **LLM:** Google Gemini via the official `google-genai` Python SDK, routed through the ngrok AI Gateway.
- **Database:** Supabase, used **only as managed Postgres** (the system of record). Supabase Auth, the Data API (PostgREST), Edge Functions, and pg_cron are not used.
- **Frontend:** Next.js (App Router, TypeScript), also deployed with ngrok Ship on the same EC2 instance. PWA-capable.
- **Hosting + routing:** ngrok Ship (early access) for deploys. An ngrok Traffic Policy on the one public endpoint routes `/api/*` to FastAPI and everything else to Next.js.
- **Login:** Google sign-in happens **only** at the ngrok edge, via the Traffic Policy `oauth` action, which also requests Gmail read-only access. There is no login code in the app.
- **Scheduled job:** A systemd timer (or cron) on the EC2 box runs the digest job as a separate CLI process (Ship doesn't document scheduled jobs).
- **Notifications:** Web Push (VAPID) via a service worker, sent from the backend with `pywebpush`; the app is installable as a PWA.

## Architecture
- **Login happens in one place: the ngrok edge.** Every request to `https://triage.prakhargaming.com` (except a few public paths) must have a valid ngrok Google session. The Traffic Policy checks the email allowlist, then forwards the request with the verified email in an `X-User-Email` header.
- FastAPI reads `X-User-Email`, checks it against `ALLOWED_EMAILS` again, and upserts a row in its own `users` table to get a `user_id`. It trusts this header **only because FastAPI is reachable solely through ngrok**: it has no public port, and the Traffic Policy strips any client-sent copy of the header before adding the real one. If FastAPI is ever exposed any other way, this trust breaks.
- FastAPI is the single API for all reads and writes. It connects to Supabase Postgres directly (use the pooler connection string) and **must filter every query by `user_id`**.
- The browser only talks to `/api/*` on the same origin. It never sees the Gmail token, any database credential, or any LLM key.
- Next.js has no auth code. Pages fetch data from client components with plain same-origin `fetch('/api/...')`, so the ngrok cookie is sent automatically. Next.js server code does not call FastAPI.
- **Local development** (no ngrok): FastAPI accepts a `DEV_USER_EMAIL` setting used in place of the header, only when `ENV=dev` (refuse to start if both `DEV_USER_EMAIL` is set and `ENV` isn't `dev`). `next.config.ts` rewrites `/api/*` to `http://localhost:8000` in dev, so the frontend stays same-origin locally too. To test the real Gmail connect flow locally, run the ngrok agent with the same Traffic Policy.
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

## Hosting: ngrok Ship + Traffic Policy (routing and login)
Ship is early access (https://ngrok.com/ship), so check its docs for anything below that isn't confirmed. Ship builds OCI containers from the repo on every git push and runs them on my own machines via a daemon that only makes an **outbound** connection to ngrok. Traffic reaches the app through the ngrok gateway, so ngrok handles TLS and there are no inbound ports, no Caddy/nginx, and no public IP.

- **One public endpoint, two internal ones:** the public cloud endpoint `https://triage.prakhargaming.com` runs the Traffic Policy below. FastAPI and Next.js run as ngrok **internal endpoints** (e.g. `https://triage-api.internal`, `https://triage-web.internal`), which aren't reachable from the internet. The policy forwards `/api/*` and `/healthz` to FastAPI with `forward-internal`, and everything else to Next.js. A single origin means one login cookie, no CORS, and no cross-origin redirects when a session expires.
- **Google OAuth client (the only one):** in Google Cloud, create one Web client. Use redirect URI `https://idp.ngrok.com/oauth2/callback`, add `ngrok.com` and `prakhargaming.com` as authorized domains, set the app to "In production", and add the scopes `userinfo.email`, `userinfo.profile`, and `gmail.readonly`. FastAPI uses **the same client ID and secret** to refresh Gmail tokens, because Google refresh tokens are tied to the client that issued them.
- **Traffic Policy sketch** (verify field names and expression syntax against the docs: https://ngrok.com/docs/traffic-policy/actions/oauth, https://ngrok.com/docs/integrations/oauth/google-oauth, https://ngrok.com/docs/traffic-policy/actions/forward-internal):
```yaml
on_http_request:
  # Public paths: no login. iOS fetches the manifest and icons without cookies.
  - expressions: ["req.url.path == '/healthz'"]
    actions:
      - type: forward-internal
        config: { url: https://triage-api.internal }
  - expressions: ["req.url.path == '/manifest.webmanifest' || req.url.path.startsWith('/icons/')"]
    actions:
      - type: forward-internal
        config: { url: https://triage-web.internal }

  # Drop any identity or token headers the client tried to send.
  - actions:
      - type: remove-headers
        config: { headers: [x-user-email, x-gmail-refresh-token] }

  # Google login, with Gmail read-only and a refresh token.
  - actions:
      - type: oauth
        config:
          provider: google
          auth_id: triage
          client_id: "<from ngrok secrets>"
          client_secret: "<from ngrok secrets>"
          scopes:
            - https://www.googleapis.com/auth/userinfo.email
            - https://www.googleapis.com/auth/userinfo.profile
            - https://www.googleapis.com/auth/gmail.readonly
          authz_url_params: { prompt: consent }   # ngrok already adds access_type=offline; without consent, repeat logins return no refresh token
          max_session_duration: 720h   # 30 days, so the PWA doesn't log her out daily
          idle_session_timeout: 720h

  # Email allowlist.
  - expressions: ["!(actions.ngrok.oauth.identity.email in ['prakhar530@gmail.com', 'nguyen26.bn@gmail.com'])"]
    actions:
      - type: deny
        config: { status_code: 403 }

  # Pass the verified identity to the app.
  - actions:
      - type: add-headers
        config: { headers: { x-user-email: "${actions.ngrok.oauth.identity.email}" } }

  # Gmail refresh token: only on the connect endpoint, never on other requests.
  - expressions: ["req.url.path == '/api/gmail/connect'"]
    actions:
      - type: add-headers
        config: { headers: { x-gmail-refresh-token: "${actions.ngrok.oauth.refresh_token}" } }

  # Route.
  - expressions: ["req.url.path.startsWith('/api/')"]
    actions:
      - type: forward-internal
        config: { url: https://triage-api.internal }
  - actions:
      - type: forward-internal
        config: { url: https://triage-web.internal }
```
- Keep the client secret out of the repo. Use ngrok's secrets/vault for Traffic Policy values if available; otherwise set it in the dashboard. The committed policy file uses placeholders.
- **Session expiry in the frontend:** when the ngrok session expires, client-side `fetch` calls get a redirect instead of JSON. Wrap `fetch` in one helper that detects this (`res.redirected` or a non-JSON response) and does a full-page navigation to `/ngrok/login?auth_id=triage&redirect_path=<current path>`. "Sign out" is a link to `/ngrok/logout?auth_id=triage`.
- **Service worker:** the script is same-origin and sends cookies. Verify after installing the PWA that push and `notificationclick` still work.
- **Fallback if Ship can't do something we need:** run the plain ngrok agent on the EC2 box (systemd) with the same Traffic Policy file, using agent internal endpoints. Nothing else in the design changes.

## Security and privacy requirements
- **Do not persist email bodies.** Store only sender, subject, the LLM's summary, why it matters, suggested action, the Gmail thread ID, and a deep link back to the thread. Email bodies exist only in memory during a run.
- Do not log email content or raw LLM prompts/responses. Log counts, IDs, timings, token usage, and error codes only (structured JSON logs).
- **Data handling by the model provider:** email content is sent to Google. Requests go through the ngrok AI Gateway on gateway credits, and I've confirmed that ngrok does not train on this data. Note this in the README. If I later switch to my own Google key, it must be a paid-tier key whose terms exclude training on API data, never a free-tier key.
- Gmail refresh token: store encrypted at rest (AES-GCM via `cryptography`, key held in AWS SSM Parameter Store or Secrets Manager, fetched at startup). Never return it from any endpoint.
- **Treat email content as untrusted input (prompt injection).** Wrap each email in clear delimiters, instruct the model that email text is data and never instructions, give the model no tools (no function calling, no grounding, no URL context), and require structured JSON output validated with Pydantic.
- Gmail scope: `https://www.googleapis.com/auth/gmail.readonly` only.
- Restrict access to an allowlist of emails, enforced twice: once at the edge (Traffic Policy `deny`) and again in FastAPI (`ALLOWED_EMAILS`, 403 for anyone else).
- No CORS middleware: the app is single-origin. Don't add a wildcard.
- In Supabase, **disable the Data API** and keep RLS enabled with no policies on every table, so the public anon key can't reach anything. Only FastAPI's direct Postgres connection can read the data.
- Secrets live in SSM Parameter Store / Secrets Manager, read via the EC2 instance's IAM role. No secrets in the repo, AMI, or plain `.env` committed anywhere.

## AWS / EC2 deployment
- One small instance (e.g. t4g.small or t3.small) running Ubuntu LTS with Docker and the **ngrok Ship daemon** installed. Ship builds and runs the FastAPI and Next.js containers. The backend image installs dependencies with `uv sync --frozen --no-dev` so it matches `uv.lock` exactly. Run `uvicorn` in the container; ngrok terminates TLS.
- Security group: **no inbound rules at all** (Ship and the ngrok agent only connect outbound). No public SSH; use SSM Session Manager for shell access.
- IAM instance role with read access to the specific SSM parameters/secrets only. Ship doesn't document a secrets feature, so containers fetch secrets from SSM at startup using the instance role. Set the IMDSv2 hop limit to 2 so containers can reach instance credentials.
- Health endpoint `GET /healthz` (no auth, no secrets, no DB content). Use it as Ship's health check for blue/green rollouts, exempt it from the edge OAuth, and add a simple uptime check on it.
- The digest job runs as a **separate process** via a systemd timer on the host every hour, running the job with the same backend image (e.g. `docker run --rm <backend image> python -m app.jobs.run_digest`). If Ship supports scheduled jobs by then, use that instead. Do *not* use an in-process scheduler like APScheduler inside the API, because multiple uvicorn workers would run the job multiple times.
- Include deployment artifacts: Ship app config (or documented Ship setup steps), the Traffic Policy YAML (with secrets as placeholders), the systemd timer + service for the digest job, and an example environment file with placeholder values.

## Google OAuth and Gmail token
- **Confirmed from the ngrok source code** (by me, an ngrok employee; not yet tested end to end against Google):
  - ngrok always adds `access_type=offline`, so Google issues a refresh token.
  - The edge session keeps the refresh token across access-token refreshes.
  - The token is exposed as `actions.ngrok.oauth.refresh_token`.

  Conditions:
  1. Use **our own Google OAuth client** (`client_id`/`client_secret` set). With ngrok's managed app, custom scopes and `authz_url_params` are rejected, and the tokens are always empty.
  2. **Nothing is forwarded automatically.** In Traffic Policy, the action doesn't inject `ngrok-auth-oauth-*` headers. The policy copies the token into `x-gmail-refresh-token` with `add-headers`, **only for `POST /api/gmail/connect`**, so it isn't sent on every request.
  3. Set `authz_url_params: { prompt: consent }`. Google returns a refresh token only when the consent screen is shown, so without it, a repeat login yields an empty token.
- **Connecting Gmail:** `POST /api/gmail/connect` reads `x-gmail-refresh-token`, rejects an empty value (with a clear error telling her to log out and log in again), encrypts the token, and stores it in `gmail_credentials`. No other endpoint reads that header. The frontend calls connect when `GET /api/gmail/recent` returns 409 (not connected or `needs_reauth`), then retries once; a second 409 shows the "Reconnect Gmail" banner (see Decisions). The token never touches the frontend.
- **Milestone 1 still verifies this end to end:** log in, connect, then use the stored token from FastAPI with the same client ID and secret after the access token has expired. **Fallback** if it fails anyway: a one-time "Connect Gmail" authorization-code flow in FastAPI (`/api/gmail/oauth/start` and `/callback`) using the same client. Login still happens only at ngrok.
- `gmail.readonly` is a Google **restricted** scope. Set the Google Cloud OAuth app to **"In production"**, because in "Testing" mode refresh tokens expire after 7 days and the job will silently fail. The plan assumes it can stay **unverified** for personal use (under 100 users, with Google's "unverified app" warning screen). **Not yet confirmed:** check this against Google's current rules when creating the client. If Google requires verification for a production app with this scope, stop and ask me before falling back to "Testing" mode, which would mean logging in again every 7 days.
- If the refresh exchange returns `invalid_grant`, set `needs_reauth = true`, skip the user, and send a push notification asking her to sign in again. The UI shows a "Reconnect Gmail" banner, which links to `/ngrok/logout` and then `/ngrok/login` (forcing fresh consent) and then calls connect.

## Data model (Postgres, managed with Alembic)
```sql
users(                                   -- created on first request, keyed by the ngrok-verified email
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

## API surface (all require the ngrok-verified `X-User-Email` of an allowlisted user unless noted)
- `GET /healthz` (no auth)
- `POST /api/gmail/connect` (store refresh token), `GET /api/gmail/status`
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
Backend: `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (the same client as the ngrok `oauth` action), `TOKEN_ENCRYPTION_KEY`, `LLM_API_KEY` (ngrok access key), `LLM_BASE_URL` (`https://gateway.ngrok.ai`; leave unset to call Gemini directly), `GEMINI_API_KEY` (only used when `LLM_BASE_URL` is unset), `LLM_MODEL`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (mailto:), `ALLOWED_EMAILS`, `APP_URL` (`https://triage.prakhargaming.com`, used for push click-through links), `ENV`, and `DEV_USER_EMAIL` (dev only).
Frontend: `NEXT_PUBLIC_VAPID_PUBLIC_KEY` only. There's no API base URL, because the API is always same-origin `/api`.
ngrok Traffic Policy (dashboard/Ship, not the app): Google OAuth client ID + secret (same values as `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`), email allowlist.

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
5. **EC2 + Ship deployment + scheduling:** Provision the instance with Terraform, install the Ship daemon, deploy FastAPI and Next.js as internal endpoints behind the one public endpoint and its Traffic Policy, SSM-backed config, the digest systemd timer, local-time gating, and the no-duplicate-run guard. Verify that a non-allowlisted Google account is blocked at the edge, that a spoofed `X-User-Email` from the client is ignored, that `/healthz` and the PWA manifest load without login, that FastAPI isn't reachable except through ngrok, and that the hourly timer runs exactly once per user per day.
6. **Web Push:** subscription flow, notification only when new items exist, `invalid_grant` re-auth notification, stale subscription pruning.
7. **Hardening:** a second allowlisted test user cannot read the first user's data (API tests), and the Supabase anon key can't read any table, error states in the UI, run history view, README with setup steps (Google Cloud console (one OAuth client shared by ngrok and FastAPI), Supabase (Postgres only, Data API off), SSM parameters, gateway/Gemini config, VAPID, EC2, Ship, Traffic Policy, systemd timer).

## Out of scope for now
WhatsApp/Telegram, other mail providers, replying to or sending email, multi-tenant billing, storing email bodies, Supabase Auth, Supabase Data API, Supabase Edge Functions, Gemini tools/grounding, the Interactions API.

## Decisions (answered)
- **Timezone and notification time:** she is in Decatur, GA, so the default timezone is `America/New_York` and the default notification time is 7:00 AM local (`notify_hour_local = 7`). Both are only defaults: the Settings page must let her change the timezone and the notification hour.
- **Infrastructure as code:** use **Terraform** for the AWS setup. Put it in `infra/` and include a short README that explains the `terraform init` / `plan` / `apply` workflow for someone new to Terraform. It should manage the EC2 instance (with `user_data` that installs Docker and the Ship daemon), security group (no inbound), IAM instance role and policy, and SSM parameter *names*. No Elastic IP or DNS: ngrok provides the public address. **Do not put secret values in Terraform**, because they would end up in the state file. Terraform creates SSM parameters with placeholder values and `lifecycle { ignore_changes = [value] }`, and real secrets are set afterwards with `aws ssm put-parameter`. Use a local state file for now (gitignored), and leave remote S3 state as an optional later step.
- **LLM credentials:** Gemini runs on an **ngrok access key using gateway credits** (no Google key of my own attached).
- **Domain:** the app lives at **`triage.prakhargaming.com`**, a single origin for both frontend and API, served through ngrok. `api.prakhargaming.com` is already taken by my personal site's backend, so don't touch it. DNS for `prakhargaming.com` is not in Route 53 (its nameservers are `ns-cloud-d*.googledomains.com`), so I'll add the domain in ngrok and create the `CNAME` record it gives me by hand at my DNS provider. FastAPI and Next.js get no public addresses of their own; ngrok routes by path.
- **AWS account and region:** use my existing AWS account (the same one hosting `api.prakhargaming.com`) in `us-east-2` (Ohio). Use separate, clearly named resources (tag everything `project = email-triage`) so nothing collides with the existing backend.
- **Hosting and login:** ngrok Ship (early access) on the EC2 instance. Google login happens only at the ngrok edge (Traffic Policy `oauth`), and Supabase Auth is dropped. See "Hosting: ngrok Ship + Traffic Policy".
- **Allowed users:** `prakhar530@gmail.com` (me) and `nguyen26.bn@gmail.com` (her). Use these both in the Traffic Policy `deny` rule and in `ALLOWED_EMAILS`.
- **ngrok account:** I work at ngrok, have Ship early access, and am on a corporate plan, so custom domains, internal endpoints, and the `oauth` action with my own Google client are all available. Deploy with Ship directly; the plain-agent setup is only a fallback.
- **Database:** keep Supabase as managed Postgres only. It's free, has backups and a dashboard, and is already set up. It could later be swapped for any Postgres (Neon, RDS) by changing `DATABASE_URL`.
- **Data handling:** I've confirmed that ngrok does not train on the data sent through the gateway. Note this in the README alongside the gateway-credits setup.
- **When the frontend calls connect:** on a 409 from `GET /api/gmail/recent` rather than checking `GET /api/gmail/status` first. It calls `POST /api/gmail/connect` once and retries; only a second 409 shows the "Reconnect Gmail" banner. This saves a request and can't get stuck on the banner after she logs in again with fresh consent. `/status` stays available for other UI.
