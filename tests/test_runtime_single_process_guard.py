from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.runtime_single_process_guard import (
    RuntimeTopologyError,
    SingleProcessRuntimeGuard,
    install_single_process_runtime_guard,
)


def test_guard_exclusively_owns_shared_data_directory_until_release(tmp_path: Path):
    first = SingleProcessRuntimeGuard(tmp_path)
    second = SingleProcessRuntimeGuard(tmp_path)

    first.acquire()
    assert first.lock_path.exists()
    assert first.lock_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeTopologyError, match="already owns"):
        second.acquire()

    first.release()
    second.acquire()
    second.release()
    # The lock file is deliberately retained. Unlinking a locked inode can let
    # a third process create a second inode and bypass the exclusion.
    assert first.lock_path.exists()


def test_guard_rejects_known_multi_worker_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(RuntimeTopologyError, match="requires one server process"):
        SingleProcessRuntimeGuard(tmp_path).acquire()
    assert not (tmp_path / ".chat2api-runtime.lock").exists()


def test_guard_rejects_symlinked_data_directory(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeTopologyError, match="data directory cannot be a symbolic link"):
        SingleProcessRuntimeGuard(linked).acquire()


def test_guard_rejects_symlinked_lock_file(tmp_path: Path):
    target = tmp_path / "other.lock"
    target.write_text("pid=123\n", encoding="ascii")
    (tmp_path / ".chat2api-runtime.lock").symlink_to(target)
    with pytest.raises(RuntimeTopologyError, match="Unable to open"):
        SingleProcessRuntimeGuard(tmp_path).acquire()


def test_fastapi_installer_holds_and_releases_guard_with_lifespan(tmp_path: Path):
    app = FastAPI()
    app.state.settings = SimpleNamespace(data_dir=tmp_path)
    install_single_process_runtime_guard(app)
    install_single_process_runtime_guard(app)

    second = FastAPI()
    second.state.settings = SimpleNamespace(data_dir=tmp_path)
    install_single_process_runtime_guard(second)

    with TestClient(app):
        with pytest.raises(RuntimeTopologyError, match="already owns"):
            with TestClient(second):
                pass

    with TestClient(second):
        assert second.state.single_process_runtime_guard._fd is not None
    assert second.state.single_process_runtime_guard._fd is None
