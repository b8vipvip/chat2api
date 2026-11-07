"""Request-v6 native scope evidence must survive Broker terminal sanitization."""
from __future__ import annotations

import asyncio

from app.broker import RequestBroker, _terminal_provenance


def test_native_scope_gate_survives_real_broker_completed_path():
    async def scenario():
        broker = RequestBroker()
        state = await broker.create("req_v290_scope_gate", "ext_test_scope_gate")
        accepted = await broker.publish("req_v290_scope_gate", {
            "type": "chat.completed",
            "text": "Assistant answer",
            "diagnostics": {
                "native_scope_gate_reason": "ambiguous-reply-scopes",
                "native_scope_gate_facts": {
                    "reason": "ambiguous-reply-scopes",
                    "phase": "terminal",
                    "candidate_kind": "fresh-roleless-rich-answer-v246",
                    "fresh_action_count": 6,
                    "eligible_scope_count": 2,
                    "roleless_reply_verified": False,
                },
                "native_capture_failure": "native-reply-scope-unverified",
                "native_capture_attempted": False,
            },
        })
        assert accepted is True
        assert state.terminal_event_type == "chat.completed"
        assert state.diagnostics["native_scope_gate_reason"] == "ambiguous-reply-scopes"
        assert state.diagnostics["native_scope_gate_facts"] == {
            "reason": "ambiguous-reply-scopes",
            "phase": "terminal",
            "candidate_kind": "fresh-roleless-rich-answer-v246",
            "fresh_action_count": 6,
            "eligible_scope_count": 2,
            "roleless_reply_verified": False,
        }
        assert state.diagnostics["native_capture_failure"] == "native-reply-scope-unverified"
        assert state.diagnostics["native_capture_attempted"] is False
        assert await state.final_future == "Assistant answer"

    asyncio.run(scenario())


def test_native_scope_gate_rejects_untrusted_text_and_unbounded_fields():
    proven = _terminal_provenance({
        "diagnostics": {
            "native_scope_gate_reason": "<script>alert(1)</script>",
            "native_scope_gate_facts": {
                "reason": "bad value with prompt or signed URL",
                "phase": "upload-form",
                "candidate_kind": "prompt text with external URL",
                "fresh_action_count": 10_000,
                "eligible_scope_count": True,
                "roleless_reply_verified": "yes",
                "extra_dom_html": "<main>private prompt</main>",
                "raw_body": "private response body",
            },
        },
    })
    assert "native_scope_gate_reason" not in proven
    assert "native_scope_gate_facts" not in proven
    allowed = _terminal_provenance({
        "diagnostics": {
            "native_scope_gate_reason": "verified",
            "native_scope_gate_facts": {
                "reason": "verified",
                "phase": "progress",
                "candidate_kind": "other",
                "fresh_action_count": 32,
                "eligible_scope_count": 0,
                "roleless_reply_verified": True,
                "extra_dom_html": "<main>do not persist</main>",
            },
        },
    })
    assert allowed["native_scope_gate_reason"] == "verified"
    assert allowed["native_scope_gate_facts"] == {
        "reason": "verified",
        "phase": "progress",
        "candidate_kind": "other",
        "fresh_action_count": 32,
        "eligible_scope_count": 0,
        "roleless_reply_verified": True,
    }
