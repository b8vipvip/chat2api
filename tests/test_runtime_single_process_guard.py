from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import multiprocessing

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


def _hold_runtime_lock(data_dir: str, acquired, release):
    guard = SingleProcessRuntimeGuard(data_dir)
    guard.acquire()
    acquired.set()
    release.wait(10)
    guard.release()


def test_os_lock_excludes_another_process_and_recovers_after_abrupt_exit(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    child = context.Process(
        target=_hold_runtime_lock,
        args=(str(tmp_path), acquired, release),
    )
    child.start()
    try:
        assert acquired.wait(10), "child process did not acquire runtime lock"
        guard = SingleProcessRuntimeGuard(tmp_path)
        with pytest.raises(RuntimeTopologyError, match="already owns"):
            guard.acquire()
        child.terminate()
        child.join(10)
        assert not child.is_alive()
        guard.acquire()
        guard.release()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(10)


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
    with pytest.raises(RuntimeTopologyError, match="lock path cannot be a symbolic link"):
        SingleProcessRuntimeGuard(tmp_path).acquire()



def test_installer_wraps_and_preserves_existing_lifespan(tmp_path: Path):
    from contextlib import asynccontextmanager

    events = []

    @asynccontextmanager
    async def existing_lifespan(app):
        events.append(("startup", app.state.single_process_runtime_guard._fd is not None))
        yield
        events.append(("shutdown", app.state.single_process_runtime_guard._fd is not None))

    app = FastAPI(lifespan=existing_lifespan)
    app.state.settings = SimpleNamespace(data_dir=tmp_path)
    install_single_process_runtime_guard(app)
    with TestClient(app):
        assert events == [("startup", True)]
    assert events == [("startup", True), ("shutdown", True)]
    assert app.state.single_process_runtime_guard._fd is None


def test_production_entry_installs_guard_before_startup():
    from app.entry import app

    guard = app.state.single_process_runtime_guard
    try:
        assert app.state.single_process_runtime_guard_installed is True
        assert isinstance(guard, SingleProcessRuntimeGuard)
        assert guard._fd is not None
    finally:
        # Import-time acquisition protects store construction. The real server
        # retains the descriptor until ASGI shutdown; tests release their import.
        guard.release()


def test_production_entry_acquires_guard_before_importing_main():
    import app.entry as entry
    from pathlib import Path

    source = Path(entry.__file__).read_text(encoding="utf-8")
    assert source.index("_runtime_guard.acquire()") < source.index("from .main import app")
    assert source.index("_runtime_guard.acquire()") < source.index("install_single_process_runtime_guard(app")

def test_installer_reuses_a_pre_acquired_guard(tmp_path: Path):
    guard = SingleProcessRuntimeGuard(tmp_path)
    guard.acquire()
    app = FastAPI()
    app.state.settings = SimpleNamespace(data_dir=tmp_path)
    install_single_process_runtime_guard(app, guard=guard)
    assert app.state.single_process_runtime_guard is guard
    with TestClient(app):
        assert guard._fd is not None
    assert guard._fd is None


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
