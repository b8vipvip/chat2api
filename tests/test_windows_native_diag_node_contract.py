"""Run the Chrome extension's real Node/VM contracts in the full Python CI gate.

These standalone .mjs files are not automatically discovered by pytest, and
were previously omitted from the CI workflow. Keep the Windows visual tab,
DOM boundary and diagnostic lease behaviors covered by mandatory CI.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", [
    "native_page_windows_extension.mjs",
    "native_page_windows_capture_dom.mjs",
    "native_page_diagnostic_window_lease.mjs",
])
def test_windows_native_diagnostic_node_contract(script: str) -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests" / script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, (
        f"{script} exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
