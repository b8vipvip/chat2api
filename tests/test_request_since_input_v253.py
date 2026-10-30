from pathlib import Path
import asyncio
import subprocess

from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch

ROOT=Path(__file__).resolve().parents[1]


def test_pasteable_since_ui_and_no_stale_async_results():
    result=subprocess.run(
        ["node", "tests/request_since_input_v253.mjs"], cwd=ROOT,
        capture_output=True, text=True, timeout=25, check=False,
    )
    assert result.returncode==0,result.stdout+"\n"+result.stderr


def test_admin_request_list_echoes_cutoff_and_filters_rows(tmp_path):
    settings=Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_DATA_DIR=tmp_path,
    )
    app=create_app(settings)
    install_v7_patch(app)
    install_v8_patch(app)
    with TestClient(app) as client:
        for ident,when in (
            ("req-before", "2026-10-30T10:25:06+08:00"),
            ("req-after", "2026-10-30T12:13:16+08:00"),
        ):
            asyncio.run(app.state.telemetry.upsert({
                "request_id":ident, "recorded_at":when,
                "status":"completed", "requested_model":"gpt-6",
            }))
        response=client.get("/api/admin/requests",
            params={"since":"2026-10-30T03:56:59.000Z","limit":100},
            headers={"Authorization":"Bearer master-key"})
        assert response.status_code==200,response.text
        result=response.json()
        assert [row["request_id"] for row in result["data"]]==["req-after"]
        assert result["applied_filters"]["since"]=="2026-10-30T03:56:59+00:00"
        assert result["total"]==1
        all_rows=client.get("/api/admin/requests",headers={"Authorization":"Bearer master-key"})
        assert all_rows.status_code==200
        assert all_rows.json()["applied_filters"]["since"] is None
