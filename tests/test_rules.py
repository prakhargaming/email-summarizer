import json

import pytest
from sqlalchemy import select

from app import llm
from app.db.models import ActionItem, AgentRule
from tests.conftest import auth
from tests.test_items_settings import decide, item  # noqa: F401  (item is a fixture)


@pytest.fixture
def respond(monkeypatch):
    """Set .reply to the LLM's JSON text, or to an exception to raise. Records what the LLM was sent."""
    class Fake:
        reply = None
        sent: list[tuple[str, str]] = []

    def generate_json(system, contents, schema):
        Fake.sent.append((system, contents))
        if isinstance(Fake.reply, Exception):
            raise Fake.reply
        return Fake.reply, {}

    monkeypatch.setattr(llm, "generate_json", generate_json)
    return Fake


def rules_in_db(db):
    with db() as s:
        return [(r.rule_text, r.kind, r.active) for r in s.scalars(select(AgentRule).order_by(AgentRule.rule_text))]


def post_rule(client, text, kind="ignore", headers=None, **kw):
    return client.post("/api/rules", headers=headers or auth(), json={"rule_text": text, "kind": kind, **kw})


def test_remember_returns_proposed_rule_and_saves_nothing(client, db, item, respond):
    respond.reply = json.dumps({"rule_text": "  Invoices from Acme are not action items. ", "kind": "ignore"})
    r = decide(client, item, "rejected", note="handled by accountant", remember=True)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "rejected" and body["rule_error"] is None
    assert body["proposed_rule"] == {"rule_text": "Invoices from Acme are not action items.", "kind": "ignore"}
    [(system, contents)] = respond.sent
    assert "Prefer narrow rules" in system
    assert "<email>\nFrom: \nSubject: Invoice" in contents and "rejected it" in contents
    assert "<note>\nhandled by accountant\n</note>" in contents
    assert rules_in_db(db) == []


@pytest.mark.parametrize("reply, code", [
    (llm.LLMBlocked("finish_safety"), "finish_safety"),
    ('{"rule_text": "' + "x" * 201 + '", "kind": "ignore"}', "invalid_llm_output"),
    ('{"rule_text": "ok", "kind": "always"}', "invalid_llm_output"),
    (ConnectionError("down"), "llm_error"),
])
def test_remember_failure_keeps_decision(client, db, item, respond, reply, code):
    respond.reply = reply
    body = decide(client, item, "accepted", remember=True).json()
    assert (body["status"], body["proposed_rule"], body["rule_error"]) == ("accepted", None, code)
    assert client.get("/api/items?status=accepted", headers=auth()).json()[0]["id"] == item


def test_remember_needs_a_decision(client, item, respond):
    assert decide(client, item, "accepted").status_code == 200
    assert decide(client, item, "pending", remember=True).status_code == 422
    assert client.get("/api/items?status=accepted", headers=auth()).json()[0]["id"] == item
    assert respond.sent == []


def test_rules_crud_and_validation(client, db, item):
    r = post_rule(client, " Prioritize the school district ", "prioritize", source_item_id=item)
    assert r.status_code == 201
    rule = r.json()
    assert (rule["rule_text"], rule["kind"], rule["active"], rule["source_item_id"]) == (
        "Prioritize the school district", "prioritize", True, item)
    older = post_rule(client, "Older rule").json()
    assert post_rule(client, "x" * 201).status_code == 422
    assert post_rule(client, "   ").status_code == 422
    assert post_rule(client, "ok", "always").status_code == 422

    r = client.patch(f"/api/rules/{rule['id']}", headers=auth(), json={"active": False})
    assert r.json()["active"] is False and r.json()["rule_text"] == "Prioritize the school district"
    assert [x["id"] for x in client.get("/api/rules", headers=auth()).json()] == [older["id"], rule["id"]], "active first"
    r = client.patch(f"/api/rules/{rule['id']}", headers=auth(), json={"rule_text": "School first", "kind": "note"})
    assert (r.json()["rule_text"], r.json()["kind"], r.json()["active"]) == ("School first", "note", False)
    assert client.patch(f"/api/rules/{rule['id']}", headers=auth(), json={"kind": "bad"}).status_code == 422

    assert client.delete(f"/api/rules/{rule['id']}", headers=auth()).status_code == 204
    assert rules_in_db(db) == [("Older rule", "ignore", True)]


def test_rules_are_per_user(client, db, item):
    rule = post_rule(client, "Alice's rule").json()
    bob = auth("bob@example.com")
    assert client.get("/api/rules", headers=bob).json() == []
    assert client.patch(f"/api/rules/{rule['id']}", headers=bob, json={"active": False}).status_code == 404
    assert client.delete(f"/api/rules/{rule['id']}", headers=bob).status_code == 404
    assert post_rule(client, "steal", headers=bob, source_item_id=item).status_code == 404, "item is alice's"
    assert rules_in_db(db) == [("Alice's rule", "ignore", True)]


def test_consolidate_proposes_then_put_active_replaces_atomically(client, db, respond):
    for text in ["Ignore Acme promos", "Acme marketing is not actionable", "Prioritize school"]:
        post_rule(client, text)
    client.patch(f"/api/rules/{post_rule(client, 'Old off rule').json()['id']}", headers=auth(), json={"active": False})
    post_rule(client, "Bob's rule", headers=auth("bob@example.com"))

    merged = [{"rule_text": "Acme marketing emails are not action items", "kind": "ignore"},
              {"rule_text": "Prioritize school", "kind": "prioritize"}]
    respond.reply = json.dumps({"rules": merged})
    r = client.post("/api/rules/consolidate", headers=auth())
    assert r.status_code == 200 and r.json() == {"rules": merged}
    [(_, contents)] = respond.sent
    assert "Old off rule" not in contents and "Bob's rule" not in contents and "Ignore Acme promos" in contents
    assert len(rules_in_db(db)) == 5, "consolidate saves nothing"

    r = client.put("/api/rules/active", headers=auth(), json={"rules": merged})
    assert r.status_code == 200 and [x["rule_text"] for x in r.json()] == [m["rule_text"] for m in merged]
    alice = [x for x in rules_in_db(db) if x[0] != "Bob's rule"]
    assert sorted(alice) == sorted([
        ("Acme marketing emails are not action items", "ignore", True), ("Prioritize school", "prioritize", True),
        ("Ignore Acme promos", "ignore", False), ("Acme marketing is not actionable", "ignore", False),
        ("Prioritize school", "ignore", False), ("Old off rule", "ignore", False)])
    assert client.get("/api/rules", headers=auth("bob@example.com")).json()[0]["active"] is True


def test_put_active_is_all_or_nothing(client, db):
    post_rule(client, "Keep me")
    bad = {"rules": [{"rule_text": "fine", "kind": "ignore"}, {"rule_text": "", "kind": "ignore"}]}
    assert client.put("/api/rules/active", headers=auth(), json=bad).status_code == 422
    assert rules_in_db(db) == [("Keep me", "ignore", True)]


@pytest.mark.parametrize("reply", [json.dumps({"rules": []}), json.dumps({"rules": [{"rule_text": "a", "kind": "note"}] * 3}),
                                   llm.LLMBlocked("no_candidates")])
def test_consolidate_bad_llm_output_is_502_and_saves_nothing(client, db, respond, reply):
    post_rule(client, "one")
    post_rule(client, "two")
    respond.reply = reply
    assert client.post("/api/rules/consolidate", headers=auth()).status_code == 502
    assert rules_in_db(db) == [("one", "ignore", True), ("two", "ignore", True)]
