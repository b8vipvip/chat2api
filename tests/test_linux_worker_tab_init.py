from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "linux_worker_tab_init.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("linux_worker_tab_init", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reconcile_closes_39_restored_chatgpt_pages_and_keeps_other_sites(monkeypatch):
    module = _load_module()
    targets = [
        {
            "id": f"chat-{index}",
            "type": "page",
            "url": "https://chatgpt.com/" if index == 39 else f"https://chatgpt.com/c/{index}",
        }
        for index in range(40)
    ]
    targets.append({"id": "other", "type": "page", "url": "https://example.com/"})
    closed: list[str] = []

    monkeypatch.setattr(module, "_json", lambda *_args, **_kwargs: targets)
    monkeypatch.setattr(module, "_close", lambda _debug_url, target_id: closed.append(target_id) or True)

    result = module.reconcile("http://127.0.0.1:9222", keep=1)

    assert result == {"seen": 40, "closed": 39, "remaining": 1}
    assert len(closed) == 39
    assert "chat-39" not in closed, "normal ChatGPT root page should be the initialization authority"
    assert "other" not in closed, "startup hygiene must never close non-ChatGPT tabs"


def test_launcher_discards_only_window_restore_state_and_runs_one_shot_cdp_cleanup():
    text = (ROOT / "scripts" / "linux_worker_chrome_launcher.sh").read_text(encoding="utf-8")
    assert 'rm -rf "${PROFILE_DIR}/Default/Sessions"' in text
    assert "Cookies" in text
    assert 'linux_worker_tab_init.py' in text
    assert '--keep 1 --wait 45' in text


def test_extension_autoreload_adopts_fingerprint_when_running_chrome_is_newer_than_source():
    text = (ROOT / "scripts" / "linux_extension_autoreload.sh").read_text(encoding="utf-8")
    assert "chrome_started_after_extension_source" in text
    assert "without a second restart" in text
