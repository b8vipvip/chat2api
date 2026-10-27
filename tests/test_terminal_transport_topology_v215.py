from pathlib import Path

from app.broker import RequestBroker
from app.worker_probe_store import _sanitize_facts

ROOT = Path(__file__).resolve().parents[1]


def test_dom_topology_is_data_minimized():
    facts = _sanitize_facts({
        "scene_reply_topology": "8:h2:1:22:U;98:p:1:185:C",
        "scene_changed_block_branches": 4,
        "scene_new_actions_after_all": 0,
        "scene_new_actions_same_branch": 1,
        "raw_dom_html": "<html>secret</html>",
    })
    assert facts["scene_reply_topology"].endswith(":C")
    assert facts["scene_changed_block_branches"] == 4
    assert "raw_dom_html" not in facts
    assert "scene_reply_topology" not in _sanitize_facts({
        "scene_reply_topology": "8:div:<script>secret</script>"
    })
    assert "scene_reply_topology" not in _sanitize_facts({
        "scene_reply_topology": "a" * 1400
    })


async def test_broker_records_original_worker_terminal_ack_provenance():
    broker = RequestBroker()
    state = await broker.create("req_transport_proof", "ext_transport")
    accepted = await broker.publish("req_transport_proof", {
        "type": "chat.completed", "request_id": "req_transport_proof",
        "text": "verified assistant",
        "diagnostics": {
            "response_terminal_source": "verified-rendered-dom",
            "terminal_worker_send_attempt": 2,
            "terminal_worker_ack_type": "background-websocket-enqueue-v215",
        },
    })
    assert accepted
    assert await state.final_future == "verified assistant"
    assert state.diagnostics["terminal_worker_send_attempt"] == 2
    assert state.diagnostics["terminal_worker_ack_type"] == "background-websocket-enqueue-v215"
    assert not await broker.publish("req_transport_proof", {
        "type": "chat.error", "request_id": "req_transport_proof", "error": "late"
    })
    assert state.terminal_event_type == "chat.completed"


def test_server_websocket_records_receipt_and_broker_acceptance_only():
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert 'terminal_transport_server_seen' in source
    assert 'terminal_transport_broker_accepted' in source
    assert 'accepted = await broker.publish(request_id, message)' in source
    assert 'terminal_transport_payload_chars' in source


def test_worker_probe_topology_does_not_capture_private_raw_page_text():
    source = (ROOT / "chrome_extension/content_probe_supervisor_v1.js").read_text(encoding="utf-8")
    assert 'scene_reply_topology: topology.slice(0,1350)' in source
    assert 'pageBranch' in source
    assert 'scene_new_actions_after_all' in source
