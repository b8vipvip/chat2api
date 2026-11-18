from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_production_worker_touch_chain_accepts_current_websocket_and_rejects_stale_socket():
    script = r"""
import asyncio
import json
import tempfile
from pathlib import Path

from app.entry import app

async def main():
    registry = app.state.registry
    with tempfile.TemporaryDirectory() as directory:
        registry.data_dir = Path(directory)
        registry.path = Path(directory) / "clients.json"
        client_id, _token = await registry.register("Regression Worker", "Chrome", "0.22.291", {})
        current_socket = object()
        await registry.attach(client_id, current_socket)
        accepted = await registry.touch(
            client_id, {"extension_version": "0.22.292"}, websocket=current_socket
        )
        stale = await registry.touch(
            client_id, {"extension_version": "0.0.0"}, websocket=object()
        )
        print(json.dumps({
            "accepted": accepted,
            "stale_rejected": stale is False,
            "version": registry.clients[client_id].version,
        }))

asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "accepted": True,
        "stale_rejected": True,
        "version": "0.22.292",
    }


def test_all_worker_touch_decorators_forward_the_websocket_keyword_and_result():
    cases = (
        ("app/linux_worker_bridge_binding.py", "touch_with_worker_sync", "base_touch"),
        ("app/worker_auto_login_v153_patch.py", "touch_with_relogin", "previous_touch"),
        ("app/worker_text_model_management_v146_patch.py", "touch_with_history", "base_touch"),
        ("app/worker_model_tasks_v147_patch.py", "touch_tasks", "base_touch"),
    )
    for relative, function, predecessor in cases:
        source = (ROOT / relative).read_text(encoding="utf-8")
        start = source.index(f"async def {function}")
        end = source.index("registry.touch =", start)
        block = source[start:end]
        assert "websocket: Any | None = None" in block
        assert f"await {predecessor}(client_id, metadata, websocket=websocket)" in block
        assert "if not accepted:" in block
        assert "return False" in block
        assert "return True" in block
