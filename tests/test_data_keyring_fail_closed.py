from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.api_keys import ApiKeyStore
from app.data_keyring import DataKeyring, DataKeyringError
from app.payment_channels_v106_patch import PaymentChannelsStore
from app.user_commerce import PaymentConfigStore


@pytest.mark.asyncio
async def test_corrupt_api_key_document_fails_closed(tmp_path: Path) -> None:
    keyring = DataKeyring(tmp_path)
    (tmp_path / "api_keys.json").write_text("{broken", encoding="utf-8")
    store = ApiKeyStore(tmp_path, keyring.root_secret, keyring=keyring)

    with pytest.raises(DataKeyringError, match="refusing to discard"):
        await store.load()


def test_corrupt_payment_config_fails_closed(tmp_path: Path) -> None:
    keyring = DataKeyring(tmp_path)
    (tmp_path / "user_payment_config.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(DataKeyringError, match="restore a verified backup"):
        PaymentConfigStore(tmp_path, keyring)


def test_corrupt_payment_channels_fails_closed(tmp_path: Path) -> None:
    keyring = DataKeyring(tmp_path)
    (tmp_path / "user_payment_channels_v106.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(DataKeyringError, match="restore a verified backup"):
        PaymentChannelsStore(tmp_path, keyring)


def test_malformed_nested_payment_provider_fails_closed(tmp_path: Path) -> None:
    keyring = DataKeyring(tmp_path)
    (tmp_path / "user_payment_channels_v106.json").write_text(
        '{"aggregate":{"zpay":[]}}',
        encoding="utf-8",
    )

    with pytest.raises(DataKeyringError, match="restore a verified backup"):
        PaymentChannelsStore(tmp_path, keyring)


def test_atomic_write_creates_private_file_before_use(tmp_path: Path) -> None:
    target = tmp_path / "private.json"

    DataKeyring.atomic_write(target, b'{"secret":"value"}')

    assert target.read_bytes() == b'{"secret":"value"}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".private.json.*.tmp"))


def test_backup_does_not_remove_a_racing_foreign_checksum(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "encrypted.json"
    source.write_bytes(b"legacy-ciphertext")
    backup = source.with_name(source.name + ".pre-keyring.bak")
    checksum = backup.with_name(backup.name + ".sha256")
    original_open = os.open

    def racing_open(path, flags, *args, **kwargs):
        if Path(path) == checksum:
            checksum.write_text("owned-by-another-process\n", encoding="ascii")
            raise FileExistsError(str(checksum))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(FileExistsError):
        DataKeyring.backup_file(source)

    assert checksum.read_text(encoding="ascii") == "owned-by-another-process\n"
    assert not backup.exists()
