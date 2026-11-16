import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, settings
from app.db.models import User
from tests.conftest import auth


def test_no_header_401(client):
    assert client.get("/api/gmail/status").status_code == 401


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
    assert client.get("/api/gmail/status", headers=auth("eve@example.com")).status_code == 403, "header wins"


def test_dev_user_email_requires_dev_env(monkeypatch):
    monkeypatch.setenv("DEV_USER_EMAIL", "bob@example.com")
    with pytest.raises(ValidationError, match="ENV=dev"):
        Settings(env="prod")
    assert Settings(env="dev").dev_user_email == "bob@example.com"
