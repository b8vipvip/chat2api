from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_ec46_long_send_revalidation_contract():
    result = subprocess.run(
        ['node', '--test', 'tests/ec46_long_send_revalidation_v254.mjs'],
        cwd=ROOT, capture_output=True, text=True, timeout=25, check=False,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
