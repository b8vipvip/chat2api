from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


def test_explicit_sse_terminal_survives_stream_reader_failure() -> None:
    if not shutil.which("node"):
        return
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "tests/network_stream_early_terminal_v55.mjs"],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr



def test_responses_terminal_only_reconciles_complete_same_turn_dom() -> None:
    if not shutil.which("node"):
        return
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "tests/responses_terminal_reconcile_v163.mjs"],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_network_terminal_remains_owned_by_request_v6() -> None:
    ext = Path(__file__).resolve().parents[1] / "chrome_extension"
    network = (ext / "content_network_stream_recovery_v55.js").read_text(encoding="utf-8")
    request = (ext / "content_request_v6.js").read_text(encoding="utf-8")
    assert "network_stream_error:" in network
    assert "networkTerminalEvidenceSource" in request
    assert 'type: "chat.completed"' not in network
