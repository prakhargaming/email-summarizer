import jwt
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, settings
from app.db.models import User
from tests.conftest import auth, token


def test_no_header_401(client):
    assert client.get("/api/gmail/status").status_code == 401


@pytest.mark.parametrize("headers", [
    {"X-User-Email": "alice@example.com"},  # the old ngrok header is not trusted any more
    {"Authorization": f"Bearer {token(secret='x' * 32)}"},
    {"Authorization": f"Bearer {token(ttl=-60)}"},
    {"Authorization": f"Basic {token()}"},
    {"Authorization": "Bearer " + jwt.encode({"sub": "alice@example.com"}, "s" * 32, algorithm="HS256")},
    {"Authorization": "Bearer garbage"},
], ids=["forged-header", "wrong-secret", "expired", "wrong-scheme", "no-exp", "garbage"])
def test_untrusted_callers_401(client, headers):
    assert client.get("/api/gmail/status", headers=headers).status_code == 401


def test_unsigned_token_401(client):
    unsigned = jwt.encode({"sub": "alice@example.com", "exp": 2**31}, None, algorithm="none")
    assert client.get("/api/gmail/status", headers={"Authorization": f"Bearer {unsigned}"}).status_code == 401


def test_allowlisted_200_and_case_insensitive(client):
    assert client.get("/api/gmail/status", headers=auth()).status_code == 200
    assert client.get("/api/gmail/status", headers=auth("ALICE@example.com")).status_code == 200


def test_not_allowlisted_403(client, db):
    assert client.get("/api/gmail/status", headers=auth("eve@example.com")).status_code == 403
    with db() as s:
        assert s.scalars(select(User)).all() == [], "rejected users must not be created"


def test_user_upserted_once(client, db):
    client.get("/api/gmail/status", headers=auth())
    client.get("/api/gmail/status", headers=auth("Alice@Example.com"))
    with db() as s:
        assert [u.email for u in s.scalars(select(User))] == ["alice@example.com"]


def test_dev_user_email_fallback(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_user_email", "bob@example.com")
    assert client.get("/api/gmail/status").status_code == 200
    assert client.get("/api/gmail/status", headers=auth("eve@example.com")).status_code == 403, "token wins"
    bad = {"Authorization": f"Bearer {token(ttl=-60)}"}
    assert client.get("/api/gmail/status", headers=bad).status_code == 401, "a bad token never falls back"


def test_dev_user_email_requires_dev_env(monkeypatch):
    monkeypatch.setenv("DEV_USER_EMAIL", "bob@example.com")
    with pytest.raises(ValidationError, match="ENV=dev"):
        Settings(env="prod")
    assert Settings(env="dev").dev_user_email == "bob@example.com"
