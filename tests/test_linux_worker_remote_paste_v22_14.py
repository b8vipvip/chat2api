from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_remote_login_ui_accepts_native_local_clipboard_paste():
    ui = (ROOT / "app" / "admin_linux_workers.js").read_text(encoding="utf-8")

    assert 'id="linuxLoginKeyboardSink"' in ui
    assert 'keyboardSink.focus({preventScroll:true})' in ui
    assert 'keyboardSink.addEventListener("paste"' in ui
    assert 'event.clipboardData?.getData("text/plain")' in ui
    assert 'event.preventDefault(); keyboardSink.value = ""; queueRemoteText(text);' in ui
    assert 'const isPaste=(event.ctrlKey||event.metaKey)' in ui
    assert 'for (const character of characters) queueRemoteInput({kind:"key",key:character,modifiers:[]});' in ui
    assert '远程粘贴只支持单行文本' in ui
    assert '最多 512 个字符' in ui
    assert 'navigator.clipboard.readText' not in ui


def test_remote_paste_release_bumps_server_runtime():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    match = re.search(r'SERVER_RUNTIME_VERSION = "(\d+)\.(\d+)\.(\d+)"', runtime)
    assert match
    assert tuple(map(int, match.groups())) >= (0, 22, 14)
