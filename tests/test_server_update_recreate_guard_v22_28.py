from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_updater_bypasses_unbounded_compose_recreate_path():
    updater = (ROOT / "scripts" / "chat2api_server_update.sh").read_text(encoding="utf-8")

    for token in (
        "replace_chat2api_container()",
        "service_container_ids()",
        'label=com.docker.compose.project.working_dir=${APP_DIR}',
        'label=com.docker.compose.service=${COMPOSE_SERVICE}',
        'docker stop --timeout "$CONTAINER_STOP_SECONDS"',
        "graceful stop timed out/failed",
        "docker kill $ids",
        "docker rm -f $ids",
        'docker compose up -d --no-deps --no-build "$COMPOSE_SERVICE"',
        "COMPOSE_UP_COMMAND_SECONDS",
        "service is healthy despite Compose CLI timeout",
        'replace_chat2api_container "rollback"',
        'CURRENT_MESSAGE="新版本容器切换"',
        "exit 7",
    ):
        assert token in updater

    # The update path must not hand an existing container to Compose's recreate
    # state machine again; it explicitly stops/removes the old service first.
    assert "docker compose up -d --remove-orphans" not in updater
    assert "docker rm -f $ids -v" not in updater


def test_container_shutdown_contract_is_explicit_and_signal_safe():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'CMD ["sh", "-c", "exec uvicorn app.entry:app' in dockerfile
    assert "stop_grace_period: 20s" in compose


def test_updater_shell_parses_after_recreate_guard_changes():
    result = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / "chat2api_server_update.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
