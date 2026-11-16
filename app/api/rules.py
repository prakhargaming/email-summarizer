import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_user
from app.db import get_session
from app.db.models import ActionItem, AgentRule
from app.pipeline import rules
from app.pipeline.rules import Kind, RuleIn, RuleText

router = APIRouter(prefix="/api/rules")

User = Annotated[Principal, Depends(current_user)]
Db = Annotated[Session, Depends(get_session)]


class NewRule(RuleIn):
    source_item_id: uuid.UUID | None = None


class RulePatch(BaseModel):
    rule_text: RuleText | None = None
    kind: Kind | None = None
    active: bool | None = None


class ActiveSet(BaseModel):
    rules: list[RuleIn] = Field(max_length=200)


def _out(r: AgentRule) -> dict:
    return {"id": str(r.id), "rule_text": r.rule_text, "kind": r.kind, "active": r.active,
            "source_item_id": r.source_item_id and str(r.source_item_id), "created_at": r.created_at}


def _active(db: Session, user: Principal) -> list[AgentRule]:
    q = (select(AgentRule).where(AgentRule.user_id == user.user_id, AgentRule.active)
         .order_by(AgentRule.created_at.desc()))
    return list(db.scalars(q))


def _own(db: Session, user: Principal, rule_id: uuid.UUID) -> AgentRule:
    rule = db.scalar(select(AgentRule).where(AgentRule.id == rule_id, AgentRule.user_id == user.user_id))
    if rule is None:
        raise HTTPException(404, "rule_not_found")
    return rule


@router.get("")
def list_rules(user: User, db: Db) -> list[dict]:
    q = (select(AgentRule).where(AgentRule.user_id == user.user_id)
         .order_by(AgentRule.active.desc(), AgentRule.created_at.desc()))
    return [_out(r) for r in db.scalars(q)]


@router.post("", status_code=201)
def create_rule(body: NewRule, user: User, db: Db) -> dict:
    if body.source_item_id and db.scalar(select(ActionItem.id).where(
            ActionItem.id == body.source_item_id, ActionItem.user_id == user.user_id)) is None:
        raise HTTPException(404, "item_not_found")
    rule = AgentRule(user_id=user.user_id, **body.model_dump())
    db.add(rule)
    db.commit()
    return _out(rule)


@router.patch("/{rule_id}")
def patch_rule(rule_id: uuid.UUID, body: RulePatch, user: User, db: Db) -> dict:
    rule = _own(db, user, rule_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(rule, k, v)
    out = _out(rule)  # before commit: re-reading afterwards 500s if a concurrent DELETE removed the row
    db.commit()
    return out


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: uuid.UUID, user: User, db: Db) -> None:
    db.delete(_own(db, user, rule_id))
    db.commit()


@router.post("/consolidate")
def consolidate(user: User, db: Db) -> dict:
    """A proposal only: she reviews it, then confirms with PUT /api/rules/active."""
    active = _active(db, user)
    if len(active) < 2:
        return {"rules": [{"rule_text": r.rule_text, "kind": r.kind} for r in active]}
    try:
        merged = rules.consolidate(active)
    except Exception as e:
        raise HTTPException(502, rules.error_code("consolidate", e))
    return {"rules": [r.model_dump() for r in merged]}


@router.put("/active")
def replace_active(body: ActiveSet, user: User, db: Db) -> list[dict]:
    """Deactivates (keeps) every current active rule and saves the confirmed list, in one transaction."""
    db.execute(update(AgentRule).where(AgentRule.user_id == user.user_id, AgentRule.active).values(active=False))
    new = [AgentRule(user_id=user.user_id, **r.model_dump()) for r in body.rules]
    db.add_all(new)
    db.commit()
    return [_out(r) for r in new]
