from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v57_owns_final_admission_after_account_free_safety_patch() -> None:
    hook = read("app/server_worker_sync_lifespan_patch.py")
    source = read("app/capacity_queue_v57_patch.py")
    assert "install_capacity_queue_v57_patch" in hook
    assert 'PATCH_ID = "worker-key-capacity-queue-v57"' in source
    assert 'broker.create = create_fifo' in source
    assert 'broker.release = release_fifo' in source
    assert 'capacity_queue_mode": "fifo-unbounded-v57"' in source
    assert 'FREE_ACCOUNT_GENERATION_LIMIT' not in source
    assert 'account_generation_queue_wait_seconds": None' in source


def test_worker_window_settings_are_independent_and_default_to_three() -> None:
    source = read("app/capacity_queue_v57_patch.py")
    admin = read("app/admin_v21_5.js")
    for token in (
        "DEFAULT_WORKER_CONCURRENCY = 3",
        "DEFAULT_RESERVE_WINDOWS = 3",
        '"max_concurrency"',
        '"reserve_windows"',
        '/api/admin/extensions/{client_id}/capacity-v57',
        'routing["worker_limit"] = max(worker_limit(client_id), reserve_limit(client_id))',
    ):
        assert token in source
    for token in (
        'platformHeader.textContent = "Worker 窗口"',
        '["concurrency", "reserve_windows"]',
        'data-worker-max',
        'data-worker-reserve',
        '最大并发',
        '空闲备用窗口',
    ):
        assert token in admin


def test_per_api_key_concurrency_defaults_to_three_and_queues() -> None:
    source = read("app/capacity_queue_v57_patch.py")
    admin = read("app/admin_v21_5.js")
    for token in (
        "DEFAULT_KEY_CONCURRENCY = 3",
        "key_queues: dict[str, deque[str]]",
        "key_active: dict[str, int]",
        "key_active[key_id] < key_limit(key_id)",
        '/api/admin/keys/{key_id}/concurrency-v57',
        '"api_key_capacity_limit"',
    ):
        assert token in source
    assert 'th.textContent = "最大并发"' in admin
    assert 'data-key-max' in admin
    assert '超过部分将排队依次执行' in admin


def test_rate_limit_guard_becomes_admission_cooldown_signal() -> None:
    source = read("app/capacity_queue_v57_patch.py")
    assert 'broker.publish = publish_rate_aware' in source
    assert '"chatgpt is temporarily rate limited"' in source
    assert '"too many requests"' in source
    assert 'RATE_LIMIT_DEFAULT_SECONDS = 300' in source
    assert 'rate_limit_cooldown_active' in source


def test_admin_capacity_javascript_parses() -> None:
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app/admin_v21_5.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
