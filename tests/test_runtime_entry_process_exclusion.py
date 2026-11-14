from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def _isolated_environment(data_dir: Path, ready_file: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["CHAT2API_DATA_DIR"] = str(data_dir)
    env["CHAT2API_API_KEY"] = "runtime-entry-isolation-test-key"
    env.pop("WEB_CONCURRENCY", None)
    env.pop("UVICORN_WORKERS", None)
    env.pop("GUNICORN_WORKERS", None)
    if ready_file is not None:
        env["CHAT2API_TEST_ENTRY_READY_FILE"] = str(ready_file)
    else:
        env.pop("CHAT2API_TEST_ENTRY_READY_FILE", None)
    return env


def test_production_entry_rejects_second_process_before_store_import(tmp_path: Path) -> None:
    """Exercise app.entry itself, not just two lock objects in a test process."""
    repository_root = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "shared-data"
    ready_file = tmp_path / "first-process-ready"

    first_script = """
import os
import time
from pathlib import Path
from app.entry import app
Path(os.environ["CHAT2API_TEST_ENTRY_READY_FILE"]).write_text("ready", encoding="ascii")
time.sleep(120)
"""
    stderr_path = tmp_path / "first-process.stderr"
    with stderr_path.open("w", encoding="utf-8") as stderr_handle:
        first = subprocess.Popen(
            [sys.executable, "-c", first_script],
            cwd=repository_root,
            env=_isolated_environment(data_dir, ready_file),
            stdout=subprocess.DEVNULL,
            stderr=stderr_handle,
            text=True,
        )
    try:
        deadline = time.monotonic() + 60
        while not ready_file.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)

        if not ready_file.exists():
            if first.poll() is None:
                first.terminate()
            try:
                first.wait(timeout=10)
            except subprocess.TimeoutExpired:
                first.kill()
                first.wait(timeout=10)
            raise AssertionError(
                "first production entry process did not acquire its runtime lock: "
                + stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            )

        second_script = """
import sys
from app.runtime_single_process_guard import RuntimeTopologyError
try:
    import app.entry
except RuntimeTopologyError as exc:
    if "already owns" not in str(exc):
        raise
    if "app.main" in sys.modules:
        raise SystemExit("app.main was imported before the runtime lock rejected this process")
    print("blocked-before-main")
else:
    raise SystemExit("second app.entry process unexpectedly acquired the shared data directory")
"""
        second = subprocess.run(
            [sys.executable, "-c", second_script],
            cwd=repository_root,
            env=_isolated_environment(data_dir),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert second.returncode == 0, second.stdout + second.stderr
        assert "blocked-before-main" in second.stdout
        assert first.poll() is None, "the first process must retain ownership after the rejected contender"

        lock_path = data_dir / ".chat2api-runtime.lock"
        assert lock_path.is_file()
        assert lock_path.stat().st_size == 32
    finally:
        if first.poll() is None:
            first.terminate()
        try:
            first.wait(timeout=10)
        except subprocess.TimeoutExpired:
            first.kill()
            first.wait(timeout=10)

    # OS process exit must release the lock without unlinking its inode.
    assert (data_dir / ".chat2api-runtime.lock").is_file()
    third_script = "from app.entry import app; print('reacquired-after-process-exit')"
    third = subprocess.run(
        [sys.executable, "-c", third_script],
        cwd=repository_root,
        env=_isolated_environment(data_dir),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert third.returncode == 0, third.stdout + third.stderr
    assert "reacquired-after-process-exit" in third.stdout
