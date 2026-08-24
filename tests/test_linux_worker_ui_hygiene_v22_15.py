import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _runtime_version(source: str) -> tuple[int, int, int]:
    match = re.search(r'SERVER_RUNTIME_VERSION = "(\d+)\.(\d+)\.(\d+)"', source)
    assert match
    return tuple(map(int, match.groups()))


def test_manifest_loads_ui_hygiene_and_site_settings_permission():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.8.2"
    assert "contentSettings" in manifest["permissions"]
    scripts = manifest["content_scripts"][1]["js"]
    assert "content_ui_hygiene_v31.js" in scripts
    assert scripts.index("content_ui_hygiene_v31.js") < scripts.index("content_page_driver_v22.js")


def test_background_preconfigures_microphone_and_notifications_without_clipboard_permission():
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    settings = (ROOT / "chrome_extension" / "background_site_permissions_v31.js").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))

    assert '"background_site_permissions_v31.js"' in entry
    assert 'chrome.runtime.getPlatformInfo()' in settings
    assert 'platform?.os !== "linux"' in settings
    assert '["microphone", "allow"]' in settings
    assert '["notifications", "block"]' in settings
    assert "https://chatgpt.com/*" in settings
    assert "https://www.chatgpt.com/*" in settings
    assert "https://chat.openai.com/*" in settings
    assert "clipboardRead" not in manifest["permissions"]
    assert "clipboardWrite" not in manifest["permissions"]


def test_ui_hygiene_only_dismisses_known_nuisance_or_microphone_preflight_dialogs():
    source = (ROOT / "chrome_extension" / "content_ui_hygiene_v31.js").read_text(encoding="utf-8")
    for token in (
        "More relevant, personalized replies",
        "got it",
        "not now",
        "microphone",
        "captcha",
        "payment",
        "dangerousContext",
        "nuisanceContext",
        "microphoneContext",
        "MutationObserver",
    ):
        assert token.lower() in source.lower()
    assert "action.node.click()" in source
    assert "document.querySelectorAll(\"button\").forEach" not in source


def test_runtime_tracks_ui_hygiene_release():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert _runtime_version(runtime) >= (0, 22, 15)
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
