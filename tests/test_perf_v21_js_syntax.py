from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_new_perf_javascript_is_syntax_valid() -> None:
    files = [
        "background_socket_singleflight_v21.js",
        "content_model_fast_v21.js",
        "model_prefetch_fast_v21.js",
        "content_request_perf_v21.js",
        "content_completion_fast_v21.js",
        "conversation_warm_pool_v2.js",
    ]
    for name in files:
        result = subprocess.run(
            ["node", "--check", str(EXTENSION / name)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{name}: {result.stderr or result.stdout}"
