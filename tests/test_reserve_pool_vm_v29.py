from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_reserve_pool_vm_contract():
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "reserve_pool_v29.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "reserve_pool_v29 VM contract passed" in result.stdout
