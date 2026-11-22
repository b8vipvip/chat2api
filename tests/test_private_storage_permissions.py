from __future__ import annotations

import asyncio
import os
import stat

import pytest
from PIL import Image

from app.media_archive_v173 import MediaArchive
from app.telemetry import TelemetryStore


def _png() -> bytes:
    import io

    output = io.BytesIO()
    Image.new("RGB", (24, 24), (30, 120, 210)).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_telemetry_history_is_private_and_rejects_symlinks(tmp_path):
    telemetry = TelemetryStore(tmp_path / "private")
    asyncio.run(telemetry.upsert({
        "request_id": "req_private_history",
        "status": "completed",
        "prompt": "private prompt",
        "response_text": "private response",
    }))
    assert stat.S_IMODE(telemetry.path.stat().st_mode) == 0o600

    linked_root = tmp_path / "linked"
    linked_root.mkdir()
    target = tmp_path / "outside-history.jsonl"
    target.write_text('{"request_id":"must-not-be-read"}\\n', encoding="utf-8")
    (linked_root / "request_history.jsonl").symlink_to(target)

    with pytest.raises(RuntimeError, match="symbolic link"):
        asyncio.run(TelemetryStore(linked_root).load())
    assert target.read_text(encoding="utf-8") == '{"request_id":"must-not-be-read"}\\n'


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_media_archive_root_index_and_local_objects_are_private(tmp_path):
    store = MediaArchive(tmp_path / "media-data", "master")
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.db_path.stat().st_mode) == 0o600

    record = store.store_image(_png(), "image/png", "owner-key", "req_private_media")
    objects = store.root / "objects"
    assert stat.S_IMODE(objects.stat().st_mode) == 0o700
    full = objects / f"{record['id']}.png"
    thumbnail = objects / f"{record['id']}-thumb.webp"
    assert stat.S_IMODE(full.stat().st_mode) == 0o600
    assert stat.S_IMODE(thumbnail.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_media_archive_rejects_symlinked_root_and_database(tmp_path):
    data_dir = tmp_path / "root-link-data"
    data_dir.mkdir()
    outside_root = tmp_path / "outside-media-root"
    outside_root.mkdir()
    (data_dir / "media_archive").symlink_to(outside_root, target_is_directory=True)
    with pytest.raises(RuntimeError, match="root cannot be a symbolic link"):
        MediaArchive(data_dir, "master")

    database_dir = tmp_path / "db-link-data" / "media_archive"
    database_dir.mkdir(parents=True)
    target = tmp_path / "outside-index.sqlite3"
    target.write_bytes(b"must remain untouched")
    (database_dir / "index.sqlite3").symlink_to(target)
    with pytest.raises(RuntimeError, match="symbolic links"):
        MediaArchive(database_dir.parent, "master")
    assert target.read_bytes() == b"must remain untouched"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission and symlink assertions")
def test_media_archive_local_reader_rejects_symlinked_object(tmp_path):
    store = MediaArchive(tmp_path, "master")
    objects = store.root / "objects"
    objects.mkdir(mode=0o700)
    target = tmp_path / "outside-image.bin"
    target.write_bytes(_png())
    (objects / "linked.png").symlink_to(target)

    with pytest.raises(RuntimeError, match="symbolic links"):
        store._read({}, "local", "objects/linked.png")
    assert target.read_bytes() == _png()
