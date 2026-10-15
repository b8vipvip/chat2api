from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_reasoning_driver_mismatch_is_terminal_not_success():
    if not shutil.which("node"):
        return
    run = subprocess.run(
        ["node", str(ROOT / "tests" / "reasoning_verification_authority_v165.mjs")],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stdout + "\\n" + run.stderr


def test_no_unverified_extra_high_to_high_conversion():
    source = (ROOT / "chrome_extension" / "model_routing_v2.js").read_text(encoding="utf-8")
    assert 'const legacyLevel = reasoning === "extra-high" ? "high" : reasoning;' not in source
    assert 'failure.code = "reasoning_level_unsupported";' in source
