"""Original raster downloads must match the SHA persisted at publication.

Migration is additive and leaves historical bearer URLs intact. Old rows
have no trusted source SHA; never backfill a digest from their current bytes.
"""
import hashlib
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.media_archive_v173 import MediaArchive


def png():
    out = io.BytesIO()
    Image.new("RGB", (190, 80), (102, 172, 40)).save(out, "PNG")
    return out.getvalue()


def media_path(store, media_id):
    return store.root / "objects" / f"{media_id}.png"


def test_original_sha_survives_restart_and_corrupted_download_is_refused(tmp_path):
    store = MediaArchive(tmp_path, "master")
    raw = png()
    record = store.store_image(raw, "image/png", "owner", "req_sha_v324")
    with store._db() as conn:
        row = conn.execute("SELECT sha256,size FROM media WHERE id=?", (record["id"],)).fetchone()
    assert row["sha256"] == record["sha256"] == hashlib.sha256(raw).hexdigest()
    assert row["size"] == len(raw)

    reopened = MediaArchive(tmp_path, "master")
    assert reopened.load(record["id"], record["token"])[0] == raw
    thumb = reopened.load(record["id"], record["token"], thumbnail=True)[0]
    assert thumb != raw and thumb.startswith(b"RIFF")

    # Same-length substitution defeats metadata size checks but not SHA256.
    media_path(store, record["id"]).write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises(ValueError, match="SHA256 integrity mismatch"):
        reopened.load(record["id"], record["token"])
    # Preview is a separate object and must remain addressable.
    assert reopened.load(record["id"], record["token"], thumbnail=True)[0] == thumb
    assert reopened.list_items(), "corrupt object does not erase manifest evidence"


def test_truncated_original_rejected_even_for_legacy_compatibility(tmp_path):
    store = MediaArchive(tmp_path, "master")
    raw = png()
    record = store.store_image(raw, "image/png", "owner", "req_small_v324")
    media_path(store, record["id"]).write_bytes(raw[:-1])
    with pytest.raises(ValueError, match="byte-length integrity mismatch"):
        store.load(record["id"], record["token"])


def test_existing_v173_database_migrates_without_forging_sha(tmp_path):
    root = tmp_path / "media_archive"
    (root / "objects").mkdir(parents=True)
    db = root / "index.sqlite3"
    raw = png()
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE media (
            id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, owner TEXT NOT NULL,
            request_id TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
            width INTEGER NOT NULL, height INTEGER NOT NULL,
            backend TEXT NOT NULL, object_key TEXT NOT NULL,
            thumb_key TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        conn.execute("INSERT INTO media VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            "med_legacy00001", "legacy-token", "old-owner", "old-request",
            "image/png", len(raw), 190, 80, "local",
            "objects/med_legacy00001.png",
            "objects/med_legacy00001-thumb.webp", "2026-01-01",
        ))
    (root / "objects" / "med_legacy00001.png").write_bytes(raw)
    store = MediaArchive(tmp_path, "master")
    with store._db() as conn:
        sha = conn.execute("SELECT sha256 FROM media WHERE id='med_legacy00001'").fetchone()[0]
    assert sha == "", "do not certify old bytes by generating a digest on read"
    assert store.load("med_legacy00001", "legacy-token")[0] == raw
    # Additive migration is idempotent even after repeated server launches.
    assert MediaArchive(tmp_path, "master").load("med_legacy00001", "legacy-token")[0] == raw
    # Legacy rows can still check length, but are NOT described as SHA verified.
    (root / "objects" / "med_legacy00001.png").write_bytes(raw[:-2])
    with pytest.raises(ValueError, match="byte-length"):
        store.load("med_legacy00001", "legacy-token")


def test_api_media_route_never_returns_http_200_for_corrupted_original(tmp_path):
    app = create_app(Settings(
        CHAT2API_API_KEY="test-key", CHAT2API_PAIRING_CODE="test-pair",
        CHAT2API_DATA_DIR=tmp_path,
    ))
    with TestClient(app) as client:
        raw = png()
        store = app.state.media_archive
        record = store.store_image(raw, "image/png", "owner", "req_http_v324")
        route = f"/api/media/{record['id']}/{record['token']}"
        assert client.get(route).status_code == 200
        media_path(store, record["id"]).write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        response = client.get(route)
        assert response.status_code == 503
        assert b"Media storage integrity unavailable" in response.content
        assert response.content != raw
        assert client.get(route + "/thumbnail").status_code == 200
