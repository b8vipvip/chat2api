from pathlib import Path
import json
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_manifest_installs_epoch_owner_after_v5_and_supersedes_v49_recovery():
    manifest = json.loads(read("chrome_extension/manifest.json"))
    scripts = manifest["content_scripts"][1]["js"]
    assert scripts.index("content_request_v5.js") < scripts.index("content_rich_response_v69.js")
    assert scripts.index("content_rich_response_v69.js") < scripts.index("content_request_v6.js")
    assert scripts.index("content_response_stream_recovery_v49.js") < scripts.index("content_response_stream_recovery_v69.js")


def test_request_v6_binds_reply_to_current_prompt_turn_not_assistant_count():
    source = read("chrome_extension/content_request_v6.js")
    assert 'const KEY = "__CHAT2API_REQUEST_CONTENT_V6__"' in source
    assert "function currentUserTurn(active)" in source
    assert "function promptMatchesTurn(turn, active)" in source
    assert 'reason: "after-current-user-turn"' in source
    assert '"historical-turn"' in source
    assert "turnFollows(currentUser, turnOf(node))" in source
    assert "active.baselineNodes = new Set(nodes)" in source
    assert "v5.active = active" in source
    assert "v5.listener = listener" in source
    assert 'request_controller: "request-v6"' in source
    assert "response_epoch_revision: 69" in source
    assert 'response_format: "markdown"' in source
    # v5's production bug treated any increase in assistant-role node count as a
    # new answer. v41 can create historical role proxies after send, so v69 must
    # never use node-count advancement as response ownership evidence.
    assert "nodes.length > active.baselineCount" not in source


def test_epoch_recovery_disables_legacy_count_based_timer_and_reuses_v6_contract():
    source = read("chrome_extension/content_response_stream_recovery_v69.js")
    assert "clearInterval(legacy.timer)" in source
    assert 'legacy.superseded_by = "response-stream-v69-epoch-safe"' in source
    assert 'const REQUEST_KEY = "__CHAT2API_REQUEST_CONTENT_V6__"' in source
    assert "contract.currentAssistantState(activeRef)" in source
    assert 'response_stream_recovery: "epoch-safe-v69"' in source
    assert "response_epoch_revision: 69" in source


def test_rich_serializer_preserves_semantic_answer_content_and_excludes_ui_chrome():
    source = read("chrome_extension/content_rich_response_v69.js")
    assert 'format: "markdown"' in source
    assert '"button"' in source
    assert '"[data-testid*=\'copy\']"' in source
    assert "clone.querySelectorAll(ACTION_SELECTOR)" in source
    assert "tableMarkdown(table)" in source
    assert "listItemText(li, depth, ordered, index)" in source
    assert 'if (tag === "pre")' in source
    assert 'if (tag === "img")' in source
    assert "![${alt.replace" in source
    assert "MAX_INLINE_IMAGES = 4" in source
    assert "MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024" in source
    assert "captureFinalMarkdown" in source


def test_manual_playground_waits_for_exact_final_reply_and_safely_renders_markdown():
    source = read("app/admin_playground_chat_v69.js")
    patch = read("app/playground_chat_patch.py")
    assert 'PATCH_ID = "playground-chat-v69"' in patch
    assert 'with_name("admin_playground_chat_v69.js")' in patch
    assert "stream:false" in source.replace(" ", "")
    assert "payload.choices?.[0]?.message?.content" in source
    assert "function renderMarkdown(markdown)" in source
    assert "pgChatCode" in source
    assert "pgChatTableWrap" in source
    assert "pgChatImage" in source
    assert '/^(https?:|mailto:|data:image\\/)/i' in source
    assert "ChatGPT 网页自身的复制/点赞等操作图标不会当作模型回答传输" in source
    # User/error strings remain escaped; model Markdown is parsed by the bounded
    # renderer rather than injected as arbitrary ChatGPT HTML.
    assert "escHtml(item.content)" in source
    assert "innerHTML = assistantText" not in source


def test_v69_javascript_syntax():
    for path in (
        "chrome_extension/content_rich_response_v69.js",
        "chrome_extension/content_request_v6.js",
        "chrome_extension/content_response_stream_recovery_v69.js",
        "app/admin_playground_chat_v69.js",
    ):
        result = subprocess.run(["node", "--check", str(ROOT / path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"
