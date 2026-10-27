"""Regression for request-v6 single terminal and API transport delivery record."""
from pathlib import Path
from app.worker_probe_store import _sanitize_facts

ROOT = Path(__file__).resolve().parents[1]


def test_worker_probe_dom_topology_is_structured_and_text_free():
    probe = (ROOT / "chrome_extension/content_probe_supervisor_v1.js").read_text()
    assert "scene_reply_topology: topology.slice(0,5600)" in probe
    assert "scene_new_actions_after_all" in probe
    assert "scene_new_actions_same_branch" in probe
    assert "scene_changed_block_branches" in probe
    facts = _sanitize_facts({
        "scene_reply_topology": "r1p400;i1h21;i1t999;f1b12",
        "scene_new_actions_after_all": 1,
        "scene_changed_block_branches": 2,
        "scene_new_actions_same_branch": 1,
    })
    assert facts["scene_reply_topology"] == "r1p400;i1h21;i1t999;f1b12"
    assert facts["scene_new_actions_same_branch"] == 1
    assert _sanitize_facts({"scene_reply_topology": "<user-token>"}) == {}


def test_sse_disconnect_must_write_terminal_even_without_worker_completed_timestamp():
    source = (ROOT / "app/main.py").read_text()
    # Original guard lost the SSE request at 12 seconds when ChatGPT only
    # finished later at 206 seconds.
    assert "if not recorded and state.completed_mono:" not in source
    assert 'state.diagnostics["api_stream_closed_before_delivery"] = True' in source
    assert '"cancelled" if not worker_terminal else "error"' in source
    assert "api_stream_closure_worker_terminal" in source


def test_terminal_receipt_archival_stages_preserve_single_broker_owner():
    source = (ROOT / "app/main.py").read_text()
    assert "terminal_transport_broker_accepted" in source
    assert 'active_state.diagnostics["server_terminal_receipt_stage"]' in source
    assert '"server_finalization_stage"] = "archive-started"' in source
    assert '"server_finalization_stage"] = "archive-finished"' in source
    assert '"server_finalization_stage"] = "archive-timeout"' in source
    assert "timeout=180.0" in source
    broker = (ROOT / "app/broker.py").read_text()
    assert 'elif event_type == "chat.completed":' in broker
    assert "state.terminal_event_type is not None" in broker
