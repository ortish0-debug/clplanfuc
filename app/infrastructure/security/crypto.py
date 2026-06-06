"""Шифрование данных "at rest" с использованием Fernet (AES-128 CBC + HMAC-SHA256)."""
import base64
import os
from cryptography.fernet import Fernet, InvalidToken


def _get_fernet_key() -> bytes:
    """Получить ключ Fernet из SECRET_KEY или генерировать новый."""
    secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    if secret_key == "dev-secret-key-change-in-production":
        return Fernet.generate_key()

    key_material = secret_key[:32].encode().ljust(32, b'\0')
    return base64.urlsafe_b64encode(key_material)


_cipher = Fernet(_get_fernet_key())


def encrypt_data(plain_text: str) -> str:
    """Зашифровать текст с использованием Fernet (AES-256 CBC + HMAC-SHA256)."""
    if not plain_text:
        return ""
    encrypted = _cipher.encrypt(plain_text.encode())
    return encrypted.decode()


def decrypt_data(cipher_text: str) -> str:
    """Расшифровать текст."""
    if not cipher_text:
        return ""
    try:
        decrypted = _cipher.decrypt(cipher_text.encode())
        return decrypted.decode()
    except InvalidToken:
        return ""
