import uuid
from urllib.parse import quote
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_user
from app.db import get_session
from app.db.models import ActionItem
from app.pipeline import rules

router = APIRouter(prefix="/api/items")

User = Annotated[Principal, Depends(current_user)]
Db = Annotated[Session, Depends(get_session)]
Status = Literal["pending", "accepted", "rejected", "done"]

# Every legal (from, to) move. Moving back to pending is the undo/restore path.
TRANSITIONS: set[tuple[str, str]] = {
    ("pending", "accepted"), ("pending", "rejected"),
    ("accepted", "done"), ("done", "accepted"),
    ("accepted", "pending"), ("rejected", "pending"),
}


class Decision(BaseModel):
    decision: Status
    note: str | None = Field(None, max_length=500)
    remember: bool = False


def _out(i: ActionItem, email: str) -> dict:
    return {
        "id": str(i.id), "sender": i.sender, "subject": i.subject, "summary": i.summary,
        "why_it_matters": i.why_it_matters, "suggested_action": i.suggested_action,
        "priority": i.priority, "due_hint": i.due_hint, "status": i.status,
        "decision_note": i.decision_note,
        "gmail_thread_id": i.gmail_thread_id, "created_at": i.created_at,
        # authuser picks the right inbox when several Google accounts are signed in.
        "gmail_url": f"https://mail.google.com/mail/?authuser={quote(email)}#all/{i.gmail_thread_id}",
    }


@router.get("")
def list_items(user: User, db: Db, status: Status = "pending") -> list[dict]:
    q = (select(ActionItem).where(ActionItem.user_id == user.user_id, ActionItem.status == status)
         .order_by(ActionItem.priority, ActionItem.created_at.desc()))
    return [_out(i, user.email) for i in db.scalars(q)]


@router.post("/{item_id}/decision")
def decide(item_id: uuid.UUID, body: Decision, user: User, db: Db) -> dict:
    item = db.scalar(select(ActionItem).where(ActionItem.id == item_id, ActionItem.user_id == user.user_id))
    if item is None:
        raise HTTPException(404, "item_not_found")
    if (item.status, body.decision) not in TRANSITIONS:
        raise HTTPException(409, f"cannot_move_{item.status}_to_{body.decision}")
    if body.remember and body.decision == "pending":
        raise HTTPException(422, "nothing_to_remember")
    item.status = body.decision
    if body.decision == "pending":
        item.decided_at, item.decision_note = None, None
    else:
        item.decided_at = datetime.now(timezone.utc)
        if body.note is not None:
            item.decision_note = body.note.strip() or None
    db.commit()
    out = _out(item, user.email)
    if not body.remember:
        return out
    # The decision is already committed, so a failed suggestion never loses it.
    try:
        return {**out, "proposed_rule": rules.distill(item, item.decision_note).model_dump(), "rule_error": None}
    except Exception as e:
        return {**out, "proposed_rule": None, "rule_error": rules.error_code("distill", e)}
