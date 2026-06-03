"""Tests for per-channel secret encryption."""

import pytest

from storyfactory import crypto


def test_encrypt_decrypt_round_trip():
    key = crypto.generate_key().encode()
    secrets = {"api_key": "sk-secret-123", "refresh_token": "rt-abc"}

    token = crypto.encrypt_secrets(secrets, key=key)
    assert token  # non-empty ciphertext
    assert "sk-secret-123" not in token  # plaintext not present

    assert crypto.decrypt_secrets(token, key=key) == secrets


def test_decrypt_empty_returns_empty():
    key = crypto.generate_key().encode()
    assert crypto.decrypt_secrets(None, key=key) == {}
    assert crypto.decrypt_secrets("", key=key) == {}


def test_wrong_key_fails():
    key_a = crypto.generate_key().encode()
    key_b = crypto.generate_key().encode()
    token = crypto.encrypt_secrets({"api_key": "x"}, key=key_a)

    with pytest.raises(crypto.SecretKeyError):
        crypto.decrypt_secrets(token, key=key_b)


def test_missing_key_raises(monkeypatch, tmp_path):
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    monkeypatch.setenv(crypto.ENV_KEY_FILE, str(tmp_path / "nonexistent.key"))
    with pytest.raises(crypto.SecretKeyError):
        crypto.encrypt_secrets({"a": "b"})  # create_key defaults to False


def test_ensure_key_persists_to_file(monkeypatch, tmp_path):
    key_file = tmp_path / "k.key"
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    monkeypatch.setenv(crypto.ENV_KEY_FILE, str(key_file))

    key1 = crypto.ensure_key()
    assert key_file.exists()
    # Re-resolving returns the same persisted key.
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    assert crypto.resolve_key() == key1


def test_mask_secret():
    assert crypto.mask_secret("sk-1234567890") .endswith("7890")
    assert crypto.mask_secret("") == ""
    assert set(crypto.mask_secret("ab")) == {"•"}
