import json
import logging
from uuid import UUID

import requests
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import PushSubscription

log = logging.getLogger("push")


class PushSession(requests.Session):
    def request(self, *args, **kwargs):
        # Keep delivery on the push provider host checked at subscription time.
        kwargs["allow_redirects"] = False
        return super().request(*args, **kwargs)


def configured() -> bool:
    return bool(settings.vapid_private_key and settings.vapid_subject)


def _send(db: Session, subscription: PushSubscription, body: str, ttl: int) -> int:
    if not configured():
        return 503

    endpoint, user_id = subscription.endpoint, subscription.user_id
    keys = {"p256dh": subscription.p256dh, "auth": subscription.auth}
    try:
        with PushSession() as session:
            webpush(
                {"endpoint": endpoint, "keys": keys},
                json.dumps({"title": "Email triage", "body": body, "url": "/"}),
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
                requests_session=session,
                timeout=5,
                ttl=ttl,
            )
    except WebPushException as exc:
        if exc.status_code in (404, 410):
            db.execute(delete(PushSubscription).where(
                PushSubscription.endpoint == endpoint,
                PushSubscription.user_id == user_id,
                PushSubscription.p256dh == keys["p256dh"],
                PushSubscription.auth == keys["auth"],
            ))
            db.commit()
            return 410
        log.warning("push_rejected status=%s", exc.status_code)
        return 502
    except Exception as exc:
        # Provider exceptions can include the subscription endpoint.
        log.warning("push_failed error_type=%s", type(exc).__name__)
        return 502
    return 204


def send_test(db: Session, subscription: PushSubscription) -> int:
    return _send(db, subscription, "Test notification from Email triage.", ttl=60)


def send_digest(db: Session, user_id: UUID, items_created: int, reconnect: bool) -> None:
    if not configured() or not (items_created or reconnect):
        return

    if reconnect:
        body = "Reconnect Gmail to resume your daily digest."
    elif items_created == 1:
        body = "1 new item needs your attention."
    else:
        body = f"{items_created} new items need your attention."

    subscriptions = db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)).all()
    for subscription in subscriptions:
        _send(db, subscription, body, ttl=86400)
