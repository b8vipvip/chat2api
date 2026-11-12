"""R-04: late/unowned Worker evidence cannot overwrite Broker-owned model truth."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from fastapi import FastAPI

from app.broker import RequestBroker
from app import model_observability_v144_patch as model_patch
from app import model_capability_routing_patch as routing


class _Telemetry:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.fail = False

    async def upsert(self, row: dict) -> None:
        if self.fail:
            raise RuntimeError("simulated telemetry outage")
        self.rows.append(dict(row))


def test_only_broker_accepted_events_can_change_model_evidence(monkeypatch) -> None:
    async def exercise() -> None:
        app = FastAPI()
        broker = RequestBroker()
        telemetry = _Telemetry()
        sent: list[dict] = []

        async def send(_client_id: str, message: dict) -> None:
            sent.append(message)

        app.state.registry = SimpleNamespace(send=send)
        app.state.broker = broker
        app.state.telemetry = telemetry
        # These installer side-effects do not decide model truth; keep this test
        # confined to the production v144 publisher and the real RequestBroker.
        monkeypatch.setattr(model_patch, "install_worker_model_library_v145_patch", lambda app: app)
        monkeypatch.setattr(model_patch, "_patch_admin_html", lambda: None)
        model_patch.install_model_observability_v144_patch(app)

        request_id = "req_r04_accepted"
        state = await broker.create(request_id, "ext_r04")
        token = routing._MODEL_CONTEXT.set({
            "requested_model": "gpt-6", "routed_model": "gpt-6",
        })
        try:
            await app.state.registry.send("ext_r04", {
                "type": "chat.request", "request_id": request_id,
            })
        finally:
            routing._MODEL_CONTEXT.reset(token)

        assert sent and sent[0]["type"] == "chat.request"

        def evidence_event(event_type: str, served_model: str) -> dict:
            return {
                "type": event_type,
                "request_id": request_id,
                "diagnostics": {"model_evidence_v144": {
                    "served_model": served_model,
                    "evidence_source": "network_response_metadata",
                }},
            }

        assert await broker.publish(request_id, evidence_event("chat.diagnostics", "gpt-6"))
        assert state.diagnostics["model_verified"] is True
        assert state.diagnostics["served_model"] == "gpt-6"
        assert await broker.publish(request_id, {
            "type": "chat.completed", "request_id": request_id, "text": "done",
        })
        accepted_diagnostics = deepcopy(state.diagnostics)
        written_before = len(telemetry.rows)

        # Both a duplicated terminal and a late evidence-only event are
        # rejected by the real Broker and must not write a stale projection.
        assert not await broker.publish(
            request_id, evidence_event("chat.error", "gpt-5.6-sol")
        )
        assert not await broker.publish(
            request_id, evidence_event("chat.diagnostics", "gpt-5.6-sol")
        )
        assert len(telemetry.rows) == written_before
        assert state.diagnostics == accepted_diagnostics
        assert state.terminal_event_type == "chat.completed"
        assert state.text == "done"

        # No request state exists for this ID. Do not create a history entry
        # merely because a Worker emitted seemingly valid model evidence.
        assert not await broker.publish(
            "req_r04_unknown", {
                **evidence_event("chat.diagnostics", "gpt-5.6-sol"),
                "request_id": "req_r04_unknown",
            }
        )
        assert len(telemetry.rows) == written_before

        # The same request's final truth is still the accepted concrete model.
        assert telemetry.rows[-1]["served_model"] == "gpt-6"
        assert telemetry.rows[-1]["model_verified"] is True
        await broker.release(request_id)

    asyncio.run(exercise())


def test_model_projection_keeps_source_unknown_without_served_evidence() -> None:
    trace = model_patch._trace(
        {"requested_model": "gpt-6", "routed_model": "gpt-6"},
        {"default_model": "gpt-6", "evidence_source": "network_response_metadata"},
    )
    assert trace["served_model"] is None
    assert trace["model_verified"] is False
    assert trace["model_verification_level"] == "profile-default"



def test_telemetry_projection_outage_does_not_reverse_accepted_broker_event(monkeypatch) -> None:
    async def exercise() -> None:
        app = FastAPI()
        broker = RequestBroker()
        telemetry = _Telemetry()
        async def send(_client_id: str, _message: dict) -> None:
            return None

        app.state.registry = SimpleNamespace(send=send)
        app.state.broker = broker
        app.state.telemetry = telemetry
        monkeypatch.setattr(model_patch, "install_worker_model_library_v145_patch", lambda app: app)
        monkeypatch.setattr(model_patch, "_patch_admin_html", lambda: None)
        model_patch.install_model_observability_v144_patch(app)

        request_id = "req_r04_telemetry_outage"
        state = await broker.create(request_id, "ext_r04")
        token = routing._MODEL_CONTEXT.set({
            "requested_model": "gpt-6", "routed_model": "gpt-6",
        })
        try:
            await app.state.registry.send("ext_r04", {
                "type": "chat.request", "request_id": request_id,
            })
        finally:
            routing._MODEL_CONTEXT.reset(token)

        telemetry.fail = True
        accepted = await broker.publish(request_id, {
            "type": "chat.diagnostics", "request_id": request_id,
            "diagnostics": {"model_evidence_v144": {
                "served_model": "gpt-6",
                "evidence_source": "network_response_metadata",
            }},
        })
        assert accepted is True
        assert state.diagnostics["served_model"] == "gpt-6"
        assert state.diagnostics["model_verified"] is True

        terminal = await broker.publish(request_id, {
            "type": "chat.completed", "request_id": request_id, "text": "done",
        })
        assert terminal is True
        assert state.terminal_event_type == "chat.completed"
        await broker.release(request_id)

    asyncio.run(exercise())



def test_model_routing_telemetry_outage_does_not_block_worker_dispatch(monkeypatch) -> None:
    async def exercise() -> None:
        app = FastAPI()
        broker = RequestBroker()
        telemetry = _Telemetry()
        telemetry.fail = True
        sent: list[dict] = []
        async def send(_client_id: str, message: dict) -> None:
            sent.append(message)

        app.state.registry = SimpleNamespace(send=send)
        app.state.broker = broker
        app.state.telemetry = telemetry
        monkeypatch.setattr(model_patch, "install_worker_model_library_v145_patch", lambda app: app)
        monkeypatch.setattr(model_patch, "_patch_admin_html", lambda: None)
        model_patch.install_model_observability_v144_patch(app)

        request_id = "req_r04_routing_telemetry_outage"
        await broker.create(request_id, "ext_r04")
        token = routing._MODEL_CONTEXT.set({
            "requested_model": "gpt-6", "routed_model": "gpt-6",
        })
        try:
            await app.state.registry.send("ext_r04", {
                "type": "chat.request", "request_id": request_id,
            })
        finally:
            routing._MODEL_CONTEXT.reset(token)

        assert len(sent) == 1
        assert sent[0]["type"] == "chat.request"
        assert sent[0]["routing"]["requested_model"] == "gpt-6"
        assert sent[0]["routing"]["routed_model"] == "gpt-6"
        # Broker remains the only terminal owner even when observability is down.
        assert await broker.publish(request_id, {
            "type": "chat.completed", "request_id": request_id, "text": "done",
        })
        await broker.release(request_id)

    asyncio.run(exercise())
