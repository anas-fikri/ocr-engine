"""Enkripsi rahasia yang disimpan di database (contoh secret key SILO dari Web UI).

Kunci diturunkan dari env PQE_SECRET_KEY. Tanpa PQE_SECRET_KEY, rahasia tidak bisa disimpan lewat
Web UI (tetap bisa lewat env AWS_SECRET_ACCESS_KEY). Jika PQE_SECRET_KEY diganti, rahasia lama
tidak bisa dibaca lagi dan harus diisi ulang.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from engine.settings import get_settings


class SecretKeyMissing(Exception):
    pass


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise SecretKeyMissing("PQE_SECRET_KEY belum diisi: rahasia tidak bisa disimpan terenkripsi")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, SecretKeyMissing):
        return None


def available() -> bool:
    return bool(get_settings().secret_key)
