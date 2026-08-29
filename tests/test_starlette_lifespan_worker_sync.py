from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_entry_imports_with_current_fastapi_starlette() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from app.entry import app; print(app.version)"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "0.22.35" in result.stdout


def test_worker_sync_uses_lifespan_compatibility_adapter() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    adapter = (ROOT / "app" / "server_worker_sync_lifespan_patch.py").read_text(encoding="utf-8")
    assert "from .server_worker_sync_lifespan_patch import install_server_worker_sync_patch" in entry
    assert "previous_lifespan = app.router.lifespan_context" in adapter
    assert "@asynccontextmanager" in adapter
    assert "captured[\"startup\"]" in adapter
    assert "captured[\"shutdown\"]" in adapter
