from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.api_keys import ApiPrincipal
from app.responses_emulated_tools_v109_patch import _bridge_correction_prompt
from app.responses_model_routing_v108_patch import _record_telemetry
from app.responses_protocol_integrity_v110_patch import _required_exact_literals


OLD = "FDEX_CODEX_SMOKE_19f6ab999f4b454d_WIRE"
CURRENT = "FDEX_CODEX_SMOKE_19f6ab999f4b454d_TOOLS"


def _codex_history() -> str:
    return (
        "USER MESSAGE:\n"
        f"Reason briefly, then reply with this exact marker: {OLD}\n\n"
        "ASSISTANT MESSAGE:\n" + OLD + "\n\n"
        "USER MESSAGE:\n"
        "Use a shell command to create a file with exactly this text followed "
        f"by newline: {CURRENT}. Then read it and finally reply with {CURRENT}.\n\n"
        "DEVELOPER MESSAGE:\n<rollout_budget>80000</rollout_budget>\n"
    )


def test_codex_latest_stage_replaces_historic_exact_wire_marker() -> None:
    body = {"input": [{"type": "message", "role": "user", "content": _codex_history()}]}
    assert _required_exact_literals(body) == [CURRENT]
    correction = _bridge_correction_prompt(body)
    assert CURRENT in correction
    assert OLD not in correction


def test_non_user_messages_do_not_contaminate_exact_constraints() -> None:
    body = {"input": [
        {"type": "message", "role": "user", "content": f"Reply with exact marker {CURRENT}"},
        {"type": "message", "role": "assistant", "content": f"Reply with exact marker {OLD}"},
    ]}
    assert _required_exact_literals(body) == [CURRENT]


def test_responses_root_uses_browser_timing_and_selected_worker_identity() -> None:
    class Telemetry:
        def __init__(self):
            self.data = {
                "resp_req_sample": {
                    "request_id": "resp_req_sample",
                    "client_id": "ext_real",
                    "worker_client_id": "ext_real",
                    "timings": {"first_token_ms": 8400.5, "browser_started_ms": 4200.0},
                }
            }

        def get(self, key):
            return self.data.get(key)

        async def upsert(self, row):
            self.data[row["request_id"]] = {**self.data.get(row["request_id"], {}), **row}
            return row

    telemetry = Telemetry()
    server = SimpleNamespace(state=SimpleNamespace(telemetry=telemetry))
    principal = ApiPrincipal(key_id="k", name="fdex-plus", kind="managed", scopes=("chat",))
    asyncio.run(_record_telemetry(
        server, request_id="resp_req_sample", principal=principal,
        payload={"model": "gpt-5.6-sol", "stream": True},
        prompt="private request", response={"id": "resp_ok", "status": "completed", "output_text": "done"},
        http_status=200, started_mono=0.0,
    ))
    row = telemetry.data["resp_req_sample"]
    assert row["request_type"] == "responses"
    assert row["client_id"] == "ext_real"
    assert row["timings"]["first_token_ms"] == 8400.5
    assert row["timings"]["browser_started_ms"] == 4200.0
    assert row["timings"]["total_ms"] > 0
