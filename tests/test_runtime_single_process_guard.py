from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
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
    if os.name == "nt":
        # Windows permissions are inherited from the access-controlled data
        # directory; chmod does not provide POSIX 0600 semantics there.
        assert os.access(first.lock_path, os.W_OK)
    else:
        assert first.lock_path.stat().st_mode & 0o777 == 0o600
    # Cross-process contention is covered by the spawned-process regression
    # below. Windows byte-range locks deny even same-process reads of the
    # locked byte range, so inspect the marker only after releasing the lock.
    first.release()
    marker = first.lock_path.read_bytes()
    assert marker.startswith(f"pid={os.getpid()}\n".encode("ascii"))
    assert len(marker) == 32
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


def _try_acquire_inherited_guard(guard, result_queue):
    try:
        guard.acquire()
    except RuntimeTopologyError as exc:
        result_queue.put(("rejected", str(exc)))
    else:
        result_queue.put(("acquired", ""))
        guard.release()


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="fork is unavailable")
def test_preloaded_fork_worker_cannot_reuse_parent_runtime_lock(tmp_path: Path):
    guard = SingleProcessRuntimeGuard(tmp_path)
    guard.acquire()
    context = multiprocessing.get_context("fork")
    result_queue = context.Queue()
    child = context.Process(target=_try_acquire_inherited_guard, args=(guard, result_queue))
    try:
        child.start()
        child.join(10)
        assert not child.is_alive()
        status, message = result_queue.get(timeout=2)
        assert status == "rejected"
        assert "inherited across fork" in message
        second = SingleProcessRuntimeGuard(tmp_path)
        with pytest.raises(RuntimeTopologyError, match="already owns"):
            second.acquire()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(10)
        guard.release()


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


def test_guard_rejects_hard_linked_lock_file_without_chmodding_target(tmp_path: Path):
    target = tmp_path / "sensitive.json"
    target.write_text('{"private":true}', encoding="utf-8")
    original_mode = target.stat().st_mode & 0o777
    lock_path = tmp_path / ".chat2api-runtime.lock"
    try:
        os.link(target, lock_path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hard links are unavailable on this filesystem: {exc}")

    with pytest.raises(RuntimeTopologyError, match="lock path cannot be hard-linked"):
        SingleProcessRuntimeGuard(tmp_path).acquire()

    assert target.stat().st_mode & 0o777 == original_mode
    assert target.read_text(encoding="utf-8") == '{"private":true}'


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


def test_production_entry_installs_guard_before_startup(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    script = """
from app.entry import app
from app.runtime_single_process_guard import SingleProcessRuntimeGuard
guard = app.state.single_process_runtime_guard
assert app.state.single_process_runtime_guard_installed is True
assert isinstance(guard, SingleProcessRuntimeGuard)
assert guard._fd is not None
guard.release()
"""
    env = os.environ.copy()
    env["CHAT2API_DATA_DIR"] = str(tmp_path / "isolated-data")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_production_entry_acquires_guard_before_importing_main():
    source_path = Path(__file__).resolve().parents[1] / "app" / "entry.py"
    source = source_path.read_text(encoding="utf-8")
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

    # Keep lifespan assertions portable across POSIX flock and Windows
    # process-scoped byte-range locking. Actual contention is tested with a
    # separate spawned process in test_os_lock_excludes_another_process...
    with TestClient(app):
        assert app.state.single_process_runtime_guard._fd is not None
    assert app.state.single_process_runtime_guard._fd is None

    with TestClient(second):
        assert second.state.single_process_runtime_guard._fd is not None
    assert second.state.single_process_runtime_guard._fd is None
