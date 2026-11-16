"""Live Milestone 4 check: a saved rule visibly changes the next run's output.

Runs the real extraction over the last 48h (or argv[1] hours) of real mail with and without one extra rule. Rules are built in
memory and nothing is inserted, so the database is only read.
"""
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core import crypto
from app.db import SessionLocal
from app.db.models import AgentRule, AgentSettings, GmailCredential, User
from app.pipeline import digest, gmail
from app.pipeline.prompt import build_system_instruction

EMAIL = "prakhar530@gmail.com"
HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 48


def name(sender: str) -> str:
    return sender.split("<")[0].strip().strip('"') or sender


with SessionLocal() as db:
    uid = db.scalar(select(User.id).where(User.email == EMAIL))
    focus = getattr(db.get(AgentSettings, uid), "focus_text", None) or ""
    rules, decisions = digest.prompt_context(db, uid)
    token = gmail.refresh_access_token(crypto.decrypt(db.get(GmailCredential, uid).refresh_token_encrypted))

emails, cap_hit = gmail.fetch_emails(token, int((datetime.now(UTC) - timedelta(hours=HOURS)).timestamp()))
print(f"window: {HOURS}h; emails fetched: {len(emails)} (cap hit: {cap_hit}); existing active rules: {len(rules)}; examples: {len(decisions)}")


def extract(extra: AgentRule | None) -> dict:
    system = build_system_instruction(focus, ([extra] if extra else []) + rules, decisions)
    picked, blocked = digest._extract_all(system, emails, {"check": "rule_effect"}, {"prompt_tokens": 0, "output_tokens": 0})
    assert not blocked, "an LLM batch was blocked; rerun"
    return {t: (item.priority, name(e.sender)) for t, (item, e) in picked.items()}


def show(label: str, out: dict) -> None:
    print(f"{label}: {len(out)} items")
    for t, (p, who) in sorted(out.items(), key=lambda kv: kv[1][0]):
        print(f"  {t}  p{p}  {who}")


base = extract(None)
show("no extra rule", base)

if base:
    thread, (_, who) = max(base.items(), key=lambda kv: kv[1][0])
    subject = next(e.subject for e in emails if e.thread_id == thread)
    rule = AgentRule(kind="ignore", rule_text=f'Emails from {who} about "{subject}" are not action items.')
    after = extract(rule)
    show(f"with ignore rule on {who} ({thread})", after)
    print(f"targeted item disappeared: {thread not in after}")

skipped = [e for e in emails if e.thread_id not in base]
if skipped:
    e = skipped[0]
    rule = AgentRule(kind="prioritize", rule_text=f'Always treat emails from {name(e.sender)} about "{e.subject}" '
                                                  "as urgent action items (priority 1).")
    after = extract(rule)
    show(f"with prioritize rule on {name(e.sender)} ({e.thread_id})", after)
    print(f"targeted item appeared: {e.thread_id in after}" + (f" at p{after[e.thread_id][0]}" if e.thread_id in after else ""))
else:
    print("every fetched thread was already returned; no non-returned sender to prioritize")
