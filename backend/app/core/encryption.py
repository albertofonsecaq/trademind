"""
Fernet symmetric encryption for platform session credentials.
Key is derived from the app SECRET_KEY so credentials are useless without it.
"""
import base64
import hashlib
from cryptography.fernet import Fernet
from app.core.config import settings

def _fernet() -> Fernet:
    raw = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    key = base64.urlsafe_b64encode(raw)
    return Fernet(key)


def encrypt_credential(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_credential(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
