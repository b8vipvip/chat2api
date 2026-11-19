from pathlib import Path
import shutil
import subprocess

import pytest


def test_verified_request_owned_progress_capture_cadence_v346():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the Worker content-script regression")
    subprocess.run(
        [node, "tests/native_progress_scope_cadence_v346.mjs"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        timeout=20,
    )
