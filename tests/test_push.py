from base64 import urlsafe_b64encode

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from requests import Response
from requests.adapters import HTTPAdapter

from app.core.config import settings
from tests.conftest import auth


def b64(raw):
    return urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.fixture
def subscription(monkeypatch):
    vapid = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(settings, "vapid_private_key", b64(vapid.private_numbers().private_value.to_bytes(32, "big")))
    monkeypatch.setattr(settings, "vapid_subject", "mailto:push@example.com")
    public_key = ec.generate_private_key(ec.SECP256R1()).public_key()
    return {
        "endpoint": "https://web.push.apple.com/token",
        "keys": {
            "p256dh": b64(public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)),
            "auth": b64(b"a" * 16),
        },
    }


def test_browser_subscription_send_and_prune(client, subscription, monkeypatch):
    provider_status = 201
    deliveries = []

    def respond(_adapter, request, **kwargs):
        deliveries.append(request)
        response = Response()
        response.status_code = provider_status
        response.headers["Location"] = "https://example.com/redirect"
        response._content = b""
        response.url = request.url
        return response

    monkeypatch.setattr(HTTPAdapter, "send", respond)
    endpoint = {"endpoint": subscription["endpoint"]}
    for _ in range(2):
        assert client.put("/api/push/subscription", headers=auth(), json=subscription).status_code == 204
    bob = auth("bob@example.com")
    assert client.put("/api/push/subscription", headers=bob, json=subscription).status_code == 409
    assert client.post("/api/push/test", headers=bob, json=endpoint).status_code == 410
    assert client.request("DELETE", "/api/push/subscription", headers=bob, json=endpoint).status_code == 204
    assert not deliveries

    assert client.post("/api/push/test", headers=auth(), json=endpoint).status_code == 204
    [delivery] = deliveries
    assert delivery.url == endpoint["endpoint"]
    assert delivery.headers["Authorization"].startswith("vapid ")
    assert delivery.headers["Content-Encoding"] == "aes128gcm"
    assert b"Test notification" not in delivery.body

    provider_status = 302
    assert client.post("/api/push/test", headers=auth(), json=endpoint).status_code == 502
    assert len(deliveries) == 2
    assert client.post("/api/push/status", headers=auth(), json=endpoint).status_code == 204

    provider_status = 410
    assert client.post("/api/push/test", headers=auth(), json=endpoint).status_code == 410
    assert client.post("/api/push/status", headers=auth(), json=endpoint).status_code == 404


@pytest.mark.parametrize("endpoint", [
    "http://web.push.apple.com/token",
    "https://web.push.apple.com.example.com/token",
    "https://web.push.apple.com@localhost/token",
    "https://web.push.apple.com:444/token",
])
def test_rejects_untrusted_endpoints(client, subscription, endpoint):
    subscription["endpoint"] = endpoint
    assert client.put("/api/push/subscription", headers=auth(), json=subscription).status_code == 422
