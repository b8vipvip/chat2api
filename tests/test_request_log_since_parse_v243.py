from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_admin_paste_timestamp_parser_v243():
    result = subprocess.run(
        ["node", "tests/request_log_since_parse_v243.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
