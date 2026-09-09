import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.api_keys import ApiPrincipal
from app.responses_model_routing_v108_patch import _record_telemetry, _response_payload


ROOT = Path(__file__).resolve().parents[1]


def test_responses_stream_terminal_payload_is_recovered_for_metering() -> None:
    raw = (
        'event: response.created\n'
        'data: {"type":"response.created","response":{"id":"resp_1","status":"in_progress"}}\n\n'
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed","output_text":"done","usage":{"input_tokens":11,"output_tokens":3,"total_tokens":14}}}\n\n'
    ).encode()
    response = _response_payload(raw, True)
    assert response is not None
    assert response["id"] == "resp_1"
    assert response["status"] == "completed"
    assert response["output_text"] == "done"


def test_responses_completed_record_uses_canonical_telemetry_shape() -> None:
    class Telemetry:
        def __init__(self) -> None:
            self.rows = []

        async def upsert(self, row):
            self.rows.append(dict(row))
            return row

    telemetry = Telemetry()
    server = SimpleNamespace(state=SimpleNamespace(telemetry=telemetry))
    principal = ApiPrincipal(key_id="key_user", name="user-key", kind="managed", scopes=("chat",))
    response = {
        "id": "resp_1",
        "status": "completed",
        "output_text": "done",
        "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
    }
    asyncio.run(
        _record_telemetry(
            server,
            request_id="resp_req_1",
            principal=principal,
            payload={"model": "gpt-5.6-sol", "stream": False, "tools": [{"type": "web_search"}]},
            prompt="hello",
            response=response,
            http_status=200,
            started_mono=0.0,
        )
    )
    row = telemetry.rows[-1]
    assert row["request_id"] == "resp_req_1"
    assert row["response_id"] == "resp_1"
    assert row["api_key_id"] == "key_user"
    assert row["request_type"] == "responses"
    assert row["requested_model"] == "gpt-5.6-sol"
    assert row["status"] == "completed"
    assert row["usage"]["prompt_tokens"] == 11
    assert row["usage"]["completion_tokens"] == 3
    assert row["usage"]["total_tokens"] == 14
    assert row["diagnostics"]["response_protocol"] == "responses-v108"
    assert row["diagnostics"]["tool_types"] == ["web_search"]


def test_responses_metering_cannot_bypass_user_commerce_charge_wrapper() -> None:
    routing = (ROOT / "app" / "responses_model_routing_v108_patch.py").read_text(encoding="utf-8")
    commerce = (ROOT / "app" / "user_console_v104_patch.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert '"x-chat2api-request-id"' in routing
    assert "server_app.state.telemetry.upsert" in routing
    assert '"request_type": "responses"' in routing
    assert "billing.record_charge(owner, row, pricing)" in commerce
    assert "telemetry.upsert = metered_upsert" in commerce
    assert entry.index("install_user_console_v104_patch(app)") < entry.index("install_responses_model_routing_v108_patch(app)")
