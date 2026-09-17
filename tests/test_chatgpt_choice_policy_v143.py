from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
POLICY = "chrome_extension/content_chatgpt_choice_policy_v143.js"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_choice_policy_loads_immediately_after_request_v6_everywhere() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    scripts = manifest["content_scripts"][1]["js"]
    bootstrap = read("chrome_extension/content_bootstrap.js")
    assert scripts.index("content_request_v6.js") < scripts.index("content_chatgpt_choice_policy_v143.js") < scripts.index("content_interruption_guard_v72.js")
    assert bootstrap.index('"content_request_v6.js"') < bootstrap.index('"content_chatgpt_choice_policy_v143.js"') < bootstrap.index('"content_request_lifecycle_v50.js"')


def test_dual_reply_policy_detects_pair_and_defaults_to_reply1_without_feedback_click() -> None:
    source = read(POLICY)
    for token in (
        'const KEY = "__CHAT2API_CHATGPT_CHOICE_POLICY_V143__"',
        "function dualReplyPair(active)",
        "ancestorWithReplyLabel(first, 1)",
        "ancestorWithReplyLabel(second, 2)",
        "hideReply2FromWorker(pair, active)",
        'dual_reply_policy: "reply1"',
        "dual_reply_feedback_clicked: false",
        'target.style.visibility = "hidden"',
        "restoreHiddenReplies()",
    ):
        assert token in source
    assert "我更喜欢这个回复" not in source
    assert "I prefer this response" not in source


def test_work_mode_offer_always_stays_in_chat_for_active_api_request() -> None:
    source = read(POLICY)
    for token in (
        "function stayInChatButton()",
        "留在聊天模式",
        "stay in chat mode",
        "function dismissWorkModeOffer(active)",
        "button.click()",
        'work_mode_offer_action: "stay-in-chat"',
        "const active = activeRequest();",
        "if (!active)",
    ):
        assert token in source
    assert "改用工作模式" not in source
    assert "switch to work mode" not in source.lower()


def test_runtime_contract_requires_choice_policy_so_existing_tabs_hot_heal() -> None:
    contract = read("chrome_extension/content_runtime_contract_v71.js")
    bootstrap = read("chrome_extension/content_bootstrap.js")
    assert "__CHAT2API_CHATGPT_CHOICE_POLICY_V143__" in contract
    assert "chatgpt_choice_policy_v143: Number(choicePolicy?.revision || 0) >= 143" in contract
    assert '"content_chatgpt_choice_policy_v143.js"' in bootstrap


def test_choice_policy_javascript_syntax() -> None:
    result = subprocess.run(["node", "--check", str(ROOT / POLICY)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
