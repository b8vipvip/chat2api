from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.v21_3_patch import ADMIN_SCRIPT_ORDER, BUNDLE_ASSET, install_v21_3_patch


ROOT = Path(__file__).resolve().parents[1]


def test_v213_bundle_order_and_auth_recovery_contract() -> None:
    assert ADMIN_SCRIPT_ORDER.index("admin_v16.js") < ADMIN_SCRIPT_ORDER.index("admin_v17.js")
    assert ADMIN_SCRIPT_ORDER.index("admin_v17.js") < ADMIN_SCRIPT_ORDER.index("admin_v17_1.js")
    assert ADMIN_SCRIPT_ORDER.index("admin_v17_1.js") < ADMIN_SCRIPT_ORDER.index("admin_v18.js")
    assert ADMIN_SCRIPT_ORDER[-2:] == ["admin_v21.js", "admin_v21_1.js"]

    recovery = (ROOT / "app" / "admin_v21_3.js").read_text(encoding="utf-8")
    assert 'const VERSION = "0.21.3"' in recovery
    assert 'document.getElementById("adminLoginGate")' in recovery
    assert 'globalThis.key = () =>' in recovery
    assert '"/api/admin/auth/session"' in recovery
    assert '"/api/admin/auth/login"' in recovery
    assert "CHAT2API_API_KEY 作为管理员凭据" in recovery


def test_v213_replaces_legacy_script_tags_with_one_bundle() -> None:
    app = FastAPI()

    @app.get("/admin")
    async def admin() -> HTMLResponse:
        return HTMLResponse(
            '<html><body><script>window.base=true;</script>'
            '<script src="/assets/chat2api-v17.js"></script>'
            '<script src="/assets/chat2api-v21-1.js"></script>'
            '</body></html>'
        )

    install_v21_3_patch(app)
    client = TestClient(app)

    page = client.get("/admin")
    assert page.status_code == 200
    assert page.text.count(BUNDLE_ASSET) == 1
    assert 'src="/assets/chat2api-v17.js"' not in page.text
    assert 'src="/assets/chat2api-v21-1.js"' not in page.text
    assert "window.base=true" in page.text

    bundle = client.get(BUNDLE_ASSET)
    assert bundle.status_code == 200
    assert "loading ordered admin bundle v0.21.3" in bundle.text
    assert bundle.text.index("BEGIN admin_v16.js") < bundle.text.index("BEGIN admin_v17.js")
    assert bundle.text.index("BEGIN admin_v17.js") < bundle.text.index("v21.3 admin auth checkpoint")
    assert bundle.text.index("BEGIN admin_v17_1.js") < bundle.text.index("BEGIN admin_v18.js")
    assert "window.__chat2apiAdminPatchErrors" in bundle.text
    assert "admin bundle ready" in bundle.text


def test_v213_entry_installs_last() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v21_3_patch import install_v21_3_patch" in entry
    assert entry.index("install_v21_2_patch(app)") < entry.index("install_v21_3_patch(app)")
