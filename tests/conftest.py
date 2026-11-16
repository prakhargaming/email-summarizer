import base64
import os

os.environ.update(
    DATABASE_URL="postgresql+psycopg://u:p@localhost:1/db",
    GOOGLE_CLIENT_ID="cid",
    GOOGLE_CLIENT_SECRET="csecret",
    TOKEN_ENCRYPTION_KEY=base64.b64encode(b"k" * 32).decode(),
    ALLOWED_EMAILS="Alice@Example.com, bob@example.com",
    ENV="test",
    LLM_MODEL="test-model",
    LLM_BASE_URL="https://gateway.test",
    LLM_API_KEY="ngrok-key",
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.db.models import Base
from app.main import app


def auth(email="alice@example.com"):
    return {"X-User-Email": email}


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(engine)


@pytest.fixture
def client(db):
    def session():
        with db() as s:
            yield s

    app.dependency_overrides[get_session] = session
    yield TestClient(app)
    app.dependency_overrides.clear()
