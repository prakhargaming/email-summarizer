from typing import Annotated
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import Principal, current_user
from app.db import get_session
from app.db.models import AgentSettings

router = APIRouter(prefix="/api/settings")

User = Annotated[Principal, Depends(current_user)]
Db = Annotated[Session, Depends(get_session)]
TIMEZONES = available_timezones()


class SettingsIn(BaseModel):
    focus_text: str = Field(max_length=2000)
    timezone: str
    notify_hour_local: int = Field(ge=0, le=23)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        if v not in TIMEZONES:
            raise ValueError("unknown timezone")
        return v


def _row(db: Session, user: Principal) -> AgentSettings:
    row = db.get(AgentSettings, user.user_id)
    if row is None:
        db.add(AgentSettings(user_id=user.user_id))
        try:
            db.commit()
        except IntegrityError:  # a concurrent first request created the row
            db.rollback()
        row = db.get(AgentSettings, user.user_id)
    return row


def _out(row: AgentSettings) -> dict:
    return {"focus_text": row.focus_text or "", "timezone": row.timezone, "notify_hour_local": row.notify_hour_local}


@router.get("")
def get_settings(user: User, db: Db) -> dict:
    return _out(_row(db, user))


@router.put("")
def put_settings(body: SettingsIn, user: User, db: Db) -> dict:
    row = _row(db, user)
    row.focus_text, row.timezone, row.notify_hour_local = body.focus_text, body.timezone, body.notify_hour_local
    db.commit()
    return _out(row)
