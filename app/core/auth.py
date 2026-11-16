from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
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


def _verified_email(authorization: str | None) -> str:
    """The email from the short-lived token the Next.js proxy signs. Nothing else from the caller is trusted."""
    if not authorization:
        return settings.dev_user_email or ""  # only ever set when ENV=dev
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    try:
        claims = jwt.decode(
            token, settings.api_signing_secret, algorithms=["HS256"], leeway=10, options={"require": ["exp", "sub"]}
        )
    except jwt.InvalidTokenError:
        return ""
    return claims["sub"]


def current_user(
    db: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    email = _verified_email(authorization).strip().lower()
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
