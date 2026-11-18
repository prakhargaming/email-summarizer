import base64
import re
from typing import Annotated
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app import push
from app.core.auth import Principal, current_user
from app.db import get_session
from app.db.models import PushSubscription

router = APIRouter(prefix="/api/push")

User = Annotated[Principal, Depends(current_user)]
Db = Annotated[Session, Depends(get_session)]


class Endpoint(BaseModel):
    endpoint: str = Field(max_length=2048, pattern=r"^[!-~]+$")

    @field_validator("endpoint")
    @classmethod
    def _provider_url(cls, endpoint: str) -> str:
        url = urlsplit(endpoint)
        host = url.hostname or ""
        known_provider = host in {"fcm.googleapis.com", "updates.push.services.mozilla.com"}
        apple = re.fullmatch(r"(?:[a-z0-9-]+\.)+push\.apple\.com", host)
        if (
            not endpoint.startswith("https://") or url.netloc != host
            or not url.path.startswith("/") or "#" in endpoint or "\\" in endpoint
            or not (known_provider or apple)
        ):
            raise ValueError("Expected an HTTPS endpoint from a supported push provider")
        return endpoint


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(pattern=r"^[A-Za-z0-9_-]{87}$")
    auth: str = Field(pattern=r"^[A-Za-z0-9_-]{22}$")

    @field_validator("p256dh")
    @classmethod
    def _public_key(cls, key: str) -> str:
        raw = base64.urlsafe_b64decode(key + "=")
        if len(raw) != 65 or raw[0] != 4:
            raise ValueError("Expected an uncompressed P-256 public key")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        return key


class Subscription(Endpoint):
    keys: SubscriptionKeys


def _owned_subscription(db: Session, user: Principal, endpoint: str) -> PushSubscription | None:
    return db.scalar(select(PushSubscription).where(
        PushSubscription.endpoint == endpoint, PushSubscription.user_id == user.user_id,
    ))


@router.put("/subscription", status_code=204)
def subscribe(body: Subscription, user: User, db: Db) -> None:
    if not push.configured():
        raise HTTPException(503, "push_not_configured")
    insert = postgresql.insert if db.get_bind().dialect.name == "postgresql" else sqlite.insert
    keys = body.keys.model_dump()
    saved = db.scalar(
        insert(PushSubscription)
        .values(endpoint=body.endpoint, user_id=user.user_id, **keys)
        .on_conflict_do_update(
            index_elements=[PushSubscription.endpoint],
            set_=keys,
            where=PushSubscription.user_id == user.user_id,
        )
        .returning(PushSubscription.endpoint)
    )
    if saved is None:
        raise HTTPException(409, "subscription_in_use")
    db.commit()


@router.delete("/subscription", status_code=204)
def unsubscribe(body: Endpoint, user: User, db: Db) -> None:
    db.execute(delete(PushSubscription).where(
        PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.user_id,
    ))
    db.commit()


@router.post("/status", status_code=204)
def subscription_status(body: Endpoint, user: User, db: Db) -> None:
    if _owned_subscription(db, user, body.endpoint) is None:
        raise HTTPException(404, "subscription_missing")


@router.post("/test", status_code=204)
def test_notification(body: Endpoint, user: User, db: Db) -> Response:
    subscription = _owned_subscription(db, user, body.endpoint)
    if subscription is None:
        raise HTTPException(410, "subscription_missing")
    return Response(status_code=push.send_test(db, subscription))
