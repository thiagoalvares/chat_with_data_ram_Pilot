"""
Encryption for stored API keys — Fernet (AES-128-CBC + HMAC), key from .env.

Lazy initialization: a missing/invalid ENCRYPTION_KEY must NOT crash the whole
service at import time. Endpoints that need encryption get a clear error
instead; everything else keeps working.

Generate a key once and put it in python-service/.env:
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
NOTE: if the key is ever lost/changed, stored API keys become undecryptable and
users simply re-enter theirs.
"""

from cryptography.fernet import Fernet

from config import Config
from logger import logger

_cipher = None


class CryptoNotConfigured(Exception):
    pass


def _get_cipher() -> Fernet:
    global _cipher
    if _cipher is None:
        if not Config.ENCRYPTION_KEY:
            raise CryptoNotConfigured(
                "ENCRYPTION_KEY is not set in python-service/.env. Generate one with: "
                "python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        try:
            _cipher = Fernet(Config.ENCRYPTION_KEY.encode())
        except Exception as e:
            raise CryptoNotConfigured(f"ENCRYPTION_KEY is invalid: {e}")
    return _cipher


def is_configured() -> bool:
    try:
        _get_cipher()
        return True
    except CryptoNotConfigured:
        return False


def encrypt_api_key(plain_key: str) -> str:
    return _get_cipher().encrypt(plain_key.encode()).decode()


def decrypt_api_key(encrypted_key: str) -> str:
    return _get_cipher().decrypt(encrypted_key.encode()).decode()


def mask_api_key(api_key: str) -> str:
    """sk-abc123def456 -> sk-****f456 (never expose full keys in UI/logs)."""
    if len(api_key) <= 7:
        return "****"
    return f"{api_key[:3]}****{api_key[-4:]}"
