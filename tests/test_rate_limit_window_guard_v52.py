from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"


def test_multiple_chatgpt_tabs_are_adopted_instead_of_creating_n_plus_one_window() -> None:
    source = (EXT / "browser_tabs.js").read_text(encoding="utf-8")
    assert 'text.includes("Multiple ChatGPT tabs")' in source
    assert 'adopt-existing-on-multiple-tabs-v52' in source
    assert 'const existing = await chooseExistingTab()' in source
    assert 'if (text.includes("No ChatGPT tab")) return createAutomationWindow();' in source
    assert 'text.includes("Multiple ChatGPT tabs") || text.includes("No ChatGPT tab")' not in source


def test_visible_chatgpt_rate_limit_modal_arms_five_minute_circuit_breaker() -> None:
    source = (EXT / "content_rate_limit_guard_v52.js").read_text(encoding="utf-8")
    for token in ('COOLDOWN_MS = 5 * 60 * 1000','/请求过于频繁/i','/暂时限制.*访问对话记录/i','/请稍等几分钟后再重试/i','/too many requests/i','chatgptRateLimitGuardV52','chatgpt-rate-limit-detected','terminateActiveRequest','type: "chat.error"','ChatGPT is temporarily rate limited'):
        assert token in source


def test_background_circuit_breaker_blocks_request_resolution_and_all_chatgpt_window_creates() -> None:
    source = (EXT / "background_rate_limit_guard_v52.js").read_text(encoding="utf-8")
    for token in ('error.code = "chatgpt_rate_limited"','await assertReady("resolve-target-tab")','state.baseWindowsCreate','chatgptCreateData(createData)','await beforeWindowCreate("chrome.windows.create")','chrome.windows.create = guardedCreate'):
        assert token in source


def test_bundle_0826_load_order_and_runtime_contract_include_rate_limit_guard() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    entry = (EXT / "background_entry.js").read_text(encoding="utf-8")
    bootstrap = (EXT / "content_bootstrap.js").read_text(encoding="utf-8")
    preflight = (EXT / "background_runtime_preflight_v48.js").read_text(encoding="utf-8")
    content_contract = (EXT / "content_runtime_contract_v48.js").read_text(encoding="utf-8")
    marker = (EXT / "content_bundle_marker_v48.js").read_text(encoding="utf-8")
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert manifest["version"] == "0.8.28"
    content_scripts = manifest["content_scripts"][1]["js"]
    assert content_scripts.index("content_ui_hygiene_v31.js") < content_scripts.index("content_rate_limit_guard_v52.js") < content_scripts.index("content_tool_isolation_v48.js")
    assert entry.index('"browser_tabs.js"') < entry.index('"background_rate_limit_guard_v52.js"') < entry.index('"background_tab_supervisor_v32.js"')
    assert '"content_rate_limit_guard_v52.js"' in bootstrap
    assert 'REQUIRED_BUNDLE = "0.8.28"' in preflight
    assert '"content_rate_limit_guard_v52.js"' in preflight
    assert 'REQUIRED_BUNDLE = "0.8.28"' in content_contract
    assert '__CHAT2API_RATE_LIMIT_CONTENT_V52__' in content_contract
    assert 'bundle: "0.8.28"' in marker
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.28"' in runtime
    assert '"chatgpt_rate_limit_circuit_breaker": True' in runtime
    assert '"worker_window_reopen_loop_guard": True' in runtime
    assert '"active_rate_limit_terminal_error": True' in runtime
    assert '"network_response_parser_v62": True' in runtime


def test_rate_limit_guard_javascript_parses() -> None:
    for filename in ("content_rate_limit_guard_v52.js","background_rate_limit_guard_v52.js","browser_tabs.js","background_runtime_preflight_v48.js","content_runtime_contract_v48.js"):
        result = subprocess.run(["node", "--check", str(EXT / filename)], cwd=ROOT, capture_output=True, text=True, check=False)
        assert result.returncode == 0, f"{filename}\n{result.stderr}"
