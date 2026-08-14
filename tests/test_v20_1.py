from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
EXT = ROOT / "chrome_extension"


def test_console_uses_current_public_model_ids_only() -> None:
    source = (APP / "admin_v20_1.js").read_text(encoding="utf-8")
    assert 'const TEXT_MODELS = ["gpt-5.6-sol", "gpt-5.5", "gpt-5.5-mini"]' in source
    for model_id in (
        "gpt-5.6-sol",
        "gpt-5.5",
        "gpt-5.5-mini",
        "gpt-image",
        "gpt-live",
        "gpt-live-mini",
    ):
        assert f'"{model_id}"' in source
    assert '"default"' not in source
    assert '"chatgpt-web"' not in source


def test_playground_supports_mini_without_reasoning_parameter() -> None:
    source = (APP / "admin_v20_1.js").read_text(encoding="utf-8")
    assert "renderTestModels" in source
    assert "resolvedModel !== MINI_MODEL && effort" in source
    assert 'model: "gpt-5.5-mini"' in source
    assert "推理强度（mini 自动）" in source


def test_developer_docs_describe_current_mini_routing() -> None:
    source = (APP / "admin_v20_1.js").read_text(encoding="utf-8")
    assert "当前公开模型" in source
    assert "优先随机选择在线且空闲的 Free 扩展" in source
    assert "GPT-5.5 + 极速" in source
    assert "Bridge 重试与恢复" in source


def test_existing_extension_retry_mechanisms_are_retained() -> None:
    background = (EXT / "background.js").read_text(encoding="utf-8")
    request = (EXT / "content_request_v2.js").read_text(encoding="utf-8")
    assert "scheduleReconnect" in background
    assert "Math.min(30000, 1000 * 2 ** Math.min(reconnectAttempt, 5))" in background
    assert "while (attempts < 3" in request
    assert 'diagnostic(active, "retry"' in request


def test_v20_1_patch_is_installed_last_and_updates_version() -> None:
    entry = (APP / "entry.py").read_text(encoding="utf-8")
    patch = (APP / "v20_1_patch.py").read_text(encoding="utf-8")
    assert 'PATCH_VERSION = "0.20.1"' in patch
    assert '"/assets/chat2api-v20-1.js"' in patch
    assert 'marker = \'<script src="/assets/chat2api-v20-1.js"></script>\'' in patch
    assert "install_v20_patch(app)\ninstall_v20_1_patch(app)" in entry
    assert 'payload["server_version"] = PATCH_VERSION' in patch
