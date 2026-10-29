from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_v249_source_file_handoff_parity_and_counters():
    result = subprocess.run(
        ["node", "tests/file_card_source_format_v249.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
