import json
from pathlib import Path
import subprocess

from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, CHROME_BRIDGE_VERSION


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"
CONTENT = "content_login_v27.js"
BACKGROUND = "background_login_v27.js"
VM_CONTRACT = "tests/login_readiness_v27.mjs"
GUEST_VM_CONTRACT = "tests/content_login_guest_precedence_v27.mjs"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_current_bridge_loads_login_detector_for_new_and_existing_tabs():
    manifest = json.loads(read(EXT / "manifest.json"))
    assert CHROME_BRIDGE_VERSION == "0.22.114"
    assert manifest["version"] == CHROME_BRIDGE_BUNDLE_VERSION == "0.22.114"
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


def test_guest_composer_auth_precedence_vm_contract():
    result = subprocess.run(
        ["node", GUEST_VM_CONTRACT], cwd=ROOT, capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "content_login_guest_precedence_v27 VM contract passed" in result.stdout


def test_background_login_coordinator_loads_before_request_route_authority():
    entry = read(EXT / "background_entry.js")
    source = read(EXT / BACKGROUND)
    assert entry.index('"content_bootstrap.js"') < entry.index(f'"{BACKGROUND}"') < entry.index('"conversation_routing.js"')
    assert '"conversation_warm_pool_v2.js"' not in entry
    # The legacy source may still recognize a warm-pool hook for compatibility,
    # but the production entry has no speculative warm-window owner.
    assert 'NETWORK_GATE_KEY = "__CHAT2API_NETWORK_GATE_V26__"' in source
    assert 'PERSISTENT_POOL_KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_V132__"' in source
    assert "async function readyForPrewarm()" in source


def test_startup_probe_delegates_physical_window_lifecycle_to_persistent_pool():
    source = read(EXT / BACKGROUND)
    for token in (
        "trackedProbe()",
        "ensureProbeWindow({ focused: false, userVisible: false })",
        "pool.ensureLoginSurface({",
        "chatgptLoginProbeAdoptable",
        "manual-login-window-created",
        "startup-readiness-window-created",
        "persistent-pool-bootstrap-adopted",
        "retireAutomaticProbeIfReady",
        "chrome.windows.onFocusChanged.addListener",
        'message?.type === "popup.login.open"',
        'message?.type === "popup.login.refresh"',
    ):
        assert token in source
    assert "chrome.windows.create" not in source
    assert "chrome.windows.remove" not in source
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
        "已登录 · Composer 可用",
        "需要登录 · 请在可见 ChatGPT 窗口完成认证",
        'send({ type: "popup.login.open" })',
        'send({ type: "popup.login.refresh" })',
        "Composer 已确认可用",
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
        "No login bootstrap should open when the network gate rejects proactive prewarm",
        "Login readiness must delegate bootstrap creation to Persistent Window Pool v132",
        "Startup readiness window must remain unfocused",
        "Ready bootstrap becomes pool-owned standby #1 and must not be auto-retired",
        "Login readiness must not close a pool-owned bootstrap window after readiness is confirmed",
        "Manual login action must reuse the existing auth window",
        'console.log("login_readiness_v27 VM contract passed")',
    ):
        assert token in contract
