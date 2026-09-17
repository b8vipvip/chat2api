from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def test_manifest_installs_request_v6_as_epoch_terminal_owner():
    manifest=json.loads(read("chrome_extension/manifest.json")); scripts=manifest["content_scripts"][1]["js"]
    assert scripts.index("content_request_v5.js") < scripts.index("content_rich_response_v69.js") < scripts.index("content_request_v6.js")
    assert scripts.index("content_request_v6.js") < scripts.index("content_chatgpt_choice_policy_v143.js") < scripts.index("content_interruption_guard_v72.js")
    assert "content_response_stream_recovery_v49.js" not in scripts
    assert "content_response_stream_recovery_v69.js" not in scripts
    assert "content_terminal_integrity_v89.js" not in scripts

def test_request_v6_binds_reply_to_current_prompt_turn_not_assistant_count():
    source=read("chrome_extension/content_request_v6.js")
    for token in ('const KEY = "__CHAT2API_REQUEST_CONTENT_V6__"',"function currentUserTurn(active)","function promptMatchesTurn(turn, active)",'reason: "after-current-user-turn"','"historical-turn"',"turnFollows(currentUser, turnOf(node))","active.baselineNodes = new Set(nodes)","v5.active = active","v5.listener = listener",'request_controller: "request-v6"',"response_epoch_revision: 69",'response_format: "markdown"'):
        assert token in source
    assert "nodes.length > active.baselineCount" not in source

def test_choice_policy_defaults_ab_reply_to_reply1_and_stays_in_chat_mode():
    source=read("chrome_extension/content_chatgpt_choice_policy_v143.js")
    for token in ('const KEY = "__CHAT2API_CHATGPT_CHOICE_POLICY_V143__"',"function dualReplyPair(active)",'dual_reply_policy: "reply1"',"dual_reply_feedback_clicked: false",'work_mode_offer_action: "stay-in-chat"',"留在聊天模式","stay in chat mode"):
        assert token in source
    assert "改用工作模式" not in source

def test_rich_serializer_preserves_semantic_answer_content_and_excludes_ui_chrome():
    source=read("chrome_extension/content_rich_response_v69.js")
    for token in ('format: "markdown"','"button"','"[data-testid*=\'copy\']"',"clone.querySelectorAll(ACTION_SELECTOR)","tableMarkdown(table)","listItemText(li, depth, ordered, index)",'if (tag === "pre")','if (tag === "img")',"![${alt.replace","MAX_INLINE_IMAGES = 4","MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024","captureFinalMarkdown"):
        assert token in source

def test_manual_playground_waits_for_exact_final_reply_and_safely_renders_markdown():
    source=read("app/admin_playground_chat_v69.js"); patch=read("app/playground_chat_patch.py")
    assert 'PATCH_ID = "playground-chat-v3"' in patch
    assert 'with_name("admin_playground_chat_v69.js")' in patch
    assert "stream:false" in source.replace(" ","")
    assert "payload.choices?.[0]?.message?.content" in source
    assert "function renderMarkdown(markdown)" in source
    assert "innerHTML = assistantText" not in source

def test_v69_javascript_syntax():
    for path in ("chrome_extension/content_rich_response_v69.js","chrome_extension/content_request_v6.js","chrome_extension/content_chatgpt_choice_policy_v143.js","app/admin_playground_chat_v69.js"):
        result=subprocess.run(["node","--check",str(ROOT/path)],capture_output=True,text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"
