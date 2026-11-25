"""Browser-side Web Chat GPT-Live audio and WebRTC camera-track contract."""
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the browser voice harness")
def test_web_chat_voice_client_audio_camera_and_cleanup_contract():
    result = subprocess.run(
        ["node", "tests/web_chat_voice_client_v1.mjs"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "web-chat-voice-client-v1-passed" in result.stdout
