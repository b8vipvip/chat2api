from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_background_request_recovery_v40_syntax_and_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    subprocess.run(
        [node, "--check", str(ROOT / "chrome_extension" / "background_request_recovery_v40.js")],
        check=True,
        cwd=ROOT,
    )
    subprocess.run(
        [node, str(ROOT / "tests" / "request_recovery_v40.mjs")],
        check=True,
        cwd=ROOT,
    )
