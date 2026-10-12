from pathlib import Path
import subprocess
import pytest

from app.model_capability_routing_patch import _idle_compatible
from app.response_completion_integrity_v125 import enforce_nonstream_contract

ROOT = Path(__file__).resolve().parents[1]
PROMPT = "输出严格JSON数组，恰好10个字符串"


@pytest.mark.parametrize("name", ["native_editor_integrity_v125.mjs", "network_stream_early_terminal_v55.mjs"])
def test_js_response_integrity(name):
    process = subprocess.run(["node", str(ROOT / "tests" / name)], cwd=ROOT, text=True, capture_output=True, timeout=20, check=False)
    assert process.returncode == 0, process.stdout + process.stderr


def test_json_nonstream_contract_rejects_partial_and_accepts_exact_ten_strings():
    with pytest.raises(RuntimeError, match="incomplete JSON"):
        enforce_nonstream_contract(PROMPT, '["first')
    with pytest.raises(RuntimeError, match="exactly 10 strings"):
        enforce_nonstream_contract(PROMPT, '["first", "second"]')
    enforce_nonstream_contract(PROMPT, '["a","b","c","d","e","f","g","h","i","j"]')
    enforce_nonstream_contract("常规客服回复", "not a JSON array")


def test_busy_compatible_worker_is_queued_instead_of_returning_503():
    class Registry:
        busy_clients = {"ext_busy"}
        clients = {}
        def online_client_ids(self):
            return ["ext_busy"]
        def chatgpt_routing_ready(self, client_id):
            return True
        def client_models(self, client_id):
            return [{"id": "gpt-5.5-mini"}]
    assert _idle_compatible(Registry(), "gpt-5.5-mini") == ["ext_busy"]


def test_no_compatible_worker_is_never_falsely_admitted():
    class Registry:
        busy_clients = {"ext_busy"}
        clients = {}
        def online_client_ids(self):
            return ["ext_busy"]
        def chatgpt_routing_ready(self, client_id):
            return False
    assert _idle_compatible(Registry(), "gpt-5.5-mini") == []


def test_sse_dom_completion_requires_no_observed_network_stream():
    source = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    assert "&& !active.networkStreamSeen" in source
    assert "network_stream_unverified_end: true" in source
