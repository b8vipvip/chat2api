from pathlib import Path
import subprocess

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.v21_11_patch import ADMIN_COLUMN_MENU_VIEWPORT_ASSET, install_v21_11_patch


ROOT = Path(__file__).resolve().parents[1]


def test_column_menu_viewport_patch_is_loaded_after_runtime_contract():
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v21_11_patch import install_v21_11_patch" in entry
    assert entry.index("install_runtime_contract(app)") < entry.index("install_v21_11_patch(app)")


def test_column_menu_viewport_asset_is_admin_only_and_no_cache():
    app = FastAPI()

    @app.get("/admin")
    async def admin_page():
        return HTMLResponse("<html><body>admin</body></html>")

    @app.get("/developers")
    async def developers_page():
        return HTMLResponse("<html><body>developers</body></html>")

    install_v21_11_patch(app)
    client = TestClient(app)

    admin = client.get("/admin")
    assert f'<script src="{ADMIN_COLUMN_MENU_VIEWPORT_ASSET}"></script>' in admin.text
    assert "no-store" in admin.headers.get("cache-control", "")
    assert ADMIN_COLUMN_MENU_VIEWPORT_ASSET not in client.get("/developers").text

    asset = client.get(ADMIN_COLUMN_MENU_VIEWPORT_ASSET)
    assert asset.status_code == 200
    assert "javascript" in asset.headers.get("content-type", "")
    assert "no-store" in asset.headers.get("cache-control", "")


def test_column_menu_repositions_inside_viewport_and_scrolls_internally():
    source = (ROOT / "app" / "admin_v21_11.js").read_text(encoding="utf-8")

    for token in (
        'const MENU_ID = "extensionColumnSettingsMenu"',
        'const BUTTON_ID = "extensionColumnSettingsButton"',
        "menu.scrollHeight",
        "availableBelow",
        "availableAbove",
        "openAbove",
        'menu.style.maxHeight = `${maxHeight}px`',
        'menu.style.overflowY = naturalHeight > maxHeight ? "auto" : "visible"',
        'menu.style.overscrollBehavior = "contain"',
        'menu.style.scrollbarGutter = "stable"',
        'window.addEventListener("resize", scheduleAdjust)',
        'window.addEventListener("scroll", scheduleAdjust, true)',
        'document.addEventListener("click"',
    ):
        assert token in source

    assert "setInterval(" not in source
    assert "fetch(" not in source
    assert "chrome." not in source


def test_column_menu_viewport_javascript_syntax():
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_v21_11.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
