"""Encrypted vault for credentials, sessions and OAuth tokens (Fernet / AES-128-CBC + HMAC)."""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class VaultError(RuntimeError):
    pass


class Vault:
    """Encrypts JSON-serialisable secrets. The key is never stored next to the data."""

    def __init__(self, master_key: str | bytes | None) -> None:
        if not master_key:
            raise VaultError("JOBPILOT_MASTER_KEY is not set. Run `jobpilot keygen`.")
        try:
            self._fernet = Fernet(master_key)
        except (ValueError, TypeError) as exc:
            raise VaultError("JOBPILOT_MASTER_KEY is not a valid Fernet key") from exc

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, secret: dict[str, Any]) -> bytes:
        return self._fernet.encrypt(json.dumps(secret, sort_keys=True).encode())

    def decrypt(self, token: bytes) -> dict[str, Any]:
        try:
            return json.loads(self._fernet.decrypt(token))
        except InvalidToken as exc:
            raise VaultError("secret could not be decrypted (wrong key or tampered data)") from exc

    def __repr__(self) -> str:
        return "Vault(<redacted>)"
