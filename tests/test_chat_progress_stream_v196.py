"""Real progress is distinct from UI status, spinner, and terminal answer."""
from pathlib import Path
import subprocess
from types import SimpleNamespace

from app.chat_media_deadline_v171 import chat_timeout_window, worker_chat_timeout
from app.chat_progress_stream_v196 import status_from_diagnostics, progress_is_real

ROOT = Path(__file__).resolve().parents[1]


def test_worker_status_is_not_assistant_answer():
    diag = {
        "visible_reasoning_source": "visible-chatgpt-ui-v196",
        "visible_reasoning_status_kind": "working",
        "visible_reasoning_status": "正在分析",
    }
    assert status_from_diagnostics(diag) == {
        "text": "正在分析", "kind": "working", "source": "visible-chatgpt-ui-v196"
    }
    assert status_from_diagnostics({**diag, "visible_reasoning_status": "正在分析..."}) is not None
    assert status_from_diagnostics({**diag, "visible_reasoning_status": "生成回答，涉及私有内容..."}) is None
    assert status_from_diagnostics({**diag, "visible_reasoning_source": "DOM-full-page"}) is None
    assert status_from_diagnostics({**diag, "visible_reasoning_status": "x" * 1000}) is None
    assert status_from_diagnostics({**diag, "visible_reasoning_status": "原始提示词: test"}) is None
    real_summary = status_from_diagnostics({
        "visible_reasoning_source": "visible-chatgpt-ui-v196",
        "visible_reasoning_status_kind": "reasoning",
        "visible_reasoning_status": "正在梳理项目的模块及其依赖关系",
    })
    assert real_summary["kind"] == "reasoning"
    assert status_from_diagnostics({
        **diag, "visible_reasoning_source": "generation-control-v196", "visible_reasoning_status": "正在生成"
    }) is not None


def test_static_spinner_can_never_extend_deadline():
    spinner = SimpleNamespace(diagnostics={
        "generation_liveness": "page-state-heartbeat-v49",
        "generation_heartbeat_sequence": 50000,
        "generating_observed": True,
        "visible_reasoning_status": "正在分析",
        "network_stream_bytes": 120000,
    }, text="")
    assert not progress_is_real(spinner)
    assert chat_timeout_window(300, automatic_extension=True,
                               media_handoff_proven=False,
                               stream_progress_proven=progress_is_real(spinner)) == 300
    assert not progress_is_real(SimpleNamespace(diagnostics={
        "network_recovered_assistant_chars": "invalid",
    }, text=""))


def test_real_progress_extends_only_automatic_stream_up_to_900():
    active = SimpleNamespace(diagnostics={
        "network_recovered_assistant_chars": 270,
        "network_stream_chunks": 84,
    }, text="")
    assert progress_is_real(active)
    assert chat_timeout_window(300, automatic_extension=True, media_handoff_proven=False,
                               stream_progress_proven=progress_is_real(active)) == 900
    assert worker_chat_timeout(300, automatic_extension=True, stream=True) == 900
    assert worker_chat_timeout(300, automatic_extension=True, stream=False) == 600
    assert chat_timeout_window(120, automatic_extension=False, media_handoff_proven=False,
                               stream_progress_proven=True) == 120
    assert worker_chat_timeout(120, automatic_extension=False, stream=True) == 120


def test_stream_status_keeps_final_content_separate_and_is_nonterminal():
    main = (ROOT / "app/main.py").read_text()
    broker = (ROOT / "app/broker.py").read_text()
    assert 'chunk_payload(response_id, model, {"chat2api_status":' in main
    assert 'if observed["kind"] == "reasoning" and observed["text"] != last_reasoning_text:' in main
    assert '"reasoning": preview' in main
    assert "for delta in final_text_chunks(final_text):" in main
    assert "yield \": chat2api-keepalive" in main
    assert 'elif event_type == "chat.completed":' in main
    assert 'elif event_type == "chat.completed":' in broker
    assert 'event_type in {"chat.diagnostics", "image.diagnostics"}' in broker


def test_worker_native_status_reports_from_active_request():
    proc = subprocess.run(["node", str(ROOT / "tests/worker_generation_status_v196.mjs")],
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr + proc.stdout
