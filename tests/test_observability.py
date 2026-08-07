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


def test_telemetry_store_persists_recent_requests(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = TelemetryStore(tmp_path, max_items=10)
        await store.load()
        await store.append({"request_id": "req_1", "status": "completed", "usage": {"prompt_tokens": 3, "completion_tokens": 2}, "timings": {"total_ms": 100, "first_token_ms": 50}})
        reloaded = TelemetryStore(tmp_path, max_items=10)
        await reloaded.load()
        assert reloaded.recent(1)[0]["request_id"] == "req_1"
        summary = reloaded.summary()
        assert summary["completed_requests"] == 1
        assert summary["estimated_total_tokens"] == 5
    asyncio.run(scenario())


def test_admin_panel_contains_runtime_sections() -> None:
    for text in ("chat2api 管理面板", "在线扩展", "最近请求", "估算 Token", "/api/admin/overview"):
        assert text in ADMIN_HTML


def test_server_exposes_diagnostics_usage_and_admin_routes() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert '@app.get("/admin")' in source
    assert '@app.get("/api/admin/overview"' in source
    assert '"chat.diagnostics"' in source
    assert '"usage": usage' in source
    assert "token_usage" in source
