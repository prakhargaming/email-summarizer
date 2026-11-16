from tests.conftest import auth


def test_connect_then_status(client):
    assert client.get("/api/gmail/status", headers=auth()).json() == {"connected": False, "needs_reauth": False}
    r = client.post("/api/gmail/connect", headers={**auth(), "X-Gmail-Refresh-Token": "rt-alice"})
    assert r.json() == {"connected": True}
    assert "rt-alice" not in r.text
    assert client.get("/api/gmail/status", headers=auth()).json() == {"connected": True, "needs_reauth": False}


def test_connect_rejects_empty(client):
    r = client.post("/api/gmail/connect", headers={**auth(), "X-Gmail-Refresh-Token": "  "})
    assert r.status_code == 400 and "log in again" in r.json()["detail"]
    assert client.post("/api/gmail/connect", headers=auth()).status_code == 400


def test_user_isolation(client):
    client.post("/api/gmail/connect", headers={**auth(), "X-Gmail-Refresh-Token": "rt-alice"})
    bob = auth("bob@example.com")
    assert client.get("/api/gmail/status", headers=bob).json() == {"connected": False, "needs_reauth": False}
