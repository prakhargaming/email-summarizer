import json
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app import llm, push
from app.core import crypto
from app.db.models import ActionItem, AgentRule, AgentRun, AgentSettings, GmailCredential
from app.pipeline import gmail
from app.pipeline.prompt import build_system_instruction, render_emails

BATCH_SIZE = 20
MAX_RULES = 50
MAX_EXAMPLES = 10
log = logging.getLogger("digest")


class ExtractedItem(BaseModel):
    message_id: str
    summary: str
    why_it_matters: str
    suggested_action: str
    priority: int = Field(ge=1, le=3)
    due_hint: str | None = None


class Extraction(BaseModel):
    items: list[ExtractedItem]


def _log(event: str, **fields) -> None:
    log.info(json.dumps({"event": event, **fields}))


def run_for_user(db: Session, user_id: UUID) -> AgentRun:
    """Never raises: every outcome is recorded on the returned agent_runs row."""
    run = AgentRun(user_id=user_id)
    db.add(run)
    db.commit()
    ctx = {"run_id": str(run.id), "user_id": str(user_id)}
    stats = {"prompt_tokens": 0, "output_tokens": 0, "fetch_ms": 0, "llm_ms": 0}
    t0 = time.monotonic()
    reconnect = False
    try:
        run.status, run.error_code = _run(db, run, ctx, stats)
    except gmail.InvalidGrant:
        reconnect = db.scalar(
            update(GmailCredential)
            .where(GmailCredential.user_id == user_id, GmailCredential.needs_reauth.is_not(True))
            .values(needs_reauth=True)
            .returning(GmailCredential.user_id)
        ) is not None
        run.status, run.error_code = "failed", "invalid_grant"
    except Exception as e:
        db.rollback()
        # Type name only: exception messages (e.g. pydantic's) can echo email-derived content.
        _log("run_error", **ctx, error_type=type(e).__name__)
        run.status = "failed"
        run.error_code = "invalid_llm_output" if isinstance(e, ValidationError) else "unexpected_error"
    run.finished_at = datetime.now(UTC)
    db.commit()
    _log("run_finished", **ctx, status=run.status, error_code=run.error_code,
         emails_seen=run.emails_seen, items_created=run.items_created,
         total_ms=int((time.monotonic() - t0) * 1000), **stats)
    try:
        push.send_digest(db, user_id, run.items_created if run.status != "failed" else 0, reconnect)
    except Exception as exc:
        db.rollback()
        _log("push_error", **ctx, error_type=type(exc).__name__)
    return run


def _run(db: Session, run: AgentRun, ctx: dict, stats: dict) -> tuple[str, str | None]:
    started = datetime.now(UTC)
    settings = db.get(AgentSettings, run.user_id)
    if settings is None:
        settings = AgentSettings(user_id=run.user_id, focus_text="")
        db.add(settings)
    cred = db.get(GmailCredential, run.user_id)
    if cred is None:
        return "failed", "gmail_not_connected"
    access_token = gmail.refresh_access_token(crypto.decrypt(cred.refresh_token_encrypted))

    since = settings.last_run_at or started - timedelta(hours=24)
    t = time.monotonic()
    emails, cap_hit = gmail.fetch_emails(access_token, int(since.timestamp()))
    stats["fetch_ms"] = int((time.monotonic() - t) * 1000)
    run.emails_seen = len(emails)

    system = build_system_instruction(settings.focus_text or "", *prompt_context(db, run.user_id))
    t = time.monotonic()
    picked, blocked = _extract_all(system, emails, ctx, stats)
    stats["llm_ms"] = int((time.monotonic() - t) * 1000)

    run.items_created = _insert(db, run, picked)
    settings.last_run_at = started
    if blocked:
        return "partial", "llm_blocked"
    if cap_hit:
        return "partial", "email_cap_hit"
    return "success", None


def prompt_context(db: Session, user_id: UUID) -> tuple[list[AgentRule], list[ActionItem]]:
    """Her active rules (newest first, capped) and her most recent decided items."""
    rules = db.scalars(select(AgentRule).where(AgentRule.user_id == user_id, AgentRule.active)
                       .order_by(AgentRule.created_at.desc()).limit(MAX_RULES)).all()
    decided = db.scalars(select(ActionItem).where(ActionItem.user_id == user_id, ActionItem.status != "pending",
                                                  ActionItem.decided_at.is_not(None))
                         .order_by(ActionItem.decided_at.desc()).limit(MAX_EXAMPLES)).all()
    return list(rules), list(decided)


def _extract_all(system, emails, ctx, stats) -> tuple[dict[str, tuple[ExtractedItem, gmail.Email]], bool]:
    """Returns the highest-priority item per thread, each paired with OUR email (never model-supplied fields)."""
    by_thread: dict[str, tuple[ExtractedItem, gmail.Email]] = {}
    blocked = False

    def call(batch: list[gmail.Email]) -> None:
        text, usage = llm.generate_json(system, render_emails(batch), Extraction)
        stats["prompt_tokens"] += usage["prompt_tokens"] or 0
        stats["output_tokens"] += usage["output_tokens"] or 0
        ours = {e.message_id: e for e in batch}
        for item in Extraction.model_validate_json(text).items:
            email = ours.get(item.message_id)
            if email is None:  # hallucinated or injected id from outside this batch
                continue
            prev = by_thread.get(email.thread_id)
            if prev is None or item.priority < prev[0].priority:
                by_thread[email.thread_id] = (item, email)

    for i in range(0, len(emails), BATCH_SIZE):
        batch = emails[i:i + BATCH_SIZE]
        try:
            call(batch)
        except llm.LLMBlocked as e:
            _log("llm_blocked", **ctx, code=e.code, batch_size=len(batch))
            mid = (len(batch) + 1) // 2
            for half in (batch[:mid], batch[mid:]):
                if not half:
                    continue
                try:
                    call(half)
                except llm.LLMBlocked as e2:
                    _log("llm_blocked", **ctx, code=e2.code, batch_size=len(half), retry=True)
                    blocked = True
    return by_thread, blocked


def _insert(db: Session, run: AgentRun, picked: dict[str, tuple[ExtractedItem, gmail.Email]]) -> int:
    if not picked:
        return 0
    insert = postgresql.insert if db.get_bind().dialect.name == "postgresql" else sqlite.insert
    rows = [{
        "user_id": run.user_id, "run_id": run.id,
        "gmail_thread_id": email.thread_id, "gmail_message_id": email.message_id,
        "sender": email.sender, "subject": email.subject,
        "summary": item.summary, "why_it_matters": item.why_it_matters,
        "suggested_action": item.suggested_action, "priority": item.priority, "due_hint": item.due_hint,
    } for item, email in picked.values()]
    stmt = (insert(ActionItem).values(rows)
            .on_conflict_do_nothing(index_elements=["user_id", "gmail_thread_id"])
            .returning(ActionItem.id))
    return len(db.execute(stmt).all())
