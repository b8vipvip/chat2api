from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.data_keyring import DataKeyring, DataKeyringError


def test_data_keyring_writes_versioned_purpose_scoped_envelopes(tmp_path: Path) -> None:
    keyring = DataKeyring(tmp_path)
    encrypted = keyring.encrypt("merchant-secret", "payment_channels")
    envelope = json.loads(encrypted)

    assert envelope["format"] == DataKeyring.FORMAT
    assert envelope["version"] == 2
    assert envelope["key_id"] == keyring.key_id
    assert envelope["purpose"] == "payment_channels"
    assert "merchant-secret" not in encrypted
    assert keyring.decrypt(encrypted, "payment_channels") == b"merchant-secret"
    with pytest.raises(DataKeyringError, match="purpose"):
        keyring.decrypt(encrypted, "media_archive.settings")
    assert keyring.path.stat().st_mode & 0o777 == 0o600


def test_empty_existing_data_key_is_never_replaced(tmp_path: Path) -> None:
    path = tmp_path / "data_secret.key"
    path.write_text("", encoding="ascii")

    with pytest.raises(DataKeyringError, match="empty"):
        DataKeyring(tmp_path)
    assert path.read_text(encoding="ascii") == ""


def test_missing_key_fails_closed_when_payment_config_ciphertext_exists(tmp_path: Path) -> None:
    legacy_cipher = DataKeyring.legacy_fernet("old-root-secret")
    payload = {
        "version": 1,
        "key_ciphertext": legacy_cipher.encrypt(b"merchant-secret").decode("ascii"),
    }
    (tmp_path / "user_payment_config.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DataKeyringError, match="payment configuration contains ciphertext"):
        DataKeyring(tmp_path)
    assert not (tmp_path / "data_secret.key").exists()


def test_missing_key_can_be_created_only_after_legacy_api_key_ciphertext_is_verified(tmp_path: Path) -> None:
    old_admin_key = "historical-admin-key"
    legacy_cipher = DataKeyring.legacy_fernet(old_admin_key)
    payload = {
        "keys": [
            {
                "key_id": "key_legacy",
                "token_ciphertext": legacy_cipher.encrypt(b"sk-chat2api-legacy").decode("ascii"),
            }
        ]
    }
    (tmp_path / "api_keys.json").write_text(json.dumps(payload), encoding="utf-8")

    keyring = DataKeyring(
        tmp_path,
        legacy_secrets={"api_key.recoverable": [old_admin_key]},
    )
    assert keyring.root_secret
    assert keyring.path.exists()


def test_missing_key_refuses_unrecoverable_api_key_ciphertext(tmp_path: Path) -> None:
    legacy_cipher = DataKeyring.legacy_fernet("different-admin-key")
    payload = {
        "keys": [
            {
                "key_id": "key_legacy",
                "token_ciphertext": legacy_cipher.encrypt(b"sk-chat2api-legacy").decode("ascii"),
            }
        ]
    }
    (tmp_path / "api_keys.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DataKeyringError, match="API key secret cannot be proven recoverable"):
        DataKeyring(
            tmp_path,
            legacy_secrets={"api_key.recoverable": ["current-admin-key"]},
        )
    assert not (tmp_path / "data_secret.key").exists()


def test_missing_key_preflights_legacy_media_settings(tmp_path: Path) -> None:
    old_admin_key = "old-media-admin-key"
    legacy_cipher = DataKeyring.legacy_fernet(
        old_admin_key,
        prefix="chat2api:media:settings:v1:",
    )
    media_dir = tmp_path / "media_archive"
    media_dir.mkdir()
    (media_dir / "settings.enc").write_bytes(
        legacy_cipher.encrypt(b'{"backend":"s3","s3":{"bucket":"archive"}}')
    )

    keyring = DataKeyring(
        tmp_path,
        legacy_secrets={"media_archive.settings": [old_admin_key]},
    )
    assert keyring.root_secret


def test_concurrent_first_start_creates_one_shared_root_key(tmp_path: Path) -> None:
    def load_key(_: int) -> str:
        return DataKeyring(tmp_path).root_secret

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(load_key, range(8)))

    assert len(set(values)) == 1
    assert (tmp_path / "data_secret.key").stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".data_secret.*.tmp"))
