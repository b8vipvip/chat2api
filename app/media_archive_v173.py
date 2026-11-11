"""Generated-output archive with independent thumbnails and same-origin URLs.

Binary payloads never enter request history; the opaque URL token grants read-only
access to one archived item. All provider credentials are encrypted at rest.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

from .data_keyring import DataKeyring, DataKeyringError
from PIL import Image, ImageOps, UnidentifiedImageError

MEDIA_PATTERN = re.compile(
    r"!\[([^\]\n]{0,200})\]\((data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+|https://[^)\s]{1,2048})\)", re.I,
)
ALLOWED = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
MAX_BYTES = 20 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 36_000_000


class MediaArchive:
    def __init__(self, data_dir: Path, master_secret: str, *, keyring: DataKeyring | None = None):
        self.root = Path(data_dir) / "media_archive"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "index.sqlite3"
        self.settings_path = self.root / "settings.enc"
        secret_key = hashlib.sha256(("chat2api:media:settings:v1:" + str(master_secret or "")).encode()).digest()
        self._cipher = Fernet(base64.urlsafe_b64encode(secret_key))
        self._legacy_master_secret = str(master_secret or "")
        self.keyring = keyring
        self._init_db()

    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._db() as conn:
            # Serialize schema inspection/ALTER across multi-process API
            # workers; both may open the same older image archive at startup.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""CREATE TABLE IF NOT EXISTS media (
                id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
                request_id TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL,
                backend TEXT NOT NULL, object_key TEXT NOT NULL,
                thumb_key TEXT NOT NULL, created_at TEXT NOT NULL,
                sha256 TEXT NOT NULL DEFAULT ''
            )""")
            columns = {record["name"] for record in conn.execute("PRAGMA table_info(media)")}
            if "sha256" not in columns:
                # Historical rows predate digest persistence. Never invent a
                # checksum by hashing their possibly damaged current bytes.
                # They stay accessible under length-only legacy validation.
                conn.execute("ALTER TABLE media ADD COLUMN sha256 TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS media_created_idx ON media(created_at DESC)")

    def _config(self):
        defaults = {"backend": "local", "auto_archive": True, "public_base_url": "",
                    "s3": {"bucket": "", "region": "us-east-1", "endpoint": "",
                           "access_key_id": "", "secret_access_key": ""}}
        if self.settings_path.exists():
            try:
                encrypted = self.settings_path.read_bytes()
                if self.keyring and DataKeyring.looks_like_envelope(encrypted):
                    decoded = self.keyring.decrypt(encrypted, "media_archive.settings")
                else:
                    # Read-only compatibility for settings written before DataKeyring.
                    decoded = self._cipher.decrypt(encrypted)
                data = json.loads(decoded)
                if not isinstance(data, dict):
                    raise ValueError("Media settings document must be an object")
                defaults.update({k: v for k, v in data.items() if k in defaults})
            except (DataKeyringError, InvalidToken, ValueError, OSError) as exc:
                raise RuntimeError("Cannot decrypt media settings; restore the matching data or legacy key") from exc
        return defaults

    def migrate_legacy_settings(self) -> bool:
        """Explicitly migrate settings.enc; normal reads never rewrite legacy data."""
        if not self.keyring or not self.settings_path.exists():
            return False
        if not self._legacy_master_secret and not self.keyring.allow_empty_media_legacy:
            raise RuntimeError(
                "Migrating media settings encrypted with an empty legacy key requires explicit operator confirmation"
            )
        encrypted = self.settings_path.read_bytes()
        if DataKeyring.looks_like_envelope(encrypted):
            self.keyring.decrypt(encrypted, "media_archive.settings")
            return False
        try:
            plaintext = self._cipher.decrypt(encrypted)
            decoded = json.loads(plaintext)
            if not isinstance(decoded, dict):
                raise ValueError("Media settings document must be an object")
        except (InvalidToken, ValueError, OSError) as error:
            raise RuntimeError("Cannot migrate media settings without the matching legacy key") from error
        payload = self.keyring.encrypt(plaintext, "media_archive.settings").encode("utf-8")
        temporary = self.settings_path.with_name(f".{self.settings_path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with open(temporary, "xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.settings_path)
            try:
                os.chmod(self.settings_path, 0o600)
            except OSError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
        self._config()
        return True

    def public_config(self):
        cfg = self._config()
        s3 = cfg.get("s3") or {}
        with self._db() as conn:
            total, bytes_total = conn.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM media").fetchone()
        return {"backend": cfg["backend"], "auto_archive": bool(cfg["auto_archive"]),
                "public_base_url": cfg.get("public_base_url", ""),
                "s3": {"bucket": s3.get("bucket", ""), "region": s3.get("region", "us-east-1"),
                       "endpoint": s3.get("endpoint", ""), "credentials_configured":
                           bool(s3.get("access_key_id") and s3.get("secret_access_key"))},
                "count": total, "original_bytes": bytes_total,
                "supported_backends": ["local", "s3"],
                "compatible_s3_platforms": ["AWS S3", "Cloudflare R2", "MinIO", "S3-compatible endpoints"]}

    def configure(self, incoming: dict):
        cfg = self._config()
        backend = incoming.get("backend", cfg["backend"])
        if backend not in {"local", "s3"}:
            raise ValueError("Only verified local and S3-compatible storage are supported")
        base = str(incoming.get("public_base_url", cfg.get("public_base_url", ""))).strip().rstrip("/")
        if base:
            parsed = urlsplit(base)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
                raise ValueError("Public base URL must be an HTTPS origin without credentials")
        s3 = dict(cfg.get("s3") or {})
        proposed = incoming.get("s3")
        if proposed is not None:
            if not isinstance(proposed, dict):
                raise ValueError("s3 must be an object")
            for key in ("bucket", "region", "endpoint", "access_key_id", "secret_access_key"):
                value = str(proposed.get(key) or "")
                if value:
                    s3[key] = value.strip()
        if s3.get("endpoint"):
            parsed = urlsplit(s3["endpoint"])
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("S3 endpoint must be HTTPS")
        if backend == "s3" and not all(s3.get(k) for k in ("bucket", "access_key_id", "secret_access_key")):
            raise ValueError("S3 bucket, access key and secret key are required")
        cfg.update({"backend": backend, "auto_archive": bool(incoming.get("auto_archive", cfg["auto_archive"])),
                    "public_base_url": base, "s3": s3})
        serialized = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
        payload = (
            self.keyring.encrypt(serialized, "media_archive.settings").encode("utf-8")
            if self.keyring
            else self._cipher.encrypt(serialized)
        )
        tmp = self.settings_path.with_name(f".{self.settings_path.name}.{secrets.token_hex(8)}.tmp")
        try:
            with open(tmp, "xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.settings_path)
            try:
                os.chmod(self.settings_path, 0o600)
            except OSError:
                pass
        finally:
            tmp.unlink(missing_ok=True)
        return self.public_config()

    def _s3(self, cfg):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise RuntimeError("boto3 is required for S3 storage") from exc
        options = cfg["s3"]
        return boto3.client("s3", region_name=options["region"] or "us-east-1",
                            endpoint_url=options["endpoint"] or None,
                            aws_access_key_id=options["access_key_id"],
                            aws_secret_access_key=options["secret_access_key"],
                            config=Config(signature_version="s3v4",
                                          connect_timeout=5, read_timeout=20, retries={"max_attempts": 2}))

    def _write(self, cfg, key: str, content: bytes, mime: str):
        if cfg["backend"] == "local":
            filename = self.root / key
            filename.parent.mkdir(parents=True, exist_ok=True)
            temp = filename.with_suffix(".part")
            try:
                temp.write_bytes(content)
                os.replace(temp, filename)
            except Exception:
                # A failed local write must not leave unpublished .part files.
                temp.unlink(missing_ok=True)
                raise
        else:
            self._s3(cfg).put_object(Bucket=cfg["s3"]["bucket"], Key=key, Body=content,
                                     ContentType=mime, ServerSideEncryption="AES256")

    def _read(self, cfg, backend: str, key: str):
        if backend == "local":
            return (self.root / key).read_bytes()
        if not all(cfg.get("s3", {}).get(k) for k in ("bucket", "access_key_id", "secret_access_key")):
            raise FileNotFoundError("S3 provider credentials are not configured")
        return self._s3(cfg).get_object(Bucket=cfg["s3"]["bucket"], Key=key)["Body"].read()

    def test_backend(self) -> dict:
        """Exercise actual provider write/read/delete, not merely credential syntax."""
        cfg = self._config()
        token = secrets.token_urlsafe(14).replace("-", "").replace("_", "")
        key = "checks/connection-" + token + ".txt"
        payload = b"chat2api-storage-connection-v173"
        uploaded = False
        try:
            self._write(cfg, key, payload, "text/plain")
            uploaded = True
            result = self._read(cfg, cfg["backend"], key)
            if result != payload:
                raise RuntimeError("Storage readback mismatch")
            return {"ok": True, "backend": cfg["backend"], "verified": "write-read"}
        except Exception as exc:
            raise RuntimeError("Storage connection check failed: " + type(exc).__name__) from exc
        finally:
            if uploaded:
                try:
                    if cfg["backend"] == "local":
                        (self.root / key).unlink(missing_ok=True)
                    else:
                        self._s3(cfg).delete_object(Bucket=cfg["s3"]["bucket"], Key=key)
                except Exception:
                    pass

    @staticmethod
    def _render_thumbnail(raw: bytes, mime: str) -> tuple[int, int, bytes]:
        if mime not in ALLOWED or not raw or len(raw) > MAX_BYTES:
            raise ValueError("Unsupported image format or image too large")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                if image.width * image.height > 36_000_000:
                    raise ValueError("Image pixel count exceeds the limit")
                expected_format = {"image/png": "PNG", "image/jpeg": "JPEG",
                                   "image/webp": "WEBP", "image/gif": "GIF"}[mime]
                if image.format != expected_format:
                    raise ValueError("Image bytes do not match the declared MIME type")
                width, height = image.size
                thumb = ImageOps.exif_transpose(image)
                thumb.thumbnail((384, 384), Image.Resampling.LANCZOS)
                if thumb.mode not in {"RGB", "RGBA"}:
                    thumb = thumb.convert("RGBA")
                output = io.BytesIO()
                thumb.save(output, "WEBP", quality=78, method=4)
                preview = output.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError("Image did not pass raster validation") from exc
        return width, height, preview

    def _discard_unpublished_image(self, cfg: dict, keys: tuple[str, str]) -> None:
        """Compensate failed immutable-object writes before a media row exists.

        These keys are random, request-owned object names generated by store_image;
        they are not user-provided paths and have not been published in an API URL.
        """
        for key in keys:
            try:
                if cfg["backend"] == "local":
                    target = self.root / key
                    target.unlink(missing_ok=True)
                    target.with_suffix(".part").unlink(missing_ok=True)
                else:
                    self._s3(cfg).delete_object(Bucket=cfg["s3"]["bucket"], Key=key)
            except Exception:
                # Keep the original write error, but retain a visible cleanup
                # failure for operators instead of silently hiding an orphan.
                import logging
                logging.getLogger(__name__).exception(
                    "Failed to discard unpublished media object"
                )

    def store_image(self, raw: bytes, mime: str, owner: str, request_id: str,
                    *, stop=None):
        width, height, preview = self._render_thumbnail(raw, mime)
        cfg = self._config()
        if not cfg["auto_archive"]:
            raise ValueError("Media auto-archiving is disabled; cannot expose inline Base64")
        media_id = "med_" + secrets.token_urlsafe(15).replace("-", "").replace("_", "")
        token = secrets.token_urlsafe(24)
        origin = f"objects/{media_id}"
        full_key = origin + "." + ALLOWED[mime]
        thumb_key = origin + "-thumb.webp"
        try:
            if stop is not None:
                stop.checkpoint()
            self._write(cfg, full_key, raw, mime)
            if stop is not None:
                stop.checkpoint()
            self._write(cfg, thumb_key, preview, "image/webp")
            if stop is not None:
                stop.checkpoint()
            # A provider's successful PUT is not proof of byte-preserving
            # storage. Generated-file archives already verify their complete
            # readback before creating a download manifest; image originals
            # need that same invariant. Otherwise a partial S3/local write
            # can publish a valid-looking 1:1 image URL backed by WRONG bytes.
            # Check both full-resolution original and its separate thumbnail
            # through the exact backend read path used for downloads.
            for key, expected in ((full_key, raw), (thumb_key, preview)):
                stored = self._read(cfg, cfg["backend"], key)
                if (len(stored) != len(expected)
                    or hashlib.sha256(stored).digest() !=
                       hashlib.sha256(expected).digest()):
                    raise ValueError("Media storage readback integrity mismatch")
                if stop is not None:
                    stop.checkpoint()
            with self._db() as conn:
                conn.execute("""INSERT INTO media
                    (id, token, owner, request_id, mime, size, width, height,
                     backend, object_key, thumb_key, created_at, sha256)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    media_id, token, owner, request_id, mime, len(raw), width, height,
                    cfg["backend"], full_key, thumb_key,
                    datetime.now(timezone.utc).isoformat(),
                    hashlib.sha256(raw).hexdigest()))
        except Exception:
            # The original, thumbnail and index are one logical publication.
            # Never strand bytes if the second upload or SQLite insert fails.
            self._discard_unpublished_image(cfg, (full_key, thumb_key))
            raise
        return {"id": media_id, "token": token, "mime": mime, "bytes": len(raw),
                "width": width, "height": height, "backend": cfg["backend"],
                "sha256": hashlib.sha256(raw).hexdigest()}

    def rewrite_markdown(self, source: str, owner: str, request_id: str,
                         base_url: str, *, stop=None):
        output = []
        last = 0
        rows = []
        stored_ids = []
        complete = False
        try:
            for match in MEDIA_PATTERN.finditer(source):
                if stop is not None:
                    stop.checkpoint()
                image_url = match.group(2)
                try:
                    if image_url.startswith("data:image/"):
                        header, encoded = image_url.split(",", 1)
                        mime = header[5:].split(";", 1)[0].lower()
                        # A pasted model reply may contain an enormous inline
                        # image. Reject before b64decode allocates another binary
                        # buffer; store_image's MAX_BYTES check happens too late
                        # to protect the Server from the extra allocation.
                        max_encoded = 4 * ((MAX_BYTES + 2) // 3)
                        if len(encoded) > max_encoded:
                            raise ValueError("Inline image exceeds archive limit")
                        raw = base64.b64decode(encoded, validate=True)
                    else:
                        remote = urlsplit(image_url)
                        host = (remote.hostname or "").lower()
                        trusted = host in {"chatgpt.com", "chat.openai.com"} or any(
                            host.endswith(suffix) for suffix in (
                                ".chatgpt.com", ".openai.com", ".oaiusercontent.com", ".oaistatic.com"))
                        if not trusted or remote.scheme != "https" or remote.username or remote.password:
                            continue  # No arbitrary URL fetching or local SSRF.
                        with httpx.Client(timeout=httpx.Timeout(18, connect=5), follow_redirects=False,
                                          trust_env=False) as client:
                            with client.stream("GET", image_url, headers={"Accept": "image/*"}) as response:
                                response.raise_for_status()
                                mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                                if mime not in ALLOWED:
                                    raise ValueError("Remote image has unsupported MIME")
                                if int(response.headers.get("content-length", "0") or "0") > MAX_BYTES:
                                    raise ValueError("Remote image is too large")
                                parts, size = [], 0
                                for chunk in response.iter_bytes():
                                    size += len(chunk)
                                    if size > MAX_BYTES:
                                        raise ValueError("Remote image exceeds archive limit")
                                    parts.append(chunk)
                                raw = b"".join(parts)
                    if stop is not None:
                        stop.checkpoint()
                        record = self.store_image(
                            raw, mime, owner, request_id, stop=stop
                        )
                    else:
                        record = self.store_image(raw, mime, owner, request_id)
                    # Record immediately, even if late timeout is signaled
                    # while the storage backend was completing a blocking
                    # write. The rollback finally owns this exact media ID.
                    stored_ids.append(record["id"])
                    if stop is not None:
                        stop.checkpoint()
                except (ValueError, OSError, httpx.HTTPError) as exc:
                    raise ValueError("Generated image could not be transferred to configured storage") from exc
                prefix = (self._config().get("public_base_url") or base_url.rstrip("/"))
                asset = f"{prefix}/api/media/{record['id']}/{record['token']}"
                output.append(source[last:match.start()])
                # Standard API clients receive full-resolution content. The admin
                # playground can derive /thumbnail for lightweight preview without
                # replacing the original download URL in the assistant response.
                output.append(f"![{match.group(1) or '生成图片'}]({asset})")
                rows.append({**{k: record[k] for k in ("id", "mime", "bytes", "width", "height", "backend", "sha256")},
                             "url": asset, "thumbnail_url": asset + "/thumbnail"})
                last = match.end()
            output.append(source[last:])
            complete = True
            return "".join(output), rows

        finally:
            if not complete:
                # A later invalid image must not leave earlier images from a
                # failed reply forever indexed but unreachable by the client.
                for media_id in reversed(stored_ids):
                    try:
                        self.delete(media_id)
                    except Exception:
                        import logging
                        logging.getLogger(__name__).exception(
                            "Failed to roll back media from incomplete reply"
                        )

    def describe(self, media_id: str, owner: str, *, admin: bool = False) -> dict:
        """Read metadata without exposing another API key's opaque media token."""
        with self._db() as conn:
            row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        if row is None or (row["owner"] != owner and not admin):
            raise KeyError("Unknown media")
        return {"id": row["id"], "token": row["token"],
                "request_id": row["request_id"], "mime_type": row["mime"],
                "bytes": row["size"], "width": row["width"], "height": row["height"],
                "backend": row["backend"], "created_at": row["created_at"]}

    def load(self, media_id: str, token: str, thumbnail: bool = False):
        with self._db() as conn:
            row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        if not row or not secrets.compare_digest(row["token"], token):
            raise KeyError("Unknown media")
        key = row["thumb_key"] if thumbnail else row["object_key"]
        payload = self._read(self._config(), row["backend"], key)
        if not thumbnail:
            # The original, NOT the lossy WebP preview, is the source of
            # truth for the archived full-resolution image. A media record
            # proves ownership of its URL, not that bytes fetched today still
            # match those published originally. Generated-file downloads
            # already apply this read-time verification.
            if len(payload) != row["size"]:
                raise ValueError("Media original byte-length integrity mismatch")
            checksum = row["sha256"]
            if checksum and hashlib.sha256(payload).hexdigest() != checksum:
                raise ValueError("Media original SHA256 integrity mismatch")
            # checksum='' marks a pre-migration legacy record. Do not assert
            # SHA verification for it or silently trust a computed digest.
        return payload, "image/webp" if thumbnail else row["mime"]

    def delete(self, media_id: str) -> bool:
        """Delete one immutable object and its thumbnail; old access URLs expire."""
        with self._db() as conn:
            row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
            if row is None:
                return False
            keys = [row["object_key"], row["thumb_key"]]
            cfg = self._config()
            if row["backend"] == "local":
                for key in keys:
                    target = (self.root / key).resolve()
                    if not target.is_relative_to(self.root.resolve()):
                        raise ValueError("Invalid media storage path")
                    target.unlink(missing_ok=True)
            else:
                client = self._s3(cfg)
                for key in keys:
                    client.delete_object(Bucket=cfg["s3"]["bucket"], Key=key)
            conn.execute("DELETE FROM media WHERE id = ?", (media_id,))
        return True

    def repair_thumbnails(self, limit: int = 100, offset: int = 0) -> dict:
        """Repair existing media thumbnails in bounded, owner-preserving batches.

        This does not regenerate content through a Worker, change an original,
        reissue an access token, or change the storage backend of an old record.
        """
        limit = min(200, max(1, int(limit)))
        offset = max(0, int(offset))
        with self._db() as conn:
            rows = conn.execute(
                """SELECT id,mime,size,sha256,width,height,backend,object_key,thumb_key
                   FROM media ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        cfg = self._config()
        report = {"scanned": len(rows), "intact": 0, "repaired": 0,
                  "failed": 0, "failures": [], "next_offset": offset + len(rows) if len(rows) == limit else None}
        for row in rows:
            try:
                missing_or_invalid = False
                try:
                    preview = self._read(cfg, row["backend"], row["thumb_key"])
                except FileNotFoundError:
                    missing_or_invalid = True
                except Exception as error:
                    remote_code = str(getattr(error, "response", {}).get("Error", {}).get("Code", ""))
                    if remote_code in {"NoSuchKey", "404", "NotFound"}:
                        missing_or_invalid = True
                    else:
                        raise
                if not missing_or_invalid:
                    try:
                        with Image.open(io.BytesIO(preview)) as image:
                            image.verify()
                            if image.format != "WEBP":
                                missing_or_invalid = True
                    except (OSError, UnidentifiedImageError, ValueError):
                        missing_or_invalid = True
                if not missing_or_invalid:
                    report["intact"] += 1
                    continue
                original = self._read(cfg, row["backend"], row["object_key"])
                # A missing/corrupt thumbnail must never be regenerated from
                # source bytes that fail the digest stored on publication.
                # For legacy SHA-less rows preserve size-only verification,
                # explicitly without fabricating historical source identity.
                if len(original) != row["size"]:
                    raise ValueError("Thumbnail source original byte-length mismatch")
                if (row["sha256"]
                        and hashlib.sha256(original).hexdigest() != row["sha256"]):
                    raise ValueError("Thumbnail source original SHA256 mismatch")
                width, height, thumbnail = self._render_thumbnail(original, row["mime"])
                if width != row["width"] or height != row["height"]:
                    raise ValueError("Archived original dimensions do not match metadata")
                backend_cfg = {**cfg, "backend": row["backend"]}
                self._write(backend_cfg, row["thumb_key"], thumbnail, "image/webp")
                # A successful provider write does not mean the repaired
                # thumbnail is available or byte-correct. A corrupt PUT must
                # not increment the administrator's repaired counter, nor
                # create false visual-completeness evidence for this image.
                verified = self._read(backend_cfg, row["backend"], row["thumb_key"])
                if (len(verified) != len(thumbnail)
                    or hashlib.sha256(verified).digest() !=
                       hashlib.sha256(thumbnail).digest()):
                    raise ValueError("Repaired thumbnail storage integrity mismatch")
                report["repaired"] += 1
            except Exception as error:
                report["failed"] += 1
                report["failures"].append({"media_id": row["id"], "reason": type(error).__name__})
        return report

    def list_items(self, limit: int = 80):
        with self._db() as conn:
            items = conn.execute("""SELECT id,owner,request_id,mime,size,width,height,backend,created_at
                                    FROM media ORDER BY created_at DESC LIMIT ?""", (min(200, max(1, limit)),)).fetchall()
            return [dict(i) for i in items]
