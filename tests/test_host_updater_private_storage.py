from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import chat2api_update_storage as storage


HELPER = Path(__file__).resolve().parents[1] / "scripts" / "chat2api_update_storage.py"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and permission assertions")
def test_updater_directory_creation_rejects_symlink_components(tmp_path):
    real = tmp_path / "real-data"
    real.mkdir()
    linked = tmp_path / "linked-data"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlinks"):
        storage.validate_directory(linked, create=True)

    unsafe = tmp_path / "unsafe-data"
    unsafe.mkdir()
    unsafe.chmod(0o777)
    storage.validate_directory(unsafe, create=True)
    assert stat.S_IMODE(unsafe.stat().st_mode) == 0o755


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and hard-link assertions")
def test_updater_json_reads_reject_symlink_and_hardlink_without_touching_targets(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"status":"outside"}', encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(outside)

    with pytest.raises(RuntimeError, match="symlinks"):
        storage.read_json(linked)
    assert outside.read_text(encoding="utf-8") == '{"status":"outside"}'

    hardlinked = tmp_path / "hardlinked.json"
    os.link(outside, hardlinked)
    with pytest.raises(RuntimeError, match="single-link"):
        storage.read_json(hardlinked)
    assert outside.read_text(encoding="utf-8") == '{"status":"outside"}'


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and hard-link assertions")
def test_updater_atomic_json_write_rejects_links_and_keeps_private_mode(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"keep":true}', encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(outside)

    with pytest.raises(RuntimeError, match="symlinks"):
        storage.write_json_atomic(linked, {"replace": True})
    assert outside.read_text(encoding="utf-8") == '{"keep":true}'

    hardlinked = tmp_path / "hardlinked.json"
    os.link(outside, hardlinked)
    with pytest.raises(RuntimeError, match="single-link"):
        storage.write_json_atomic(hardlinked, {"replace": True})
    assert outside.read_text(encoding="utf-8") == '{"keep":true}'

    private = tmp_path / "private.json"
    storage.write_json_atomic(private, {"ok": True})
    assert json.loads(private.read_text(encoding="utf-8")) == {"ok": True}
    assert stat.S_IMODE(private.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX hard-link assertions")
def test_log_truncation_checks_link_count_before_modifying_file(tmp_path):
    target = tmp_path / "outside.log"
    target.write_text("preserve this log", encoding="utf-8")
    hardlink = tmp_path / "updater.log"
    os.link(target, hardlink)

    with pytest.raises(RuntimeError, match="single-link"):
        storage.truncate_private_file(hardlink)
    assert target.read_text(encoding="utf-8") == "preserve this log"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission assertions")
def test_log_tee_appends_safely_and_mirrors_output(tmp_path):
    log = tmp_path / "admin-update.log"
    result = subprocess.run(
        [sys.executable, str(HELPER), "tee-log", str(log)],
        input=b"line one\nline two\n",
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert result.stdout == b"line one\nline two\n"
    assert log.read_bytes() == b"line one\nline two\n"
    assert stat.S_IMODE(log.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission assertions")
def test_updater_cli_validates_request_id_and_prepares_nontruncating_lock(tmp_path):
    request = tmp_path / "request.json"
    storage.write_json_atomic(request, {
        "request_id": "upd_0123456789abcdef01",
        "use_build_cache": False,
    })
    request_id = subprocess.run(
        [sys.executable, str(HELPER), "read-request-id", str(request)],
        capture_output=True, text=True, check=True, timeout=10,
    )
    use_cache = subprocess.run(
        [sys.executable, str(HELPER), "read-build-cache", str(request)],
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert request_id.stdout.strip() == "upd_0123456789abcdef01"
    assert use_cache.stdout.strip() == "false"

    storage.write_json_atomic(request, {"request_id": "../../etc/passwd"})
    invalid = subprocess.run(
        [sys.executable, str(HELPER), "read-request-id", str(request)],
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert invalid.stdout.strip() == ""

    lock = tmp_path / ".admin-update.lock"
    storage.prepare_lock(lock)
    assert lock.read_bytes() == b"\0"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_updater_json_reader_enforces_bounded_input(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (storage.MAX_JSON_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        storage.read_json(path)
