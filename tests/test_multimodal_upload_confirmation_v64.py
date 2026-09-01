from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "chrome_extension" / "content_multimodal_v4.js"


def text() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_multimodal_confirmation_uses_current_chatgpt_attachment_surfaces():
    source = text()
    assert 'const CONTROLLER = "multimodal-v4-r64"' in source
    assert "attachmentRoots()" in source
    assert "[data-testid*='file-preview']" in source
    assert "[data-testid*='upload-preview']" in source
    assert "[data-testid*='thumbnail']" in source
    assert "[aria-label*='Remove attachment']" in source
    assert "[aria-label*='移除附件']" in source
    assert "img[src^='blob:']" in source
    assert "video[src^='blob:']" in source


def test_multimodal_confirmation_has_safe_consumed_input_fallback():
    source = text()
    assert "function mutationTracker(file)" in source
    assert "function inputConsumed(input)" in source
    assert "mutationEvidence" in source
    assert "consumed && mutationEvidence && !uploadBusy()" in source
    assert "Date.now() - consumedSince >= 1200" in source
    assert 'verify_reason: "input-consumed-stable"' in source
    assert "const lateError = uploadErrorFor(file.name)" in source
    assert "attachment_verification_revision: 64" in source


def test_multimodal_confirmation_keeps_strong_errors_and_diagnostics():
    source = text()
    assert "uploadErrorFor(file.name)" in source
    assert "duplicateDialog()" in source
    assert "duplicate_dialog_auto_closed" in source
    assert "input_consumed=${inputConsumed(input)}" in source
    assert "mutations=${mutations.tracker.strong}/${mutations.tracker.named}/${mutations.tracker.media}" in source
    assert "A maximum of 4 attachments is supported per request" in source
    assert 'message.type === "chat2api.attach.prepare.v4"' in source


def test_multimodal_v64_javascript_syntax():
    result = subprocess.run(["node", "--check", str(SOURCE)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
