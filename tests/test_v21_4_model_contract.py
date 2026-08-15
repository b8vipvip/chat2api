from pathlib import Path

from app.models import ChatCompletionRequest, ChatMessage, TestRunCreate
from app.v21_4_model_contract_patch import (
    MINI_MODEL,
    _ensure_mini_capabilities,
    canonical_model_id,
)


ROOT = Path(__file__).resolve().parents[1]


def test_model_ids_replace_whitespace_with_hyphens():
    assert canonical_model_id("gpt-5.5 mini") == "gpt-5.5-mini"
    assert canonical_model_id(" GPT-5.6 Sol ") == "gpt-5.6-sol"
    assert canonical_model_id("gpt   live mini") == "gpt-live-mini"


def test_chat_completion_normalizes_model_id_before_routing_and_history():
    body = ChatCompletionRequest(
        model="GPT-5.5 Mini",
        messages=[ChatMessage(role="user", content="hello")],
    )
    assert body.model == MINI_MODEL


def test_test_run_model_ids_are_canonical_too():
    report = TestRunCreate(run_id="case-1", test_type="chat", status="passed", model="gpt-5.5 mini")
    assert report.model == MINI_MODEL


def test_mini_declares_vision_and_file_understanding():
    row = _ensure_mini_capabilities({"id": "gpt-5.5 mini", "capabilities": ["text"]})
    assert row["capabilities"] == ["text", "vision", "file-understanding"]


def test_extension_loads_model_contract_after_affinity():
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert '"model_affinity_v23.js"' in entry
    assert '"model_contract_v25.js"' in entry
    assert entry.index('"model_affinity_v23.js"') < entry.index('"model_contract_v25.js"')


def test_extension_mini_capabilities_match_server_contract():
    source = (ROOT / "chrome_extension" / "model_contract_v25.js").read_text(encoding="utf-8")
    assert 'const MINI_MODEL = "gpt-5.5-mini"' in source
    assert '["text", "vision", "file-understanding"]' in source
    assert 'replace(/\\s+/g, "-")' in source
