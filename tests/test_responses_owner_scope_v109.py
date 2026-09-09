import asyncio
from collections import OrderedDict
from types import SimpleNamespace

from app.api_keys import ApiPrincipal
from app.responses_model_routing_v108_patch import _owner_allows, _record_telemetry


def principal(key_id: str, *, kind: str = "managed") -> ApiPrincipal:
    return ApiPrincipal(key_id=key_id, name=key_id, kind=kind, scopes=("chat",))


def test_response_owner_scope_denies_other_managed_key_and_allows_master() -> None:
    server = SimpleNamespace(state=SimpleNamespace(responses_response_owners=OrderedDict({"resp_a": "key_a"})))
    assert _owner_allows(server, principal("key_a"), "resp_a") is True
    assert _owner_allows(server, principal("key_b"), "resp_a") is False
    assert _owner_allows(server, principal("master", kind="master"), "resp_a") is True
    assert _owner_allows(server, principal("key_b"), "missing") is True


def test_completed_response_records_owner_before_metering() -> None:
    class Telemetry:
        async def upsert(self, row):
            self.row = dict(row)
            return row

    server = SimpleNamespace(state=SimpleNamespace(telemetry=Telemetry()))
    asyncio.run(
        _record_telemetry(
            server,
            request_id="resp_req_1",
            principal=principal("key_a"),
            payload={"model": "gpt-5.6-sol", "stream": False},
            prompt="hello",
            response={
                "id": "resp_a",
                "status": "completed",
                "output_text": "ok",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
            http_status=200,
            started_mono=0.0,
        )
    )
    assert server.state.responses_response_owners["resp_a"] == "key_a"
    assert server.state.telemetry.row["response_id"] == "resp_a"
