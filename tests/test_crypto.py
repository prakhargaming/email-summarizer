import pytest
from cryptography.exceptions import InvalidTag

from app.core import crypto


def test_roundtrip():
    blob = crypto.encrypt("1//refresh-token")
    assert b"refresh-token" not in blob
    assert crypto.decrypt(blob) == "1//refresh-token"
    assert crypto.encrypt("x") != crypto.encrypt("x"), "nonce must be random"


def test_tamper_fails():
    blob = bytearray(crypto.encrypt("secret"))
    blob[-1] ^= 1
    with pytest.raises(InvalidTag):
        crypto.decrypt(bytes(blob))
