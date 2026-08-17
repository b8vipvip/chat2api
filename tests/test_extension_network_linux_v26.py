import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"
NETWORK = "background_network_v26.js"
PLATFORM = "background_platform_v26.js"
VM_CONTRACT = "tests/network_platform_v26.mjs"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_background_entry_loads_platform_and_network_before_warm_pool():
    entry = read(EXT / "background_entry.js")
    platform_pos = entry.index(f'"{PLATFORM}"')
    network_pos = entry.index(f'"{NETWORK}"')
    socket_pos = entry.index('"background_socket_singleflight_v21.js"')
    warm_pos = entry.index('"conversation_warm_pool_v2.js"')
    assert platform_pos < network_pos < socket_pos < warm_pos


def test_existing_browser_start_connection_behavior_is_preserved():
    background = read(EXT / "background.js")
    assert 'chrome.runtime.onStartup.addListener(() => connectSocket().catch(console.error));' in background
    assert background.rstrip().endswith("connectSocket().catch(console.error);")


def test_platform_detector_explicitly_supports_linux_without_native_helper():
    source = read(EXT / PLATFORM)
    assert "chrome.runtime.getPlatformInfo()" in source
    assert 'linux_supported: os === "linux"' in source
    assert '["win", "linux", "mac"].includes(os)' in source
    assert "platform_os" in source
    assert "platform_arch" in source
    assert "platform_linux_supported" in source


def test_network_probe_is_cached_singleflight_and_never_persists_public_ip():
    source = read(EXT / NETWORK)
    for token in (
        'const PROBE_URL = "https://ipwho.is/"',
        "const CACHE_MS = 30 * 60 * 1000",
        "const ERROR_CACHE_MS = 2 * 60 * 1000",
        "const PROBE_TIMEOUT_MS = 5000",
        "state.inFlight",
        "new AbortController()",
        "payload?.country_code",
        'countryCode !== "CN"',
        'status: external ? "external" : "china-mainland"',
        "networkExternalReady",
    ):
        assert token in source
    assert "networkPublicIp" not in source
    assert "networkIp" not in source


def test_proactive_warm_pool_is_gated_but_request_time_reconcile_remains_available():
    source = read(EXT / "conversation_warm_pool_v2.js")
    assert '__CHAT2API_NETWORK_GATE_V26__' in source
    schedule_pos = source.index("function scheduleWarm(")
    proactive_pos = source.index("if (!await proactivePrewarmAllowed()) return;", schedule_pos)
    candidate_pos = source.index("async function boundedWarmCandidate")
    request_reconcile_pos = source.index("const pending = reconcileWarmSlots().catch(() => null);", candidate_pos)
    assert schedule_pos < proactive_pos < candidate_pos < request_reconcile_pos
    assert 'changes.networkExternalReady?.newValue === true' in source


def test_bridge_version_and_popup_expose_linux_and_network_state():
    manifest = json.loads(read(EXT / "manifest.json"))
    assert manifest["version"] == "0.7.7"
    popup = read(EXT / "popup.js")
    for token in (
        'linux: "Linux"',
        'status === "external"',
        'status === "china-mainland"',
        "已允许主动预热",
        "禁止主动预热",
    ):
        assert token in popup


def test_network_platform_vm_contract_is_required_by_ci():
    contract = read(ROOT / VM_CONTRACT)
    workflow = read(ROOT / ".github" / "workflows" / "ci.yml")
    for token in (
        'from "node:vm"',
        'os: "linux"',
        'country_code: "US"',
        'country_code: "CN"',
        "synthetic lookup failure",
        "networkPublicIp",
        "navigator.onLine = false",
    ):
        assert token in contract
    assert f"node --check chrome_extension/{PLATFORM}" in workflow
    assert f"node --check chrome_extension/{NETWORK}" in workflow
    assert "- name: Network and platform VM contract" in workflow
    assert f"run: node {VM_CONTRACT}" in workflow


def test_linux_setup_document_requires_persistent_profile_and_manual_login():
    doc = read(ROOT / "docs" / "EXTENSION_NETWORK_LINUX.md")
    for token in (
        "dedicated persistent Chrome/Chromium profile",
        "CAPTCHA",
        "2FA",
        "should not store ChatGPT credentials",
        "login_required",
        "打开 ChatGPT 登录窗口",
        "one persistent browser profile per worker",
    ):
        assert token in doc
