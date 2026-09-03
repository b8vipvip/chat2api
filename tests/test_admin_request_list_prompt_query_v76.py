from __future__ import annotations

import inspect
from pathlib import Path

from app.config import Settings
from app.main import create_app
from app.prompt_config_v72_patch import install_prompt_config_v72_patch


def test_prompt_config_request_list_wrapper_preserves_sync_query_contract(tmp_path: Path) -> None:
    app = create_app(Settings(CHAT2API_DATA_DIR=tmp_path))
    install_prompt_config_v72_patch(app)

    telemetry = app.state.telemetry
    assert inspect.iscoroutinefunction(telemetry.query) is False

    result = telemetry.query(limit=100)
    assert isinstance(result, dict)
    assert result["data"] == []
    assert result["total"] == 0


def test_prompt_config_request_list_wrapper_strips_full_prompt_without_coroutine(tmp_path: Path) -> None:
    app = create_app(Settings(CHAT2API_DATA_DIR=tmp_path))
    install_prompt_config_v72_patch(app)

    telemetry = app.state.telemetry
    telemetry.items.append(
        {
            "request_id": "req_v76_regression",
            "status": "completed",
            "final_prompt": "sensitive full prompt",
        }
    )

    result = telemetry.query(limit=100)
    assert inspect.isawaitable(result) is False
    assert result["data"][0]["request_id"] == "req_v76_regression"
    assert result["data"][0]["final_prompt_available"] is True
    assert "final_prompt" not in result["data"][0]
