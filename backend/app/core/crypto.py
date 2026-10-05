import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class TokenDecryptionError(Exception):
    pass


def _fernet() -> Fernet:
    settings = get_settings()
    if settings.token_encryption_key is not None:
        key = settings.token_encryption_key.get_secret_value().encode()
    else:
        if settings.is_production:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY must be set in production")
        digest = hashlib.sha256(settings.secret_key.get_secret_value().encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt_secret(ciphertext: bytes) -> str:
    try:
        return _fernet().decrypt(ciphertext).decode()
    except InvalidToken as exc:
        raise TokenDecryptionError("stored secret could not be decrypted") from exc
