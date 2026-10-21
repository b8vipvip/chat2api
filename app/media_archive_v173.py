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
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, ImageOps, UnidentifiedImageError

IMAGE_PATTERN = re.compile(
    r"!\[([^\]\n]{0,200})\]\(data:image/(png|jpeg|webp|gif);base64,([A-Za-z0-9+/=]+)\)", re.I,
)
ALLOWED = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
MAX_BYTES = 20 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 36_000_000


class MediaArchive:
    def __init__(self, data_dir: Path, master_secret: str):
        self.root = Path(data_dir) / "media_archive"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "index.sqlite3"
        self.settings_path = self.root / "settings.enc"
        secret_key = hashlib.sha256(("chat2api:media:settings:v1:" + master_secret).encode()).digest()
        self._cipher = Fernet(base64.urlsafe_b64encode(secret_key))
        self._init_db()

    def _db(self):
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS media (
                id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
                request_id TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL,
                backend TEXT NOT NULL, object_key TEXT NOT NULL,
                thumb_key TEXT NOT NULL, created_at TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS media_created_idx ON media(created_at DESC)")

    def _config(self):
        defaults = {"backend": "local", "auto_archive": True, "public_base_url": "",
                    "s3": {"bucket": "", "region": "us-east-1", "endpoint": "",
                           "access_key_id": "", "secret_access_key": ""}}
        if self.settings_path.exists():
            try:
                decoded = self._cipher.decrypt(self.settings_path.read_bytes())
                data = json.loads(decoded)
                if isinstance(data, dict):
                    defaults.update({k: v for k, v in data.items() if k in defaults})
            except (InvalidToken, ValueError, OSError) as exc:
                raise RuntimeError("Cannot decrypt media settings; verify the administrator key") from exc
        return defaults

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
        payload = self._cipher.encrypt(json.dumps(cfg, ensure_ascii=False).encode())
        tmp = self.settings_path.with_suffix(".tmp")
        with open(tmp, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
        os.replace(tmp, self.settings_path)
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
            temp.write_bytes(content)
            os.replace(temp, filename)
        else:
            self._s3(cfg).put_object(Bucket=cfg["s3"]["bucket"], Key=key, Body=content,
                                     ContentType=mime, ServerSideEncryption="AES256")

    def _read(self, cfg, backend: str, key: str):
        if backend == "local":
            return (self.root / key).read_bytes()
        if not all(cfg.get("s3", {}).get(k) for k in ("bucket", "access_key_id", "secret_access_key")):
            raise FileNotFoundError("S3 provider credentials are not configured")
        return self._s3(cfg).get_object(Bucket=cfg["s3"]["bucket"], Key=key)["Body"].read()

    def store_image(self, raw: bytes, mime: str, owner: str, request_id: str):
        if mime not in ALLOWED or not raw or len(raw) > MAX_BYTES:
            raise ValueError("Unsupported image format or image too large")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                if image.width * image.height > 36_000_000:
                    raise ValueError("Image pixel count exceeds the limit")
                if image.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    raise ValueError("Image bytes do not match an allowed raster format")
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
        cfg = self._config()
        if not cfg["auto_archive"]:
            raise ValueError("Media auto-archiving is disabled; cannot expose inline Base64")
        media_id = "med_" + secrets.token_urlsafe(15).replace("-", "").replace("_", "")
        token = secrets.token_urlsafe(24)
        origin = f"objects/{media_id}"
        full_key = origin + "." + ALLOWED[mime]
        thumb_key = origin + "-thumb.webp"
        self._write(cfg, full_key, raw, mime)
        self._write(cfg, thumb_key, preview, "image/webp")
        with self._db() as conn:
            conn.execute("INSERT INTO media VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                media_id, token, owner, request_id, mime, len(raw), width, height,
                cfg["backend"], full_key, thumb_key, datetime.now(timezone.utc).isoformat()))
        return {"id": media_id, "token": token, "mime": mime, "bytes": len(raw),
                "width": width, "height": height, "backend": cfg["backend"]}

    def rewrite_markdown(self, source: str, owner: str, request_id: str, base_url: str):
        output = []
        last = 0
        rows = []
        for match in IMAGE_PATTERN.finditer(source):
            encoded = match.group(3)
            try:
                raw = base64.b64decode(encoded, validate=True)
                record = self.store_image(raw, "image/" + match.group(2).lower(), owner, request_id)
            except (ValueError, OSError) as exc:
                raise ValueError("Generated image could not be transferred to configured storage") from exc
            prefix = (self._config().get("public_base_url") or base_url.rstrip("/"))
            asset = f"{prefix}/api/media/{record['id']}/{record['token']}"
            output.append(source[last:match.start()])
            output.append(f"![{match.group(1) or '生成图片'}]({asset}/thumbnail)")
            rows.append({**{k: record[k] for k in ("id", "mime", "bytes", "width", "height", "backend")},
                         "url": asset, "thumbnail_url": asset + "/thumbnail"})
            last = match.end()
        output.append(source[last:])
        return "".join(output), rows

    def load(self, media_id: str, token: str, thumbnail: bool = False):
        with self._db() as conn:
            row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        if not row or not secrets.compare_digest(row["token"], token):
            raise KeyError("Unknown media")
        key = row["thumb_key"] if thumbnail else row["object_key"]
        payload = self._read(self._config(), row["backend"], key)
        return payload, "image/webp" if thumbnail else row["mime"]

    def list_items(self, limit: int = 80):
        with self._db() as conn:
            items = conn.execute("""SELECT id,owner,request_id,mime,size,width,height,backend,created_at
                                    FROM media ORDER BY created_at DESC LIMIT ?""", (min(200, max(1, limit)),)).fetchall()
            return [dict(i) for i in items]
