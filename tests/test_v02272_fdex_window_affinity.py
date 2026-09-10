from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.request_window_observability_v117_patch import _window_fields
from app.responses_tool_stream_v116_patch import _recover_nested_arguments_custom_tool_envelope


ROOT = Path(__file__).resolve().parents[1]


def test_v116_recovers_real_fdex_nested_exec_shape_with_raw_javascript_quotes():
    raw = r'''<<<CHAT2API_RESPONSES_TOOL_V109>>>
{"kind":"tool_calls","calls":[{"namespace":"functions","name":"exec","arguments":{"name":"exec","input":"const match = ALL_TOOLS.find(x => x.name.includes("fdex_smoke_echo") && x.name.includes("fdex_smoke"));\nif (!match) { text("fdex_smoke_echo tool not found"); exit(); }\nconst fn = tools[match.name];\nconst result = await fn({marker:"FDEX_CODEX_SMOKE"});\ntext(result);"}}]}
<<<END_CHAT2API_RESPONSES_TOOL_V109>>>'''

    parsed = _recover_nested_arguments_custom_tool_envelope(raw)

    assert parsed is not None
    call = parsed["calls"][0]
    assert call["namespace"] == "functions"
    assert call["name"] == "exec"
    assert call["arguments"]["name"] == "exec"
    assert 'tools[match.name]' in call["arguments"]["input"]
    assert 'marker:"FDEX_CODEX_SMOKE"' in call["arguments"]["input"]


def test_v117_promotes_worker_slot_and_physical_window_identity():
    fields = _window_fields(
        {
            "extension_worker_index": 1,
            "routed_window_id": 4321,
            "routed_tab_id": 8765,
            "extension_worker_route_key": "key_demo",
        }
    )

    assert fields == {
        "window_number": 1,
        "window_id": 4321,
        "tab_id": 8765,
        "window_route_key": "key_demo",
    }


def test_worker_v27_is_loaded_after_parallel_allocator_and_before_dispatch():
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert entry.index('"conversation_workers_v25.js"') < entry.index('"conversation_workers_v27.js"')
    assert entry.index('"conversation_workers_v27.js"') < entry.index('"conversation_dispatch.js"')


def test_worker_v27_preserves_sequential_affinity_and_only_spills_after_busy_grace():
    source = (ROOT / "chrome_extension" / "conversation_workers_v27.js").read_text(encoding="utf-8")

    assert "HANDOFF_GRACE_MS = 450" in source
    assert "primaryBusy" in source
    assert "waitForPrimaryHandoff" in source
    assert "restoreAffinityIfNeeded" in source
    assert "expectedConversation" in source
    assert "expectedUrl" in source
    assert "saved-conversation-restored" in source
    assert 'extension_worker_router: "per-api-key-v27-sequential-handoff"' in source
    assert "extension_worker_index: selected.workerIndex" in source
    assert "extension_window_number: selected.workerIndex" in source
    assert "routed_tab_id" in source
    assert "routed_window_id" in source


def test_request_history_exposes_window_number_and_download_log_persists_identity():
    source = (ROOT / "app" / "request_window_observability_v117_patch.py").read_text(encoding="utf-8")

    assert "窗口编号" in source
    assert '"window_number"' in source
    assert '"window_id"' in source
    assert '"tab_id"' in source
    assert '"window_route_key"' in source
    assert "await telemetry.upsert" in source


def test_responses_compatibility_chain_installs_v116():
    source = (ROOT / "app" / "responses_tool_stream_v112_patch.py").read_text(encoding="utf-8")
    assert "responses_tool_stream_v116_patch" in source
    assert "install_responses_tool_stream_v116_patch(app)" in source


def test_worker_v27_javascript_syntax_when_node_is_available():
    node = shutil.which("node")
    if node is None:
        return
    subprocess.run(
        [node, "--check", str(ROOT / "chrome_extension" / "conversation_workers_v27.js")],
        check=True,
        capture_output=True,
        text=True,
    )
