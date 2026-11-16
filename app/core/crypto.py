import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings


def encrypt(plaintext: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(settings.token_encryption_key).encrypt(nonce, plaintext.encode(), None)


def decrypt(blob: bytes) -> str:
    return AESGCM(settings.token_encryption_key).decrypt(blob[:12], blob[12:], None).decode()
