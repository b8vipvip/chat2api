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


def test_every_routed_chat_request_passes_both_spare_freshness_gates() -> None:
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    warm = (ROOT / "chrome_extension" / "conversation_warm_pool_v2.js").read_text(encoding="utf-8")
    reserve = (ROOT / "chrome_extension" / "background_reserve_pool_v29.js").read_text(encoding="utf-8")
    workers = (ROOT / "chrome_extension" / "conversation_workers_v25.js").read_text(encoding="utf-8")
    dispatch = (ROOT / "chrome_extension" / "conversation_dispatch.js").read_text(encoding="utf-8")

    assert "MAX_WARM_READY_AGE_MS = 30 * 60 * 1000" in warm
    assert "await pruneExpiredWarmSlots()" in warm
    assert "MAX_RESERVE_READY_AGE_MS = 30 * 60 * 1000" in reserve
    assert "await pruneExpiredReserveSlots()" in reserve
    assert 'conversation_prewarm_freshness_gate: "spare-max-ready-age-v39"' in warm
    assert 'conversation_prewarm_freshness_gate: "spare-max-ready-age-v39"' in reserve

    # Server chat.request messages are routed through workers -> reserve -> warm
    # before conversation_dispatch sends them to the content controller. This is
    # the standard external /v1 path; the playground reaches the same path.
    assert entry.index('"conversation_warm_pool_v2.js"') < entry.index('"background_reserve_pool_v29.js"')
    assert entry.index('"background_reserve_pool_v29.js"') < entry.index('"conversation_workers_v25.js"')
    assert "const baseResolver = globalThis.resolveTargetTabForRequest" in workers
    assert "await resolver(message)" in dispatch
