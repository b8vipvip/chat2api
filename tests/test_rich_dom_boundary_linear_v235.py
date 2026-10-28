"""P0 rich DOM recovery complexity stays linear without weakening integrity."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_large_rendered_response_boundary_is_linear_and_complete():
    if not shutil.which("node"):
        return
    result = subprocess.run(
        ["node", "tests/rich_dom_boundary_linear_v235.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
