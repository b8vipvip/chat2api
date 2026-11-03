from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_media_terminal_checks_current_reply_instead_of_unrelated_user_prompt():
    result = subprocess.run(["node", "tests/media_reply_scope_v261.mjs"],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_roleless_request_native_binding_uses_the_verified_assistant_div():
    result = subprocess.run(["node", "tests/request_scoped_native_reply_binding_v260.mjs"],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
