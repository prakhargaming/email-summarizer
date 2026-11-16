from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_session
from app.db.models import User


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str


# Trusted only because FastAPI is reachable solely through ngrok, which strips any client copy of this header.
def current_user(
    db: Annotated[Session, Depends(get_session)],
    x_user_email: Annotated[str | None, Header()] = None,
) -> Principal:
    email = (x_user_email or settings.dev_user_email or "").strip().lower()
    if not email:
        raise HTTPException(401, "unauthenticated")
    if email not in settings.allowed_emails:
        raise HTTPException(403, "forbidden")
    user_id = db.scalar(select(User.id).where(User.email == email))
    if user_id is None:
        db.add(User(email=email))
        try:
            db.commit()
        except IntegrityError:  # a concurrent first request created the row
            db.rollback()
        user_id = db.scalar(select(User.id).where(User.email == email))
    return Principal(user_id=user_id, email=email)
