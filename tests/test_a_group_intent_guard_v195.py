"""Regression tests for TX03 A group 2026-10-25 (v0.22.195)."""
from pathlib import Path
import subprocess

from app.chat_media_deadline_v171 import chat_timeout_window, long_form_text_requested

ROOT = Path(__file__).resolve().parents[1]

def test_handoff_is_long_form_text_without_a_file_handoff():
    prompt = ("External account-connected apps, plugins, connectors, actions, integrations, "
              "and their account data are disabled.\n[User]\n"
              "检查这个仓库 https://github.com/b8vipvip/sumeme.git "
              "请根据仓库现状生成一份完整的项目交接上下文，包含当前目标、"
              "已经完成的修改、代码架构、关键文件、已确认的问题和下一步计划")
    assert long_form_text_requested(prompt)
    assert chat_timeout_window(300, automatic_extension=True, media_handoff_proven=False,
                               long_text_requested=long_form_text_requested(prompt)) == 600
    assert chat_timeout_window(300, automatic_extension=False, media_handoff_proven=False,
                               long_text_requested=long_form_text_requested(prompt)) == 300

def test_js_request_intent_and_tool_modal_guard():
    proc = subprocess.run(
        ["node", str(ROOT / "tests/a_group_intent_guard_v195.mjs")],
        cwd=ROOT, text=True, capture_output=True, timeout=20, check=False,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
