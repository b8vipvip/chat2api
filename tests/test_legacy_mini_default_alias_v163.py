import asyncio
import json
from pathlib import Path

from app.legacy_mini_default_alias_v163_patch import (
    LEGACY_ID, LegacyMiniDefaultAliasMiddleware, current_default_model,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeRegistry:
    def __init__(self, models, minimum=None):
        self.models = models
        self.minimum = minimum

    def legacy_minimum_model(self):
        return self.minimum

    def model_catalog(self, online_only=True):
        assert online_only is True
        return self.models


def models():
    return [
        {"id": "gpt-5.5", "validated": False, "clients": ["ext_paid"],
         "source": "worker-picker-observed-unverified"},
        {"id": "gpt-5.6-sol", "validated": False, "clients": ["ext_paid"],
         "source": "worker-picker-observed-unverified"},
        {"id": "gpt-6", "validated": False, "clients": ["ext_paid"],
         "source": "worker-picker-observed-unverified"},
    ]


def test_legacy_minimum_uses_only_explicit_admin_configuration():
    assert current_default_model(FakeRegistry(models())) is None
    assert current_default_model(FakeRegistry(models(), "gpt-5.6-sol")) == "gpt-5.6-sol"
    assert current_default_model(FakeRegistry(models(), "gpt-6")) == "gpt-6"
    assert current_default_model(FakeRegistry([], "gpt-6")) is None
    assert current_default_model(FakeRegistry([{
        "id": "gpt-6", "clients": [], "source": "worker-validated"
    }], "gpt-6")) is None


def run_request(path, model, rows, stream=False, minimum=None):
    received, sent = [], []
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hello"}],
                       "stream": stream}).encode("utf8")
    scope = {"type": "http", "method": "POST", "path": path,
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode())]}

    async def app_inner(scope, recv, send):
        message = await recv()
        received.append((scope, message))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def reader():
        return {"type": "http.request", "body": body, "more_body": False}

    async def writer(message):
        sent.append(message)

    asyncio.run(LegacyMiniDefaultAliasMiddleware(app_inner, FakeRegistry(rows, minimum))(
        scope, reader, writer))
    return received, sent


def test_all_text_api_endpoints_map_legacy_id_and_update_content_length():
    for path in ("/v1/chat/completions", "/v1/completions", "/v1/responses"):
        received, sent = run_request(path, LEGACY_ID, models(), stream=True, minimum="gpt-5.6-sol")
        assert sent[0]["status"] == 200
        scope, message = received[0]
        obj = json.loads(message["body"])
        assert obj["model"] == "gpt-5.6-sol"
        assert obj["stream"] is True
        assert scope["chat2api_legacy_requested_model"] == LEGACY_ID
        assert scope["chat2api_effective_model"] == "gpt-5.6-sol"
        assert int(dict(scope["headers"])[b"content-length"]) == len(message["body"])


def test_other_models_are_never_silently_substituted():
    received, sent = run_request("/v1/chat/completions", "gpt-5.5", models())
    assert sent[0]["status"] == 200
    assert json.loads(received[0][1]["body"])["model"] == "gpt-5.5"


def test_no_real_default_fails_explicitly_instead_of_pretending_success():
    received, sent = run_request("/v1/chat/completions", LEGACY_ID, [])
    assert received == []
    assert sent[0]["status"] == 503
    assert json.loads(sent[1]["body"])["error"]["code"] == "configured_minimum_model_unavailable"


def test_background_terminal_retires_route_before_error_publication():
    router = (ROOT / "chrome_extension/model_routing_v2.js").read_text(encoding="utf-8")
    assert "failure.routingDiagnostics = diagnostics;" in router
    assert 'type: "chat.error"' not in router
    assert "route_retired_by_authority_v30" in (ROOT / "chrome_extension/conversation_dispatch.js").read_text(encoding="utf-8")


def test_stale_persisted_api_route_can_be_retired_on_fresh_server_admission():
    router = (ROOT / "chrome_extension/conversation_routing.js").read_text(encoding="utf-8")
    assert "state.activeRequests.has(String(item.inflight_request_id))" in router
    assert "restore-stale-router-lease-v147" in router
    assert "await retireRoute(candidate, item, item.inflight_request_id," in router


def test_worker_table_displays_live_extension_version_for_both_platforms():
    ui = (ROOT / "app/admin_unified_workers_v153.js").read_text(encoding="utf-8")
    assert "<th>系统</th><th>版本</th>" in ui
    assert "workerVersion(client,null)" in ui
    assert "workerVersion(ext,worker)" in ui
    assert "metadata.extension_version || metadata.worker_version" in ui
    assert 'colspan="10"' in ui
