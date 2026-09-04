from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_network_health_v56_retires_proxy_cell_repaint_loop() -> None:
    source = read("app/admin_linux_worker_chinese_progress.js")
    stable = read("app/linux_worker_table_stability_patch.py")
    assert "__CHAT2API_LINUX_WORKER_NETWORK_HEALTH_V56__" in source
    assert '__CHAT2API_LINUX_WORKER_PROXY_HEALTH_V55__ = "retired-by-v56"' in source
    assert "observedProxyCells" not in source
    assert "observeProxyCell" not in source
    assert "proxyCell.innerHTML" not in source
    assert "progressCell.innerHTML" not in source
    assert 'new MutationObserver(() => syncDom()).observe(tbody, {childList:true,subtree:false})' in source
    assert 'globalThis.__CHAT2API_LINUX_WORKER_CHINESE_PROGRESS_V22_18__=true;' in stable


def test_network_column_owns_proxy_health_and_proxy_column_is_hidden() -> None:
    source = read("app/admin_linux_worker_chinese_progress.js")
    for token in ('#view-linux-workers th:nth-child(7)','#view-linux-workers td:nth-child(7){display:none!important}','#view-linux-workers td:nth-child(6)::before','#view-linux-workers td:nth-child(6)::after','cell.dataset.chat2apiNetworkMain = view.main','cell.dataset.chat2apiNetworkSub = view.sub','cell.dataset.chat2apiNetworkTone = view.tone','cell.dataset.chat2apiNetworkOwner = "v56"','"网络正常"','"网络异常"','"GPT正常"','"GPT异常"','command:"test_proxy"',"HEALTH_TTL_MS = 60000","HEALTH_RETRY_MS = 20000",'parseProbe(result, "network_access")','"chatgpt_home","conversation_route","sentinel_route"'):
        assert token in source


def test_network_health_script_parses() -> None:
    result = subprocess.run(["node", "--check", str(ROOT / "app" / "admin_linux_worker_chinese_progress.js")], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_release_versions_are_explicit_and_consistent() -> None:
    runtime = read("app/runtime_contract.py")
    manifest = json.loads(read("chrome_extension/manifest.json"))
    marker = read("chrome_extension/content_bundle_marker_v48.js")
    marker71 = read("chrome_extension/content_bundle_marker_v71.js")
    preflight = read("chrome_extension/background_runtime_preflight_v48.js")
    contract = read("chrome_extension/content_runtime_contract_v48.js")
    contract71 = read("chrome_extension/content_runtime_contract_v71.js")
    package = read("app/__init__.py")
    project = read("pyproject.toml")
    assert 'SERVER_RUNTIME_VERSION = "0.22.55"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.25"' in runtime
    assert manifest["version"] == "0.8.25"
    assert 'bundle: "0.8.25"' in marker
    assert 'bundle: "0.8.25"' in marker71
    assert 'REQUIRED_BUNDLE = "0.8.25"' in preflight
    assert 'REQUIRED_BUNDLE = "0.8.25"' in contract
    assert 'REQUIRED_BUNDLE = "0.8.25"' in contract71
    assert '__version__ = "0.7.1"' in package
    assert 'version = "0.7.1"' in project
    for token in ('"network_response_recovery": True','"network_response_parser_v62": True','"linux_worker_master_switch": True','"linux_worker_disable_authority": True','"worker_live_occupancy": True','"worker_device_name_column": True','"worker_pairing_rename": True','"worker_presentation_console_liveness_v65": True','"worker_presentation_console_liveness_v66": True','"worker_column_registry_v67": True','"multimodal_upload_confirmation_v64": True','"multimodal_upload_v68": True','"api_key_console_v68": True','"rich_response_v69": True','"request_response_epoch_v69": True','"worker_content_runtime_epoch_v71": True','"linux_worker_proxy_health_facets": True','"worker_key_capacity_fifo_queue": True','"active_rate_limit_terminal_error": True','"routed_dispatch_terminal_error": True','"admin_single_render_owner": True','"worker_disabled_window_guard_v86": True','"successful_route_preservation_v86": True','"runtime_preflight_fast_path_v86": True','"request_prompt_viewer_repair_v86": True','"window_affinity_v87": True','"healthy_spare_lease_refresh_v87": True','"stale_route_window_cleanup_v87": True','"runtime_preflight_budget_v87": True'):
        assert token in runtime


def test_release_workflow_creates_one_release_per_runtime_version() -> None:
    workflow = read(".github/workflows/release.yml")
    assert "branches:" in workflow and "- main" in workflow
    assert "workflow_dispatch:" in workflow
    assert "contents: write" in workflow
    assert 'tag=v{server}' in workflow
    assert 'gh release view "$TAG"' in workflow
    assert 'gh release create "$TAG"' in workflow
    assert '--target "$GITHUB_SHA"' in workflow
    assert '--generate-notes' in workflow
