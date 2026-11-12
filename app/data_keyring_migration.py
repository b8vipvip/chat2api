from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .api_keys import ApiKeyStore, _fernet
from .data_keyring import DataKeyring, DataKeyringError
from .media_archive_v173 import MediaArchive
from .payment_channels_v106_patch import PaymentChannelsStore
from .user_commerce import PaymentConfigStore


_DATA_FILES = (
    "api_keys.json",
    "media_archive/settings.enc",
    "user_payment_config.json",
    "user_payment_channels_v106.json",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise DataKeyringError(f"Migration target is not a regular file: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DataKeyringMigrationService:
    """Offline, staged and reversible migration of legacy encrypted data.

    The service never migrates production data during application startup.
    Run it with the API and Worker processes stopped. Dry-run transforms only
    private staging copies; apply backs up every changed source before replacing
    any source file and verifies all new envelopes after readback.
    """

    def __init__(
        self,
        data_dir: Path,
        keyring: DataKeyring,
        *,
        legacy_media_secret: str = "",
        api_key_legacy_secrets: tuple[str, ...] | list[str] = (),
        payment_channel_legacy_secrets: tuple[str, ...] | list[str] = (),
    ) -> None:
        self.data_dir = Path(data_dir)
        self.keyring = keyring
        self.legacy_media_secret = str(legacy_media_secret or "")
        self.api_key_legacy_secrets = self._unique(api_key_legacy_secrets)
        self.payment_channel_legacy_secrets = self._unique(payment_channel_legacy_secrets)
        self.lock_path = self.data_dir / ".data-keyring-migration.lock"
        self.manifest_path = self.data_dir / "data_keyring_migration.json"
        if self.keyring.path != self.data_dir / DataKeyring.KEY_FILENAME:
            raise DataKeyringError("The migration keyring must belong to the target data directory")
        if not self.keyring.path.is_file() or self.keyring.path.is_symlink():
            raise DataKeyringError("A pre-existing data_secret.key is required; migration must not create one")

    @staticmethod
    def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        result: list[str] = []
        for item in values:
            value = str(item or "")
            if value and value not in result:
                result.append(value)
        return tuple(result)

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise DataKeyringError(
                "Another data-key migration holds the lock; inspect and remove a stale lock only after confirming no migration process is running"
            ) from error
        try:
            payload = json.dumps(
                {"pid": os.getpid(), "started_at": _utc_now()},
                separators=(",", ":"),
            ).encode("utf-8")
            os.write(fd, payload)
            os.fsync(fd)
            yield
        finally:
            os.close(fd)
            self.lock_path.unlink(missing_ok=True)

    @staticmethod
    def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
            except OSError:
                return
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        self._atomic_write(self.manifest_path, payload)

    def _copy_sources(self, stage_dir: Path) -> None:
        for relative in _DATA_FILES:
            source = self.data_dir / relative
            if not source.exists() and not source.is_symlink():
                continue
            if source.is_symlink() or not source.is_file():
                raise DataKeyringError(f"Encrypted data source is not a regular file: {relative}")
            destination = stage_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            os.chmod(destination, 0o600)

    async def _stores_for(self, root: Path) -> dict[str, Any]:
        stores: dict[str, Any] = {}
        api_path = root / "api_keys.json"
        if api_path.is_file():
            api = ApiKeyStore(root, self.keyring.root_secret, keyring=self.keyring)
            api.legacy_ciphers = [
                cipher
                for cipher in (_fernet(value) for value in self.api_key_legacy_secrets)
                if cipher is not None
            ]
            await api.load()
            stores["api_keys"] = api

        media_path = root / "media_archive" / "settings.enc"
        if media_path.is_file():
            media = MediaArchive(root, self.legacy_media_secret, keyring=self.keyring)
            stores["media_archive"] = media

        payment_path = root / "user_payment_config.json"
        if payment_path.is_file():
            stores["payment_config"] = PaymentConfigStore(root, self.keyring)

        channels_path = root / "user_payment_channels_v106.json"
        if channels_path.is_file():
            stores["payment_channels"] = PaymentChannelsStore(
                root,
                self.keyring,
                legacy_secrets=list(self.payment_channel_legacy_secrets),
            )
        return stores

    async def _verify_directory(self, root: Path, *, require_envelopes: bool) -> None:
        stores = await self._stores_for(root)

        api = stores.get("api_keys")
        if api is not None:
            await api.validate_ciphertexts()
            for item in api.keys.values():
                value = str(item.token_ciphertext or "")
                if not value:
                    continue
                if require_envelopes and not DataKeyring.looks_like_envelope(value):
                    raise DataKeyringError("An API key secret remains in a legacy encryption format")
                if DataKeyring.looks_like_envelope(value):
                    self.keyring.decrypt(value, "api_key.recoverable")

        media = stores.get("media_archive")
        if media is not None:
            media._config()
            if require_envelopes:
                value = media.settings_path.read_bytes()
                if not DataKeyring.looks_like_envelope(value):
                    raise DataKeyringError("Media settings remain in a legacy encryption format")
                self.keyring.decrypt(value, "media_archive.settings")

        payment = stores.get("payment_config")
        if payment is not None:
            value = str(payment.payload.get("key_ciphertext") or "")
            payment.secret_key()
            if value and require_envelopes:
                if not DataKeyring.looks_like_envelope(value):
                    raise DataKeyringError("Payment configuration remains in a legacy encryption format")
                self.keyring.decrypt(value, "payment_config")

        channels = stores.get("payment_channels")
        if channels is not None:
            for row, field in (
                (channels.payload["aggregate"]["zpay"], "key_ciphertext"),
                (channels.payload["direct"]["paypal"], "client_secret_ciphertext"),
            ):
                value = str(row.get(field) or "")
                if not value:
                    continue
                channels._decrypt(value)
                if require_envelopes:
                    if not DataKeyring.looks_like_envelope(value):
                        raise DataKeyringError("Payment channel credentials remain in a legacy encryption format")
                    self.keyring.decrypt(value, "payment_channels")

    async def _build_plan(self) -> tuple[Path, dict[str, Any]]:
        stage_dir = Path(tempfile.mkdtemp(prefix=".data-keyring-stage-", dir=self.data_dir))
        os.chmod(stage_dir, 0o700)
        try:
            self._copy_sources(stage_dir)
            stores = await self._stores_for(stage_dir)
            await self._verify_directory(stage_dir, require_envelopes=False)

            counts = {
                "api_key_secrets": 0,
                "media_settings": 0,
                "payment_config_secrets": 0,
                "payment_channel_secrets": 0,
            }
            api = stores.get("api_keys")
            if api is not None:
                counts["api_key_secrets"] = await api.migrate_cipher_from()

            media = stores.get("media_archive")
            if media is not None:
                counts["media_settings"] = int(media.migrate_legacy_settings())

            payment = stores.get("payment_config")
            if payment is not None:
                counts["payment_config_secrets"] = int(payment.migrate_legacy_ciphertext())

            channels = stores.get("payment_channels")
            if channels is not None:
                counts["payment_channel_secrets"] = channels.migrate_legacy_ciphertexts()

            await self._verify_directory(stage_dir, require_envelopes=True)
            changes: list[dict[str, str]] = []
            for relative in _DATA_FILES:
                original = self.data_dir / relative
                staged = stage_dir / relative
                if not original.exists():
                    continue
                if not staged.is_file():
                    raise DataKeyringError(f"Staged migration output is missing: {relative}")
                before = _sha256(original)
                after = _sha256(staged)
                if before != after:
                    changes.append(
                        {
                            "path": relative,
                            "pre_sha256": before,
                            "post_sha256": after,
                        }
                    )
            return stage_dir, {"files": changes, "counts": counts}
        except Exception:
            shutil.rmtree(stage_dir, ignore_errors=True)
            raise

    @staticmethod
    def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "files": [
                {
                    "path": item["path"],
                    "pre_sha256": item["pre_sha256"],
                    "post_sha256": item["post_sha256"],
                }
                for item in plan["files"]
            ],
            "counts": dict(plan["counts"]),
        }

    async def dry_run(self) -> dict[str, Any]:
        with self._exclusive_lock():
            stage_dir, plan = await self._build_plan()
            try:
                return {
                    "status": "dry_run",
                    "key_id": self.keyring.key_id,
                    **self._public_plan(plan),
                }
            finally:
                shutil.rmtree(stage_dir, ignore_errors=True)

    def _replace_from_stage(self, staged: Path, destination: Path) -> None:
        if staged.is_symlink() or not staged.is_file():
            raise DataKeyringError(f"Staged output is not a regular file: {staged.name}")
        self._atomic_write(destination, staged.read_bytes())

    def _rollback_entries(self, entries: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        for item in reversed(entries):
            path = self.data_dir / str(item["path"])
            try:
                current = _sha256(path)
                if current == item["pre_sha256"]:
                    continue
                if current != item["post_sha256"]:
                    raise DataKeyringError("Current file no longer matches the migration output")
                if not DataKeyring.restore_backup(path):
                    raise DataKeyringError("Verified pre-migration backup is missing")
            except Exception as error:
                errors.append(f"{item['path']}: {type(error).__name__}")
        return errors

    async def apply(self) -> dict[str, Any]:
        with self._exclusive_lock():
            stage_dir, plan = await self._build_plan()
            try:
                if not plan["files"]:
                    return {
                        "status": "no_changes",
                        "key_id": self.keyring.key_id,
                        **self._public_plan(plan),
                    }
                if self.manifest_path.exists() or self.manifest_path.is_symlink():
                    raise DataKeyringError(
                        "A prior migration manifest exists; archive its manifest and backups before starting a new migration"
                    )

                # Verify all backup destinations are free before creating any backup.
                for item in plan["files"]:
                    path = self.data_dir / item["path"]
                    backup = path.with_name(path.name + ".pre-keyring.bak")
                    checksum = backup.with_name(backup.name + ".sha256")
                    if backup.exists() or backup.is_symlink() or checksum.exists() or checksum.is_symlink():
                        raise DataKeyringError(f"An existing migration backup must be reviewed first: {item['path']}")

                created_backups: list[Path] = []
                try:
                    for item in plan["files"]:
                        path = self.data_dir / item["path"]
                        backup = DataKeyring.backup_file(path)
                        if backup is not None:
                            created_backups.append(backup)

                    manifest: dict[str, Any] = {
                        "version": 1,
                        "state": "applying",
                        "key_id": self.keyring.key_id,
                        "created_at": _utc_now(),
                        "files": [
                            {**item, "applied": False}
                            for item in plan["files"]
                        ],
                        "counts": dict(plan["counts"]),
                    }
                    self._write_manifest(manifest)
                except Exception:
                    for backup in created_backups:
                        backup.unlink(missing_ok=True)
                        backup.with_name(backup.name + ".sha256").unlink(missing_ok=True)
                    raise

                try:
                    for item in manifest["files"]:
                        self._replace_from_stage(stage_dir / item["path"], self.data_dir / item["path"])
                        item["applied"] = True
                        self._write_manifest(manifest)

                    await self._verify_directory(self.data_dir, require_envelopes=True)
                    manifest["state"] = "completed"
                    manifest["completed_at"] = _utc_now()
                    self._write_manifest(manifest)
                    return {
                        "status": "completed",
                        "key_id": self.keyring.key_id,
                        **self._public_plan(plan),
                    }
                except Exception as error:
                    rollback_errors = self._rollback_entries(manifest["files"])
                    if rollback_errors:
                        manifest["state"] = "rollback_failed"
                        manifest["rollback_errors"] = rollback_errors
                    else:
                        manifest["state"] = "rolled_back"
                        manifest["rolled_back_at"] = _utc_now()
                    self._write_manifest(manifest)
                    if rollback_errors:
                        raise DataKeyringError(
                            "Migration failed and automatic rollback was incomplete; preserve backups and perform manual recovery"
                        ) from error
                    await self._verify_directory(self.data_dir, require_envelopes=False)
                    raise DataKeyringError(
                        f"Migration failed ({type(error).__name__}); original encrypted files were restored"
                    ) from error
            finally:
                shutil.rmtree(stage_dir, ignore_errors=True)

    async def rollback(self) -> dict[str, Any]:
        with self._exclusive_lock():
            if not self.manifest_path.is_file() or self.manifest_path.is_symlink():
                raise DataKeyringError("No valid data-keyring migration manifest exists")
            try:
                manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as error:
                raise DataKeyringError("The data-keyring migration manifest is unreadable") from error
            if not isinstance(manifest, dict) or manifest.get("state") not in {
                "completed",
                "applying",
                "rollback_failed",
            }:
                if isinstance(manifest, dict) and manifest.get("state") == "rolled_back":
                    return {"status": "already_rolled_back", "key_id": self.keyring.key_id}
                raise DataKeyringError("The migration manifest is not in a rollback-eligible state")

            entries = manifest.get("files")
            if not isinstance(entries, list) or not entries:
                raise DataKeyringError("The migration manifest has no file entries")
            # Check every target before restoring any file to avoid partial rollback
            # when an operator has modified one of the migrated files.
            for item in entries:
                if not isinstance(item, dict):
                    raise DataKeyringError("The migration manifest contains an invalid file entry")
                path = self.data_dir / str(item.get("path") or "")
                current = _sha256(path)
                if current not in {item.get("pre_sha256"), item.get("post_sha256")}:
                    raise DataKeyringError(
                        f"Refusing rollback because a migrated file changed after apply: {item.get('path')}"
                    )

            errors = self._rollback_entries(entries)
            if errors:
                manifest["state"] = "rollback_failed"
                manifest["rollback_errors"] = errors
                self._write_manifest(manifest)
                raise DataKeyringError("Rollback was incomplete; preserve all backups for manual recovery")

            await self._verify_directory(self.data_dir, require_envelopes=False)
            manifest["state"] = "rolled_back"
            manifest["rolled_back_at"] = _utc_now()
            self._write_manifest(manifest)
            return {"status": "rolled_back", "key_id": self.keyring.key_id, "files": [x["path"] for x in entries]}
