from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class DataKeyringError(RuntimeError):
    """Raised when encrypted application data cannot be safely opened."""


class DataKeyring:
    """Versioned, purpose-separated encryption backed by one server data key.

    Legacy ciphertext is deliberately not accepted by decrypt(). Individual
    stores must opt into their narrowly-scoped legacy reader and migrate only
    after successful plaintext/schema validation.
    """

    FORMAT = "chat2api-encrypted-value"
    VERSION = 2
    KEY_FILENAME = "data_secret.key"
    _HKDF_SALT = b"chat2api-data-keyring-v2"

    def __init__(
        self,
        data_dir: Path,
        *,
        legacy_secrets: Mapping[str, Iterable[str]] | None = None,
        allow_empty_media_legacy: bool = False,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / self.KEY_FILENAME
        self.allow_empty_media_legacy = bool(allow_empty_media_legacy)
        self._legacy_secrets: dict[str, tuple[str, ...]] = {}
        for purpose, values in (legacy_secrets or {}).items():
            self._validate_purpose(purpose)
            unique: list[str] = []
            for value in values:
                candidate = str(value or "")
                if candidate not in unique:
                    unique.append(candidate)
            self._legacy_secrets[purpose] = tuple(unique)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._root_secret = self._load_or_create()
        self.key_id = "data-key-" + hashlib.sha256(self._root_secret.encode("utf-8")).hexdigest()[:16]

    @property
    def root_secret(self) -> str:
        """Compatibility accessor; never log or expose this value."""
        return self._root_secret

    @staticmethod
    def _validate_purpose(purpose: str) -> str:
        value = str(purpose or "")
        if not value or len(value) > 64 or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for ch in value):
            raise ValueError("Invalid data-key purpose")
        return value

    @staticmethod
    def looks_like_envelope(value: str | bytes) -> bool:
        raw = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
        # Legacy Fernet tokens are URL-safe base64 and never begin with '{'.
        # Treat malformed JSON-looking values as damaged envelopes, not legacy data.
        return raw.lstrip().startswith("{")

    @staticmethod
    def legacy_fernet(secret: str, *, prefix: str = "") -> Fernet:
        material = hashlib.sha256((prefix + str(secret or "")).encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(material))

    def legacy_secrets_for(self, purpose: str) -> tuple[str, ...]:
        self._validate_purpose(purpose)
        return self._legacy_secrets.get(purpose, ())

    def _fernet(self, purpose: str) -> Fernet:
        purpose = self._validate_purpose(purpose)
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self._HKDF_SALT,
            info=f"chat2api:{purpose}:v{self.VERSION}".encode("ascii"),
        ).derive(self._root_secret.encode("utf-8"))
        return Fernet(base64.urlsafe_b64encode(derived))

    def encrypt(self, value: str | bytes, purpose: str) -> str:
        purpose = self._validate_purpose(purpose)
        plaintext = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        ciphertext = self._fernet(purpose).encrypt(plaintext).decode("ascii")
        envelope = {
            "format": self.FORMAT,
            "version": self.VERSION,
            "key_id": self.key_id,
            "purpose": purpose,
            "ciphertext": ciphertext,
        }
        return json.dumps(envelope, separators=(",", ":"), ensure_ascii=True)

    def decrypt(self, value: str | bytes, purpose: str) -> bytes:
        purpose = self._validate_purpose(purpose)
        try:
            raw = value.decode("utf-8") if isinstance(value, bytes) else str(value or "")
            envelope: Any = json.loads(raw)
        except (TypeError, ValueError, UnicodeDecodeError) as error:
            raise DataKeyringError("Encrypted value is not a valid versioned envelope") from error
        if not isinstance(envelope, dict) or envelope.get("format") != self.FORMAT:
            raise DataKeyringError("Encrypted value has an unsupported format")
        if envelope.get("version") != self.VERSION:
            raise DataKeyringError("Encrypted value uses an unsupported key format version")
        if envelope.get("purpose") != purpose:
            raise DataKeyringError("Encrypted value purpose does not match its storage field")
        if envelope.get("key_id") != self.key_id:
            raise DataKeyringError("Encrypted value references an unavailable data key")
        token = envelope.get("ciphertext")
        if not isinstance(token, str) or not token:
            raise DataKeyringError("Encrypted value is missing its ciphertext")
        try:
            return self._fernet(purpose).decrypt(token.encode("ascii"))
        except (InvalidToken, ValueError, UnicodeEncodeError) as error:
            raise DataKeyringError("Encrypted value failed authentication") from error

    def _load_or_create(self) -> str:
        if self.path.exists() or self.path.is_symlink():
            return self._read_existing()
        self._preflight_legacy_data()

        value = secrets.token_urlsafe(48)
        temporary_path: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix=".data_secret.", suffix=".tmp", dir=self.data_dir)
            temporary_path = Path(name)
            try:
                os.fchmod(fd, 0o600)
            except OSError as error:
                os.close(fd)
                raise DataKeyringError("Unable to secure the temporary data key file") from error
            with os.fdopen(fd, "wb") as stream:
                stream.write(value.encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Hard-link creation is atomic and never overwrites a key created
                # by another process that won the first-start race.
                os.link(temporary_path, self.path)
                self._fsync_directory()
                return value
            except FileExistsError:
                return self._read_existing()
            except OSError as error:
                if self.path.exists():
                    return self._read_existing()
                raise DataKeyringError("Unable to atomically create the data key") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _read_existing(self) -> str:
        if self.path.is_symlink() or not self.path.is_file():
            raise DataKeyringError("The data key path is not a regular file")
        try:
            value = self.path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as error:
            raise DataKeyringError("The existing data key cannot be read") from error
        if not value:
            raise DataKeyringError("The existing data key is empty; restore the original key backup")
        try:
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise DataKeyringError("Unable to enforce restrictive permissions on the data key") from error
        return value

    def _fsync_directory(self) -> None:
        try:
            fd = os.open(self.data_dir, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    @staticmethod
    def backup_file(path: Path) -> Path | None:
        """Create a verified, restrictive, non-overwriting per-file migration backup."""
        source = Path(path)
        if not source.exists():
            return None
        if source.is_symlink() or not source.is_file():
            raise DataKeyringError("Cannot back up a non-regular encrypted data file")
        backup = source.with_name(source.name + ".pre-keyring.bak")
        checksum_path = backup.with_name(backup.name + ".sha256")
        if backup.exists() or backup.is_symlink() or checksum_path.exists() or checksum_path.is_symlink():
            raise DataKeyringError("A pre-keyring backup already exists; verify or restore it before retrying")
        try:
            payload = source.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(backup, 0o600)
            if hashlib.sha256(backup.read_bytes()).hexdigest() != digest:
                raise DataKeyringError("Encrypted data backup verification failed")
            checksum_fd = os.open(checksum_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(checksum_fd, "w", encoding="ascii") as stream:
                stream.write(digest + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            backup.unlink(missing_ok=True)
            checksum_path.unlink(missing_ok=True)
            raise
        return backup

    @staticmethod
    def restore_backup(path: Path) -> bool:
        """Restore the verified pre-keyring backup without deleting that backup."""
        target = Path(path)
        backup = target.with_name(target.name + ".pre-keyring.bak")
        checksum_path = backup.with_name(backup.name + ".sha256")
        if not backup.exists():
            return False
        if backup.is_symlink() or not backup.is_file() or checksum_path.is_symlink() or not checksum_path.is_file() or target.is_symlink():
            raise DataKeyringError("Cannot safely restore a non-regular encrypted data backup")
        try:
            payload = backup.read_bytes()
            expected_digest = checksum_path.read_text(encoding="ascii").strip()
            if not expected_digest or hashlib.sha256(payload).hexdigest() != expected_digest:
                raise DataKeyringError("Pre-keyring backup checksum does not match")
            temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.restore")
            try:
                with open(temporary, "xb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if hashlib.sha256(temporary.read_bytes()).digest() != hashlib.sha256(payload).digest():
                    raise DataKeyringError("Encrypted data restore verification failed")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as error:
            raise DataKeyringError("Unable to restore the pre-keyring encrypted data backup") from error
        return True

    def _read_json(self, path: Path, label: str) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise DataKeyringError(f"Cannot inspect existing encrypted {label}; refusing to replace its key") from error
        if not isinstance(value, dict):
            raise DataKeyringError(f"Existing encrypted {label} has an invalid document shape")
        return value

    @staticmethod
    def _can_decrypt(token: str, candidates: Iterable[str], *, prefix: str = "", skip_empty: bool = False) -> bool:
        for secret in candidates:
            if skip_empty and not secret:
                continue
            try:
                DataKeyring.legacy_fernet(secret, prefix=prefix).decrypt(token.encode("ascii"))
                return True
            except (InvalidToken, ValueError, UnicodeEncodeError):
                continue
        return False

    def _preflight_legacy_data(self) -> None:
        """Never mint a replacement root key while known ciphertext would be orphaned."""
        api_path = self.data_dir / "api_keys.json"
        api_doc = self._read_json(api_path, "API keys")
        if api_doc is not None:
            rows = api_doc.get("keys", [])
            if not isinstance(rows, list):
                raise DataKeyringError("Existing API key document has an invalid keys collection")
            tokens = [
                str(row.get("token_ciphertext") or "")
                for row in rows
                if isinstance(row, dict) and row.get("token_ciphertext")
            ]
            candidates = self.legacy_secrets_for("api_key.recoverable")
            for token in tokens:
                if self.looks_like_envelope(token) or not self._can_decrypt(token, candidates, skip_empty=True):
                    raise DataKeyringError(
                        "data_secret.key is missing but an API key secret cannot be proven recoverable; restore the original data key"
                    )

        payment_config = self._read_json(self.data_dir / "user_payment_config.json", "payment configuration")
        if payment_config and payment_config.get("key_ciphertext"):
            raise DataKeyringError(
                "data_secret.key is missing but payment configuration contains ciphertext; restore the original data key"
            )

        media_path = self.data_dir / "media_archive" / "settings.enc"
        if media_path.exists():
            try:
                encrypted = media_path.read_bytes()
            except OSError as error:
                raise DataKeyringError("Cannot inspect existing media settings; refusing to create a replacement key") from error
            candidates = list(self.legacy_secrets_for("media_archive.settings"))
            if not self.allow_empty_media_legacy:
                candidates = [candidate for candidate in candidates if candidate]
            if self.looks_like_envelope(encrypted) or not self._can_decrypt(
                encrypted.decode("ascii", errors="ignore"),
                candidates,
                prefix="chat2api:media:settings:v1:",
            ):
                raise DataKeyringError(
                    "data_secret.key is missing but media settings cannot be proven recoverable; restore the original key or legacy administrator secret"
                )

        channels_path = self.data_dir / "user_payment_channels_v106.json"
        channels_doc = self._read_json(channels_path, "payment channel configuration")
        if channels_doc is not None:
            candidates = [
                value for value in self.legacy_secrets_for("payment_channels") if value
            ]
            legacy_path = self.data_dir / ".chat2api_data_secret"
            if legacy_path.exists():
                try:
                    legacy_value = legacy_path.read_text(encoding="utf-8").strip()
                    if legacy_value:
                        candidates.append(legacy_value)
                    else:
                        # The old Store used this fallback only when the legacy
                        # secret file existed but contained no usable value.
                        candidates.append("chat2api-payment-v106")
                except OSError:
                    # The old installer fell back to settings.api_key or "chat2api".
                    candidates.append("chat2api")
            candidates.append("chat2api")
            providers = channels_doc.get("aggregate") or {}
            direct = channels_doc.get("direct") or {}
            tokens = []
            zpay = providers.get("zpay") if isinstance(providers, dict) else None
            paypal = direct.get("paypal") if isinstance(direct, dict) else None
            if isinstance(zpay, dict) and zpay.get("key_ciphertext"):
                tokens.append(str(zpay["key_ciphertext"]))
            if isinstance(paypal, dict) and paypal.get("client_secret_ciphertext"):
                tokens.append(str(paypal["client_secret_ciphertext"]))
            for token in tokens:
                if self.looks_like_envelope(token) or not self._can_decrypt(token, candidates):
                    raise DataKeyringError(
                        "data_secret.key is missing but payment channel credentials cannot be proven recoverable; restore the original key"
                    )
