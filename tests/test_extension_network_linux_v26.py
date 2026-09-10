import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"
NETWORK = "background_network_v26.js"
PLATFORM = "background_platform_v26.js"
VM_CONTRACT = "tests/network_platform_v26.mjs"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_background_entry_loads_platform_and_network_before_route_authority():
    entry = read(EXT / "background_entry.js")
    platform_pos = entry.index(f'"{PLATFORM}"')
    network_pos = entry.index(f'"{NETWORK}"')
    socket_pos = entry.index('"background_socket_singleflight_v21.js"')
    router_pos = entry.index('"conversation_routing.js"')
    assert platform_pos < network_pos < socket_pos < router_pos
    assert '"conversation_warm_pool_v2.js"' not in entry
    assert '"background_reserve_pool_v29.js"' not in entry


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


def test_legacy_warm_pool_source_still_documents_network_gate_but_is_not_production_owner():
    source = read(EXT / "conversation_warm_pool_v2.js")
    entry = read(EXT / "background_entry.js")
    assert '__CHAT2API_NETWORK_GATE_V26__' in source
    assert '"conversation_warm_pool_v2.js"' not in entry


def test_popup_exposes_linux_and_network_state():
    manifest = json.loads(read(EXT / "manifest.json"))
    assert manifest["manifest_version"] == 3
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
