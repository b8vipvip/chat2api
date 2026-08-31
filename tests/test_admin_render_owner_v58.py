from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_worker_window_has_one_structural_owner() -> None:
    worker = read("app/admin_v21_5.js")
    health = read("app/admin_v21_6.js")

    assert 'data-chat2api-structural-owner="worker-window-v58"' in worker
    assert 'globalThis.__CHAT2API_WORKER_WINDOW_RENDER_OWNER_V58__' in worker
    assert 'globalThis.chat2apiRefreshWorkerWindowEditorsV58' in worker
    assert 'structural_updates: "create-once-update-values"' in worker
    assert 'polling: false' in worker

    # The old v0.22.39 regression was caused by v21_6 writing platform text every
    # 1.5s while v21_5 replaced the same cell with the Worker-window editor every
    # 2s. The health layer must never take structural ownership of platform again.
    assert 'renderState(ensureCell(tr, "platform")' not in health
    assert '["platform", "平台"]' not in health
    assert 'worker_window: "admin_v21_5"' in health
    assert 'health_columns: ["network", "chatgpt"]' in health
    assert 'globalThis.chat2apiRefreshWorkerWindowEditorsV58?.(rows)' in health


def test_worker_window_refresh_is_event_driven_and_idempotent() -> None:
    worker = read("app/admin_v21_5.js")

    # Structural owner may create the editor once, but must not periodically
    # destroy/rebuild it. Row replacement is observed at tbody level and data
    # changes update values/text in place.
    assert "setInterval(" not in worker
    assert "requestAnimationFrame" in worker
    assert 'new MutationObserver(mutations =>' in worker
    assert '.observe(extensionBody, {childList:true})' in worker
    assert 'if (!editor || String(editor.dataset.clientId || "") !== clientId)' in worker
    assert 'if (live.textContent !== next) live.textContent = next' in worker
    assert 'if (platform && platform.textContent !== platformLabel) platform.textContent = platformLabel' in worker


def test_common_bug_document_records_render_owner_rule() -> None:
    doc = read("docs/COMMON_DEVELOPMENT_BUGS.md")
    assert "Multiple Render Owners" in doc
    assert "一个结构区域只能有一个 Structural Owner" in doc
    assert "659b9b9b9af9cecd08370ef581656acbe4b7c963" in doc
    assert "f6cee62e2744127cadc690242257283a282b0fda" in doc
    assert "882d1f7a4890c4a14ef3c16d7651c1a2e48824c3" in doc
    assert "请求一直 Running，但 Worker 槽位不释放" in doc


def test_admin_render_owner_scripts_parse_as_javascript() -> None:
    for path in ("app/admin_v21_5.js", "app/admin_v21_6.js"):
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
