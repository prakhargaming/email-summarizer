from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_user
from app.core.config import settings
from app.db import get_session
from app.db.models import AgentRun
from app.pipeline.digest import run_for_user

router = APIRouter(prefix="/api/runs")

User = Annotated[Principal, Depends(current_user)]
Db = Annotated[Session, Depends(get_session)]


def _out(r: AgentRun) -> dict:
    return {
        "id": str(r.id), "status": r.status, "error_code": r.error_code,
        "emails_seen": r.emails_seen, "items_created": r.items_created,
        "started_at": r.started_at, "finished_at": r.finished_at,
    }


# Sync on purpose: FastAPI runs it in a threadpool, and the pipeline is blocking I/O end to end.
@router.post("/trigger")
def trigger(user: User, db: Db) -> dict:
    if settings.env != "dev":
        raise HTTPException(404)
    return _out(run_for_user(db, user.user_id))


@router.get("")
def recent(user: User, db: Db) -> list[dict]:
    q = select(AgentRun).where(AgentRun.user_id == user.user_id).order_by(AgentRun.started_at.desc()).limit(20)
    return [_out(r) for r in db.scalars(q)]
