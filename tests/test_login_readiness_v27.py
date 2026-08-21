import json
from pathlib import Path

from app.runtime_contract import CHROME_BRIDGE_VERSION


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"
CONTENT = "content_login_v27.js"
BACKGROUND = "background_login_v27.js"
VM_CONTRACT = "tests/login_readiness_v27.mjs"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_current_bridge_loads_login_detector_for_new_and_existing_tabs():
    manifest = json.loads(read(EXT / "manifest.json"))
    assert manifest["version"] == CHROME_BRIDGE_VERSION
    scripts = manifest["content_scripts"][1]["js"]
    assert CONTENT in scripts
    assert scripts.index("content_page_adapter_v22.js") < scripts.index(CONTENT) < scripts.index("content_page_driver_v22.js")

    bootstrap = read(EXT / "content_bootstrap.js")
    assert f'"{CONTENT}"' in bootstrap
    assert bootstrap.index('"content_page_adapter_v22.js"') < bootstrap.index(f'"{CONTENT}"') < bootstrap.index('"content_page_driver_v22.js"')


def test_login_detector_is_strictly_passive_and_auth_evidence_beats_guest_composer():
    source = read(EXT / CONTENT)
    for state in ("checking", "ready", "login_required", "unknown"):
        assert f'"{state}"' in source
    assert 'strategy: "visible-composer"' in source
    assert 'strategy: authEvidence.kind === "path" ? "auth-path" : "visible-auth-control"' in source
    assert 'message?.type !== "chat2api.login.detect.v27"' in source
    detect = source.split("function detect()", 1)[1].split("globalThis[KEY]", 1)[0]
    assert detect.index("const authEvidence = authPathEvidence() || authUiEvidence()") < detect.index("const readyComposer = composer()")
    assert 'normalize(node.getAttribute("aria-label") || "")' in source
    assert 'normalize(node.innerText || node.textContent || "")' in source
    assert "AUTH_CONTROL_RE.test(text)" in source
    assert ".click()" not in source
    assert "MutationObserver" not in source
    assert "KeyboardEvent" not in source
    assert "dispatchEvent" not in source
    assert "setInterval" not in source


def test_background_login_coordinator_loads_before_warm_pool_and_gates_network_prewarm():
    entry = read(EXT / "background_entry.js")
    source = read(EXT / BACKGROUND)
    assert entry.index('"content_bootstrap.js"') < entry.index(f'"{BACKGROUND}"') < entry.index('"conversation_warm_pool_v2.js"')
    for token in (
        'NETWORK_GATE_KEY = "__CHAT2API_NETWORK_GATE_V26__"',
        'WARM_POOL_KEY = "__CHAT2API_CONVERSATION_WARM_POOL_V2__"',
        "async function readyForPrewarm()",
        "const networkAllowed = await baseAllowPrewarm()",
        "if (!networkAllowed) return false",
        "return readyForPrewarm()",
        "login_readiness_gate_v27 = true",
        "patchWarmPoolAffinityGate",
    ):
        assert token in source


def test_startup_probe_is_single_unfocused_and_manual_login_reuses_it():
    source = read(EXT / BACKGROUND)
    for token in (
        'PROBE_URL = "https://chatgpt.com/"',
        "trackedProbe()",
        "ensureProbeWindow({ focused: false, userVisible: false })",
        "focused: Boolean(focused)",
        "chatgptLoginProbeAdoptable",
        "manual-login-window-created",
        "startup-readiness-window-created",
        "retireAutomaticProbeIfReady",
        "chrome.windows.onFocusChanged.addListener",
        'message?.type === "popup.login.open"',
        'message?.type === "popup.login.refresh"',
    ):
        assert token in source
    assert "password" not in source.lower()
    assert "captcha" not in source.lower()


def test_popup_exposes_login_state_and_manual_login_action():
    html = read(EXT / "popup.html")
    popup = read(EXT / "popup.js")
    assert 'id="loginStatus"' in html
    assert 'id="openLogin"' in html
    assert 'id="refreshLogin"' in html
    assert "打开 ChatGPT 登录窗口" in html
    for token in (
        "ChatGPT：已登录，可用",
        "ChatGPT：需要登录",
        'send({ type: "popup.login.open" })',
        'send({ type: "popup.login.refresh" })',
        "Composer 已确认",
    ):
        assert token in popup


def test_login_readiness_vm_contract_and_syntax_are_required_by_ci():
    workflow = read(ROOT / ".github" / "workflows" / "ci.yml")
    contract = read(ROOT / VM_CONTRACT)
    assert f"node --check chrome_extension/{CONTENT}" in workflow
    assert f"node --check chrome_extension/{BACKGROUND}" in workflow
    assert "- name: Login readiness VM contract" in workflow
    assert f"run: node {VM_CONTRACT}" in workflow
    for token in (
        "No login probe should open when the network gate rejects proactive prewarm",
        "Startup readiness window must remain unfocused",
        "Automatic readiness probe window should be retired",
        "Manual login action must reuse the existing auth window",
        'console.log("login_readiness_v27 VM contract passed")',
    ):
        assert token in contract
