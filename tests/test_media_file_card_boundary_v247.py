from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_v247_file_card_and_rich_reply_scope():
    result = subprocess.run(
        ["node", "tests/media_file_card_boundary_v247.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=25,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
