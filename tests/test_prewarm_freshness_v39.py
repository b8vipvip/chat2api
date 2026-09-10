from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_warm_pool_rejects_four_hour_spares_without_closing_live_routes() -> None:
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "prewarm_freshness_v39.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "prewarm_freshness_v39 VM contract passed" in result.stdout


def test_legacy_spare_freshness_guards_remain_as_source_but_are_retired_from_production() -> None:
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    warm = (ROOT / "chrome_extension" / "conversation_warm_pool_v2.js").read_text(encoding="utf-8")
    reserve = (ROOT / "chrome_extension" / "background_reserve_pool_v29.js").read_text(encoding="utf-8")
    router = (ROOT / "chrome_extension" / "conversation_routing.js").read_text(encoding="utf-8")
    dispatch = (ROOT / "chrome_extension" / "conversation_dispatch.js").read_text(encoding="utf-8")

    assert "MAX_WARM_READY_AGE_MS = 30 * 60 * 1000" in warm
    assert "await pruneExpiredWarmSlots()" in warm
    assert "MAX_RESERVE_READY_AGE_MS = 30 * 60 * 1000" in reserve
    assert "await pruneExpiredReserveSlots()" in reserve
    assert 'conversation_prewarm_freshness_gate: "spare-max-ready-age-v39"' in warm
    assert 'conversation_prewarm_freshness_gate: "spare-max-ready-age-v39"' in reserve

    # v0.8.30 no longer pre-creates spare windows. The historical modules remain
    # inspectable for regressions, but the production entry must not load them.
    for retired in (
        "conversation_warm_pool_v2.js",
        "background_reserve_pool_v29.js",
        "conversation_workers_v25.js",
    ):
        assert f'"{retired}"' not in entry
    assert '"conversation_routing.js"' in entry
    assert '"conversation_dispatch.js"' in entry
    assert 'authority: "single-route-window-authority-v30"' in router
    assert "browser_side_same_api_queue: false" in router
    assert "Same-logical-API request admission is exclusively server scheduler v58" in dispatch
