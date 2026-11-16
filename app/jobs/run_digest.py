import argparse
import json
import logging
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.db.models import AgentRun, AgentSettings, GmailCredential, User
from app.pipeline.digest import run_for_user

LOCK_KEY = 0x7472696167  # arbitrary, unique to this job
DEFAULT_TZ = "America/New_York"
DEFAULT_HOUR = 7

# (user_id, agent_settings.timezone, agent_settings.notify_hour_local, started_at of the latest success/partial run)
Candidate = tuple[UUID, str | None, int | None, datetime | None]


def due_users(now: datetime, rows: Iterable[Candidate]) -> list[UUID]:
    """Users at or past their notify hour, local time, with no success/partial run yet on their local today."""
    due = []
    for user_id, tz_name, hour, last_ok in rows:
        tz = ZoneInfo(tz_name or DEFAULT_TZ)
        local = now.astimezone(tz)
        if local.hour < (DEFAULT_HOUR if hour is None else hour):
            continue
        if last_ok is not None:
            last_ok = last_ok if last_ok.tzinfo else last_ok.replace(tzinfo=UTC)  # SQLite drops the offset
            if last_ok.astimezone(tz).date() == local.date():
                continue
        due.append(user_id)
    return due


def _connected_users():
    return (select(User.id).join(GmailCredential, GmailCredential.user_id == User.id)
            .where(or_(GmailCredential.needs_reauth.is_(False), GmailCredential.needs_reauth.is_(None))))


def candidates(db: Session) -> list[Candidate]:
    last_ok = (select(AgentRun.user_id, func.max(AgentRun.started_at).label("at"))
               .where(AgentRun.status.in_(("success", "partial"))).group_by(AgentRun.user_id).subquery())
    q = (_connected_users()
         .add_columns(AgentSettings.timezone, AgentSettings.notify_hour_local, last_ok.c.at)
         .outerjoin(AgentSettings, AgentSettings.user_id == User.id)
         .outerjoin(last_ok, last_ok.c.user_id == User.id))
    return [tuple(r) for r in db.execute(q)]


def try_lock(db: Session) -> bool:
    # Transaction-scoped, not session-scoped: through Supabase's transaction pooler a session lock can stay
    # stuck on a pooled backend after we disconnect. The caller keeps this transaction open for the whole job.
    return db.scalar(text("select pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", help="run now for this user, ignoring the notify hour and today's runs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    with SessionLocal() as lock_db:
        if args.email:
            user_ids = lock_db.scalars(_connected_users().where(User.email == args.email.strip().lower())).all()
        elif not try_lock(lock_db):
            logging.info(json.dumps({"event": "job_skipped", "reason": "already_running"}))
            return 0
        else:
            user_ids = due_users(datetime.now(UTC), candidates(lock_db))

        failed = 0
        for uid in user_ids:
            try:
                with SessionLocal() as db:
                    failed += run_for_user(db, uid).status == "failed"
            except Exception as e:  # e.g. the DB dropped mid-run; keep going for the other users
                logging.info(json.dumps({"event": "run_crashed", "user_id": str(uid), "error_type": type(e).__name__}))
                failed += 1
    logging.info(json.dumps({"event": "job_finished", "users": len(user_ids), "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
