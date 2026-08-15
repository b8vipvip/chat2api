from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_console_hotfix_removes_broad_dom_observer() -> None:
    admin = (ROOT / "app" / "admin_v21_1.js").read_text(encoding="utf-8")
    assert 'const VERSION = "0.21.2"' in admin
    assert "MutationObserver" not in admin
    assert "function patchVisibleView(view)" in admin
    assert 'document.querySelectorAll(".nav button[data-view]")' in admin
    assert "let loadInFlight = null" in admin
    assert "if (loadInFlight) return loadInFlight" in admin


def test_v212_patch_is_installed_after_configurable_concurrency() -> None:
    patch = (ROOT / "app" / "v21_2_patch.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert 'PATCH_VERSION = "0.21.2"' in patch
    assert "from .v21_2_patch import install_v21_2_patch" in entry
    assert entry.index("install_v21_1_patch(app)") < entry.index("install_v21_2_patch(app)")
