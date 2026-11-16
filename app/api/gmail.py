from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.auth import Principal, current_user
from app.db import get_session
from app.db.models import GmailCredential

router = APIRouter(prefix="/api/gmail")

User = Annotated[Principal, Depends(current_user)]
Db = Annotated[Session, Depends(get_session)]


# Set by the Traffic Policy on this path only; no other endpoint reads it.
@router.post("/connect")
def connect(user: User, db: Db, x_gmail_refresh_token: Annotated[str, Header()] = "") -> dict:
    token = x_gmail_refresh_token.strip()
    if not token:
        raise HTTPException(400, "no_refresh_token: log out and log in again to grant Gmail access")
    if len(token) > 2048:
        raise HTTPException(400, "refresh_token_too_long")
    db.merge(GmailCredential(
        user_id=user.user_id,
        refresh_token_encrypted=crypto.encrypt(token),
        needs_reauth=False,
    ))
    db.commit()
    return {"connected": True}


@router.get("/status")
def status(user: User, db: Db) -> dict:
    cred = db.get(GmailCredential, user.user_id)
    return {"connected": cred is not None, "needs_reauth": bool(cred and cred.needs_reauth)}
