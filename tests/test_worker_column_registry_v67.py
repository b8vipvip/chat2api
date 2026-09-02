from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_effective_columns_are_registered_once_in_canonical_settings_schema():
    canonical = read("app/admin_extension_columns.js")
    assert canonical.count('{key: "device_name", label: "设备名称"}') == 1
    assert canonical.count('{key: "occupancy", label: "当前占用"}') == 1
    assert '{key: "occupied_windows",' not in canonical
    assert 'data-chat2api-column-key="device_name"' in canonical
    assert 'data-chat2api-column-key="occupancy"' in canonical
    assert 'column_schema_revision: COLUMN_SCHEMA_REVISION' in canonical


def test_legacy_plain_occupied_windows_column_is_retired_without_polling():
    legacy = read("app/admin_worker_runtime_v61.js")
    assert 'replacement_column: "occupancy"' in legacy
    assert 'retired: true' in legacy
    assert 'revision: 67' in legacy
    assert 'querySelectorAll(\'[data-chat2api-column-key="occupied_windows"]\')' in legacy
    assert '/api/admin/capacity-v57' not in legacy
    assert 'MutationObserver' not in legacy
    assert 'setInterval(' not in legacy
    assert 'setTimeout(' not in legacy


def test_bounded_v66_enhancer_updates_existing_canonical_cells_before_fallback_creation():
    enhancer = read("app/admin_worker_presentation_v66.js")
    assert 'let nameCell = keyedChild(tr, "device_name")' in enhancer
    assert 'if (!nameCell)' in enhancer
    assert 'let occupancyCell = keyedChild(tr, "occupancy")' in enhancer
    assert 'if (!occupancyCell)' in enhancer
    assert 'MutationObserver' not in enhancer
    assert 'setInterval(' not in enhancer


def test_changed_admin_scripts_are_valid_javascript():
    for relative in (
        "app/admin_extension_columns.js",
        "app/admin_worker_runtime_v61.js",
        "app/admin_worker_presentation_v66.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / relative)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{relative}: {result.stderr}"
