from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api_keys import ApiKeyStore
from app.data_keyring import DataKeyring, DataKeyringError
from app.data_keyring_migration import DataKeyringMigrationService
from app.media_archive_v173 import MediaArchive
from app.payment_channels_v106_patch import PaymentChannelsStore
from app.user_commerce import PaymentConfigStore


@pytest.fixture
def migration_fixture(tmp_path: Path):
    api_secret = "legacy-api-admin-secret"
    media_secret = "legacy-media-admin-secret"
    channels_secret = "legacy-payment-channels-secret"
    keyring = DataKeyring(
        tmp_path,
        legacy_secrets={
            "api_key.recoverable": [api_secret],
            "media_archive.settings": [media_secret],
            "payment_channels": [channels_secret],
        },
    )
    return {
        "keyring": keyring,
        "api_secret": api_secret,
        "media_secret": media_secret,
        "channels_secret": channels_secret,
    }


async def _seed_legacy_files(tmp_path: Path, values: dict[str, object]) -> dict[str, bytes]:
    api = ApiKeyStore(tmp_path, str(values["api_secret"]))
    await api.load()
    await api.create("legacy key")

    media = MediaArchive(tmp_path, str(values["media_secret"]))
    media.configure({
        "backend": "s3",
        "s3": {
            "bucket": "archive-bucket",
            "access_key_id": "archive-access",
            "secret_access_key": "archive-secret",
        },
    })

    payment = PaymentConfigStore(tmp_path, str(values["keyring"].root_secret))
    payment.update({
        "enabled": True,
        "pid": "10001",
        "key": "legacy-payment-secret",
    })

    channels = PaymentChannelsStore(tmp_path, str(values["channels_secret"]))
    channels.update({
        "aggregate": {
            "zpay": {
                "enabled": True,
                "pid": "10002",
                "key": "legacy-channel-secret",
                "alipay_enabled": True,
            }
        }
    })

    relatives = (
        "api_keys.json",
        "media_archive/settings.enc",
        "user_payment_config.json",
        "user_payment_channels_v106.json",
    )
    return {
        relative: (tmp_path / relative).read_bytes()
        for relative in relatives
    }


def _service(tmp_path: Path, values: dict[str, object]) -> DataKeyringMigrationService:
    return DataKeyringMigrationService(
        tmp_path,
        values["keyring"],
        legacy_media_secret=str(values["media_secret"]),
        api_key_legacy_secrets=[str(values["api_secret"])],
        payment_channel_legacy_secrets=[str(values["channels_secret"])],
    )


@pytest.mark.asyncio
async def test_dry_run_stages_all_stores_without_mutating_live_data(tmp_path: Path, migration_fixture) -> None:
    original = await _seed_legacy_files(tmp_path, migration_fixture)
    service = _service(tmp_path, migration_fixture)

    result = await service.dry_run()

    assert result["status"] == "dry_run"
    assert {item["path"] for item in result["files"]} == set(original)
    assert result["counts"] == {
        "api_key_secrets": 1,
        "media_settings": 1,
        "payment_config_secrets": 1,
        "payment_channel_secrets": 1,
    }
    for relative, content in original.items():
        assert (tmp_path / relative).read_bytes() == content
        assert not (tmp_path / (relative + ".pre-keyring.bak")).exists()
    assert not (tmp_path / "data_keyring_migration.json").exists()
    assert not (tmp_path / ".data-keyring-migration.lock").exists()


@pytest.mark.asyncio
async def test_apply_migrates_all_legacy_stores_and_keeps_verified_backups(tmp_path: Path, migration_fixture) -> None:
    original = await _seed_legacy_files(tmp_path, migration_fixture)
    service = _service(tmp_path, migration_fixture)

    result = await service.apply()

    assert result["status"] == "completed"
    assert len(result["files"]) == 4
    for relative, content in original.items():
        path = tmp_path / relative
        backup = path.with_name(path.name + ".pre-keyring.bak")
        checksum = backup.with_name(backup.name + ".sha256")
        assert backup.read_bytes() == content
        assert checksum.exists()
    api_doc = json.loads((tmp_path / "api_keys.json").read_text(encoding="utf-8"))
    assert all(
        DataKeyring.looks_like_envelope(row["token_ciphertext"])
        for row in api_doc["keys"] if row.get("token_ciphertext")
    )
    media_value = (tmp_path / "media_archive" / "settings.enc").read_bytes()
    assert DataKeyring.looks_like_envelope(media_value)
    payment_doc = json.loads((tmp_path / "user_payment_config.json").read_text(encoding="utf-8"))
    assert DataKeyring.looks_like_envelope(payment_doc["key_ciphertext"])
    channels_doc = json.loads((tmp_path / "user_payment_channels_v106.json").read_text(encoding="utf-8"))
    assert DataKeyring.looks_like_envelope(channels_doc["aggregate"]["zpay"]["key_ciphertext"])
    manifest = json.loads((tmp_path / "data_keyring_migration.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "completed"
    assert manifest["key_id"] == migration_fixture["keyring"].key_id


@pytest.mark.asyncio
async def test_rollback_restores_all_original_ciphertexts(tmp_path: Path, migration_fixture) -> None:
    original = await _seed_legacy_files(tmp_path, migration_fixture)
    service = _service(tmp_path, migration_fixture)
    await service.apply()

    result = await service.rollback()

    assert result["status"] == "rolled_back"
    for relative, content in original.items():
        assert (tmp_path / relative).read_bytes() == content
    manifest = json.loads((tmp_path / "data_keyring_migration.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "rolled_back"


@pytest.mark.asyncio
async def test_rollback_refuses_files_modified_after_migration(tmp_path: Path, migration_fixture) -> None:
    await _seed_legacy_files(tmp_path, migration_fixture)
    service = _service(tmp_path, migration_fixture)
    await service.apply()

    target = tmp_path / "user_payment_config.json"
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(DataKeyringError, match="changed after apply"):
        await service.rollback()
    assert json.loads((tmp_path / "data_keyring_migration.json").read_text(encoding="utf-8"))["state"] == "completed"


@pytest.mark.asyncio
async def test_apply_failure_rolls_back_files_already_replaced(tmp_path: Path, migration_fixture, monkeypatch) -> None:
    original = await _seed_legacy_files(tmp_path, migration_fixture)
    service = _service(tmp_path, migration_fixture)
    verify = service._verify_directory

    async def fail_after_apply(root: Path, *, require_envelopes: bool) -> None:
        if Path(root) == tmp_path and require_envelopes:
            raise DataKeyringError("synthetic post-write readback failure")
        await verify(root, require_envelopes=require_envelopes)

    monkeypatch.setattr(service, "_verify_directory", fail_after_apply)

    with pytest.raises(DataKeyringError, match="original encrypted files were restored"):
        await service.apply()

    for relative, content in original.items():
        assert (tmp_path / relative).read_bytes() == content
    manifest = json.loads((tmp_path / "data_keyring_migration.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "rolled_back"

@pytest.mark.asyncio
async def test_rollback_rejects_tampered_manifest_path(tmp_path: Path, migration_fixture) -> None:
    await _seed_legacy_files(tmp_path, migration_fixture)
    service = _service(tmp_path, migration_fixture)
    await service.apply()

    manifest_path = tmp_path / "data_keyring_migration.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(DataKeyringError, match="unsafe file path"):
        await service.rollback()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "completed"

