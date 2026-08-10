import asyncio
from pathlib import Path

from app.admin import ADMIN_HTML
from app.telemetry import TelemetryStore
from app.token_usage import estimate_tokens, usage_for


def test_estimated_token_usage_is_nonzero_and_labeled() -> None:
    usage = usage_for("你好 world", "测试完成")
    assert usage.prompt_tokens > 0
    assert usage.completion_tokens > 0
    assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens
    assert usage.estimated is True
    assert usage.estimator == "chat2api-heuristic-v1"
    assert estimate_tokens("") == 0


def test_telemetry_store_persists_filters_and_key_stats(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TelemetryStore(tmp_path, max_items=10)
        await store.load()
        await store.append(
            {
                "request_id": "req_1",
                "status": "completed",
                "request_type": "multimodal",
                "attachments_count": 1,
                "api_key_id": "key_a",
                "api_key_name": "App A",
                "requested_model": "default",
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                "timings": {"total_ms": 100, "first_token_ms": 50},
            }
        )
        reloaded = TelemetryStore(tmp_path, max_items=10)
        await reloaded.load()
        assert reloaded.recent(1)[0]["request_type"] == "multimodal"
        assert reloaded.get("req_1")["api_key_id"] == "key_a"
        assert reloaded.query(key_id="key_a", status="completed")["total"] == 1
        assert reloaded.key_stats()["key_a"]["estimated_tokens"] == 5
        summary = reloaded.summary()
        assert summary["completed_requests"] == 1
        assert summary["estimated_total_tokens"] == 5

    asyncio.run(scenario())


def test_admin_console_contains_runtime_and_control_sections() -> None:
    for text in (
        "chat2api Console",
        "在线扩展",
        "API Key",
        "请求记录",
        "开发文档",
        "测试场",
        "视觉理解",
        "文件理解",
        "图片生成",
        "语音生成",
        "语音对话",
        "全部测试",
        "/api/admin/overview",
        "/v1/chat/completions",
        "/v1/files",
        "/v1/images/generations",
    ):
        assert text in ADMIN_HTML


def test_server_exposes_multimodal_diagnostics_and_admin_routes() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert '@app.get("/admin")' in source
    assert '@app.get("/developers")' in source
    assert '@app.get("/api/admin/overview"' in source
    assert '@app.get("/api/admin/keys"' in source
    assert '@app.get("/api/admin/requests/{request_id}"' in source
    assert '@app.post("/v1/files")' in source
    assert '@app.post("/v1/images/generations")' in source
    assert '@app.post("/api/admin/tests"' in source
    assert '"chat.diagnostics"' in source
    assert '"image.diagnostics"' in source
    assert "token_usage" in source
