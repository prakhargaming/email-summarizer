import pytest
from sqlalchemy import select

from app.db.models import ActionItem, User
from tests.conftest import auth


@pytest.fixture
def item(client, db):
    client.get("/api/items", headers=auth())  # upserts alice
    with db() as s:
        alice = s.scalars(select(User.id).where(User.email == "alice@example.com")).one()
        it = ActionItem(user_id=alice, gmail_thread_id="t1", subject="Invoice", priority=1)
        s.add(it)
        s.commit()
        return str(it.id)


def decide(client, item_id, decision, **kw):
    return client.post(f"/api/items/{item_id}/decision", headers=kw.pop("headers", auth()),
                       json={"decision": decision, **kw})


def test_full_lifecycle_through_every_allowed_move(client, item):
    path = ["accepted", "done", "accepted", "pending", "rejected", "pending"]
    for status in path:
        r = decide(client, item, status)
        assert r.status_code == 200, (status, r.text)
        assert r.json()["status"] == status
        assert [i["id"] for i in client.get(f"/api/items?status={status}", headers=auth()).json()] == [item]


def test_note_saved_and_cleared_when_back_to_pending(client, item, db):
    r = decide(client, item, "accepted", note="pay by friday")
    assert r.json()["decision_note"] == "pay by friday"
    assert decide(client, item, "done").json()["decision_note"] == "pay by friday", "no note keeps the old one"
    with db() as s:
        assert s.get(ActionItem, s.scalars(select(ActionItem.id)).one()).decided_at is not None
    decide(client, item, "accepted")
    r = decide(client, item, "pending")
    assert r.json()["decision_note"] is None
    with db() as s:
        assert s.scalars(select(ActionItem)).one().decided_at is None


@pytest.mark.parametrize("path", [["done"], ["pending"], ["rejected", "accepted"], ["rejected", "done"],
                                  ["accepted", "rejected"], ["accepted", "done", "pending"]])
def test_forbidden_moves_409(client, item, path):
    *setup, bad = path
    for status in setup:
        assert decide(client, item, status).status_code == 200
    r = decide(client, item, bad)
    assert r.status_code == 409


def test_other_users_item_404(client, item):
    assert decide(client, item, "accepted", headers=auth("bob@example.com")).status_code == 404
    assert decide(client, "00000000-0000-0000-0000-000000000000", "accepted").status_code == 404
    assert client.get("/api/items", headers=auth()).json()[0]["status"] == "pending"


@pytest.mark.parametrize("body", [{"decision": "maybe"}, {"decision": "accepted", "note": "x" * 501}])
def test_decision_body_validated(client, item, body):
    assert client.post(f"/api/items/{item}/decision", headers=auth(), json=body).status_code == 422


DEFAULTS = {"focus_text": "", "timezone": "America/New_York", "notify_hour_local": 7}


def test_settings_defaults_then_round_trip_per_user(client):
    assert client.get("/api/settings", headers=auth()).json() == DEFAULTS
    new = {"focus_text": "school and bills", "timezone": "Europe/Paris", "notify_hour_local": 0}
    assert client.put("/api/settings", headers=auth(), json=new).json() == new
    assert client.get("/api/settings", headers=auth()).json() == new
    assert client.get("/api/settings", headers=auth("bob@example.com")).json() == DEFAULTS


@pytest.mark.parametrize("patch", [{"timezone": "Mars/Base"}, {"timezone": ""}, {"timezone": "../etc/passwd"},
                                   {"notify_hour_local": 24}, {"notify_hour_local": -1},
                                   {"focus_text": "x" * 2001}])
def test_settings_validation_422(client, patch):
    assert client.put("/api/settings", headers=auth(), json={**DEFAULTS, **patch}).status_code == 422
    assert client.get("/api/settings", headers=auth()).json() == DEFAULTS
