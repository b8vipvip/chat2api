from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_loading_tabs_remain_open_until_runtime_is_ready() -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests/windows_loading_window_lifecycle_v151.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Windows loading window lifecycle VM contract passed" in result.stdout


def test_no_second_physical_window_owner_and_no_reloading_same_loading_url() -> None:
    source = (ROOT / "chrome_extension/conversation_persistent_pool_v132.js").read_text(encoding="utf-8")
    entry = (ROOT / "chrome_extension/background_entry.js").read_text(encoding="utf-8")
    assert '"background_tab_supervisor_v32.js"' not in entry
    assert 'if (current !== desired)' in source
    assert 'current !== desired || tab.status !== "complete"' not in source
    assert 'state.runtimeRetryAfter.set(row.tab_id' in source
    assert 'chrome.windows.remove(created.id); } catch (_) {}\n      throw error;' not in source
    assert 'while (unassignedWindowRows(rows, value).length < target)' in source
