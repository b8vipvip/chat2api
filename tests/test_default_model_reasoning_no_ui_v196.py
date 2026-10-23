"""Default means do not select or verify a ChatGPT model or reasoning intensity.

Public OpenAI-style endpoint parsing and Worker page behavior share this contract.
"""
from pathlib import Path
import subprocess

from app.models import PlaygroundRunRequest
from app.v13_patch import _normalize_model, _normalize_reasoning, _target_from_payload


ROOT = Path(__file__).resolve().parents[1]


def test_default_model_and_effort_are_supported_for_chat_and_responses():
    assert _normalize_model("default") == "default"
    assert _normalize_model("chatgpt-web") == "default"
    for value in ("default", "auto", "page-default", None, ""):
        assert _normalize_reasoning(value) == (None, None)
    target = _target_from_payload({"model": "default", "reasoning_effort": "default"})
    assert target == {"model": "default", "reasoning_level": None, "reasoning_effort": None}
    assert _target_from_payload({"model": "gpt-6", "reasoning": {"effort": "default"}})["reasoning_level"] is None
    assert _target_from_payload({"model": "default", "reasoning_effort": "high"})["reasoning_level"] == "high"
    assert _target_from_payload({"model": "gpt-6", "reasoning_effort": "low"})["reasoning_level"] == "instant"


def test_automated_playground_default_is_first_class():
    body = PlaygroundRunRequest(test_type="text", model="default", api_key_id="managed")
    assert body.reasoning_effort == "default"
    explicit = PlaygroundRunRequest(test_type="text", model="gpt-6", reasoning_effort="default", api_key_id="managed")
    assert explicit.model == "gpt-6"
    assert explicit.reasoning_effort == "default"


def test_all_model_pickers_offer_default_and_preserve_selection():
    checks = {
        "app/admin.py": ['<option value="default">默认（页面当前模型）</option>'],
        "app/admin_text_model_management_v146.js": ['id:"default"', 'previous) ? previous : "default"'],
        "app/admin_playground_chat_v69.js": ['<option value="default" selected>默认（页面当前模型）</option>'],
        "app/admin_playground_lifecycle.js": ['level === "default" ? null : level'],
        "app/admin_v13.js": ['<option value="default" selected>默认（不检查/不切换）</option>'],
    }
    for path, phrases in checks.items():
        source = (ROOT / path).read_text()
        for phrase in phrases:
            assert phrase in source, (path, phrase)
        if path.endswith(".js"):
            result = subprocess.run(["node", "--check", str(ROOT / path)], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr


def test_worker_default_scenario_executes_without_picker_probe():
    result = subprocess.run(["node", "tests/worker_default_no_ui_v196.mjs"],cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
