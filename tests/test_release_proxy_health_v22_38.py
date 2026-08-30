from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_proxy_health_overlay_is_not_blocked_by_legacy_stable_table_flag() -> None:
    source = read("app/admin_linux_worker_chinese_progress.js")
    stable = read("app/linux_worker_table_stability_patch.py")

    assert "__CHAT2API_LINUX_WORKER_PROXY_HEALTH_V55__" in source
    assert "__CHAT2API_LINUX_WORKER_CHINESE_PROGRESS_V22_18__" not in source
    assert "observedProxyCells" in source
    assert "observeProxyCell(proxyCell)" in source
    assert 'proxyCell.dataset.chat2apiProxyHealthOwner = "v55"' in source
    assert 'if (proxyCell.innerHTML !== html) proxyCell.innerHTML = html;' in source
    assert 'globalThis.__CHAT2API_LINUX_WORKER_CHINESE_PROGRESS_V22_18__=true;' in stable


def test_proxy_health_ui_exposes_requested_four_facets() -> None:
    source = read("app/admin_linux_worker_chinese_progress.js")
    for token in (
        'pill("已配置","good")',
        '"网络正常"',
        '"网络异常"',
        '"GPT正常"',
        '"GPT异常"',
        '`延迟 ${health.latencyMs} ms`',
        'command:"test_proxy"',
        "HEALTH_TTL_MS = 60000",
        "HEALTH_RETRY_MS = 20000",
        'parseProbe(result, "network_access")',
        '"chatgpt_home", "conversation_route", "sentinel_route"',
    ):
        assert token in source


def test_release_versions_are_explicit_and_consistent() -> None:
    runtime = read("app/runtime_contract.py")
    manifest = json.loads(read("chrome_extension/manifest.json"))
    marker = read("chrome_extension/content_bundle_marker_v48.js")
    preflight = read("chrome_extension/background_runtime_preflight_v48.js")
    contract = read("chrome_extension/content_runtime_contract_v48.js")
    package = read("app/__init__.py")
    project = read("pyproject.toml")

    assert 'SERVER_RUNTIME_VERSION = "0.22.38"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.12"' in runtime
    assert manifest["version"] == "0.8.12"
    assert 'bundle: "0.8.12"' in marker
    assert 'REQUIRED_BUNDLE = "0.8.12"' in preflight
    assert 'REQUIRED_BUNDLE = "0.8.12"' in contract
    assert '__version__ = "0.7.2"' in package
    assert 'version = "0.7.2"' in project
    assert '"linux_worker_proxy_health_facets": True' in runtime


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
