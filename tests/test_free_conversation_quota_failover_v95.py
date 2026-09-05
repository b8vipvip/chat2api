from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_free_conversation_quota_is_rotated_instead_of_honoring_five_minute_affinity() -> None:
    content = read("chrome_extension/content_conversation_quota_failover_v95.js")
    background = read("chrome_extension/background_conversation_quota_failover_v95.js")

    for token in (
        'const KEY = "__CHAT2API_CONVERSATION_QUOTA_FAILOVER_V95__"',
        'const MESSAGE_TYPE = "chat2api.conversation-quota-blocked.v95"',
        "聊天已暂停",
        "纯文本聊天",
        "activePromptStillPresent(active)",
        'stage: "pre-dispatch-disabled-conversation"',
        'controller: "conversation-quota-failover-v95"',
    ):
        assert token in content

    for token in (
        'const KEY = "__CHAT2API_CONVERSATION_QUOTA_BACKGROUND_V95__"',
        "const MAX_ROTATIONS = 2",
        'conversation_quota_affinity_overridden: true',
        'recycle(requestId, "conversation-local-quota-blocked-v95")',
        "await handleServerMessage(replay)",
        'replay.__conversation_quota_failover_replay_v95 = attempt',
        'conversation_quota_failover_action: "replayed-on-fresh-window"',
        "__CHAT2API_CONVERSATION_WORKERS_V25__?.releaseRequest",
    ):
        assert token in background

    # This path is deliberately bounded. If a fresh chat is also blocked because
    # the condition is actually account-wide, the same API request cannot reopen
    # windows forever.
    assert "attempt > MAX_ROTATIONS" in background
    assert "conversation_quota_failover_exhausted: true" in background


def test_conversation_local_quota_does_not_arm_account_wide_rate_limit() -> None:
    rate = read("chrome_extension/content_rate_limit_guard_v52.js")
    background = read("chrome_extension/background_conversation_quota_failover_v95.js")

    assert "CONVERSATION_LOCAL_MATCHERS" in rate
    assert "matchesConversationLocalText(text)" in rate
    assert "if (!text || matchesConversationLocalText(text)) return false;" in rate
    assert "state.matchesConversationLocal = matchesConversationLocalText" in rate

    # The background recovery may clear v52 only when the stored cooldown came
    # from the same conversation-local surface; a genuine account-wide cooldown
    # detected by another window must remain authoritative.
    assert "looksConversationLocal(current?.text)" in background
    assert 'String(current?.url || "") === String(href || "")' in background
    assert "if (!sameSurface) return false;" in background


def test_v95_is_loaded_after_request_lifecycle_and_recovery_and_required_by_preflight() -> None:
    manifest = json.loads(read("chrome_extension/manifest.json"))
    isolated = next(item for item in manifest["content_scripts"] if item.get("world") != "MAIN")
    scripts = isolated["js"]
    assert scripts.index("content_request_v6.js") < scripts.index("content_request_lifecycle_v50.js")
    assert scripts.index("content_request_lifecycle_v50.js") < scripts.index("content_conversation_quota_failover_v95.js")
    assert scripts.index("content_conversation_quota_failover_v95.js") < scripts.index("content_request_hygiene_v42.js")

    entry = read("chrome_extension/background_entry.js")
    assert entry.index('"background_request_recovery_v40.js"') < entry.index('"background_conversation_quota_failover_v95.js"')
    assert entry.index('"background_conversation_quota_failover_v95.js"') < entry.index('"background_transport_recovery_v47.js"')

    bootstrap = read("chrome_extension/content_bootstrap.js")
    preflight = read("chrome_extension/background_runtime_preflight_v48.js")
    contract = read("chrome_extension/content_runtime_contract_v71.js")
    assert '"content_conversation_quota_failover_v95.js"' in bootstrap
    assert '"content_conversation_quota_failover_v95.js"' in preflight
    assert "result?.modules?.conversation_quota_failover_v95" in preflight
    assert "conversation_quota_failover_v95:" in contract
    assert 'Number(conversationQuota?.revision || 0) >= 95' in contract


def test_v95_javascript_assets_parse() -> None:
    for path in (
        "chrome_extension/content_conversation_quota_failover_v95.js",
        "chrome_extension/background_conversation_quota_failover_v95.js",
        "chrome_extension/content_rate_limit_guard_v52.js",
        "chrome_extension/background_runtime_preflight_v48.js",
        "chrome_extension/content_runtime_contract_v71.js",
        "chrome_extension/background_entry.js",
    ):
        completed = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert completed.returncode == 0, f"{path}: {completed.stderr}"
