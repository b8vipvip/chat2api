from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_health_promotion_modal_vm_contract() -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "ui_hygiene_health_modal_v101.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_runtime_preflight_requires_ui_hygiene_v101() -> None:
    source = (ROOT / "chrome_extension" / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    assert '"content_ui_hygiene_v31.js"' in source
    assert 'result?.modules?.ui_hygiene_v101' in source
    assert 'ui_hygiene_revision: 101' in source
