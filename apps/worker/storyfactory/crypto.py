"""Secret encryption for per-channel integration credentials.

All credential encryption/decryption lives here in the worker. The dashboard
never encrypts or decrypts secrets — it routes secret mutations through the
worker CLI and only reads masked/"configured" status from the database.

Secrets are stored as a single Fernet-encrypted JSON blob per integration
(``channel_integrations.secrets_encrypted``). The symmetric master key is
resolved from, in order:

1. ``STORYFACTORY_SECRET_KEY`` environment variable (recommended for prod).
2. A key file at ``STORYFACTORY_KEY_FILE`` (default ``./.storyfactory.key``).

The key is intentionally kept OUT of the database so a cloud-synced SQLite
file never carries the means to decrypt its own secrets.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ENV_KEY = "STORYFACTORY_SECRET_KEY"
ENV_KEY_FILE = "STORYFACTORY_KEY_FILE"
DEFAULT_KEY_FILE = "./.storyfactory.key"


class SecretKeyError(RuntimeError):
    """Raised when a master key is required but unavailable or invalid."""


def generate_key() -> str:
    """Generate a new url-safe base64 Fernet key as a string."""
    return Fernet.generate_key().decode("utf-8")


def _key_file_path() -> Path:
    return Path(os.environ.get(ENV_KEY_FILE) or DEFAULT_KEY_FILE)


def _normalize_key(raw: str | bytes | None) -> bytes | None:
    if not raw:
        return None
    key = raw.encode("utf-8") if isinstance(raw, str) else raw
    key = key.strip()
    return key or None


def resolve_key() -> bytes | None:
    """Return the configured master key, or ``None`` if none is available."""
    env_key = _normalize_key(os.environ.get(ENV_KEY))
    if env_key:
        return env_key

    key_path = _key_file_path()
    if key_path.exists():
        return _normalize_key(key_path.read_text(encoding="utf-8"))

    return None


def is_key_available() -> bool:
    """Whether a master key can be resolved without creating one."""
    return resolve_key() is not None


def ensure_key() -> bytes:
    """Resolve the master key, generating and persisting one if necessary.

    Used by setup/migration paths so an upgrade requires zero manual key
    management. A generated key is written to the key file (never the DB) with
    owner-only permissions where the platform supports it.
    """
    existing = resolve_key()
    if existing:
        return existing

    key = generate_key().encode("utf-8")
    key_path = _key_file_path()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key.decode("utf-8"), encoding="utf-8")
    try:
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
    except (OSError, NotImplementedError):
        # Best effort; Windows ignores POSIX modes.
        pass
    # Make the generated key visible to the rest of this process.
    os.environ.setdefault(ENV_KEY, key.decode("utf-8"))
    return key


def _cipher(key: bytes | None = None, *, create: bool = False) -> Fernet:
    resolved = key or (ensure_key() if create else resolve_key())
    if not resolved:
        raise SecretKeyError(
            f"No encryption key available. Set {ENV_KEY} in the environment "
            f"(generate one with `python -m storyfactory channel generate-key`)."
        )
    try:
        return Fernet(resolved)
    except (ValueError, TypeError) as exc:
        raise SecretKeyError(f"Invalid {ENV_KEY}: {exc}") from exc


def encrypt_secrets(secrets: dict, *, key: bytes | None = None, create_key: bool = False) -> str:
    """Encrypt a dict of secret fields into a Fernet token string."""
    payload = json.dumps(secrets or {}, separators=(",", ":")).encode("utf-8")
    return _cipher(key, create=create_key).encrypt(payload).decode("utf-8")


def decrypt_secrets(token: str | None, *, key: bytes | None = None) -> dict:
    """Decrypt a Fernet token back into a dict. Returns ``{}`` for empty input."""
    if not token:
        return {}
    try:
        raw = _cipher(key).decrypt(token.encode("utf-8"))
    except InvalidToken as exc:
        raise SecretKeyError(
            "Failed to decrypt secrets — the master key does not match the "
            "key used to encrypt them."
        ) from exc
    return json.loads(raw.decode("utf-8"))


def mask_secret(value: str | None, *, visible: int = 4) -> str:
    """Return a masked representation safe for display/logging."""
    if not value:
        return ""
    text = str(value)
    if len(text) <= visible:
        return "•" * len(text)
    return f"{'•' * (len(text) - visible)}{text[-visible:]}"
