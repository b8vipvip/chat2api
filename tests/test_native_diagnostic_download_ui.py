"""Exercise real export handlers through refresh, stream, abort and save races."""
from pathlib import Path
import subprocess


def test_native_diagnostic_download_handlers():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["node", "--test", "tests/native_diagnostic_download_ui.mjs"],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_download_progress_panel_survives_record_replacement():
    source = (Path(__file__).resolve().parents[1] / "app/worker_probe_ui_patch.py").read_text()
    assert source.count('id="nativePageDownloads"') == 1
    assert source.index('id="nativePageDownloads"') < source.index('<tbody id="nativePageRecords"')
