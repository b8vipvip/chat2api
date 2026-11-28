"""Regression for cross-process generated-file archives and GPT-Live stop race."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_v230_stop_during_live_start_is_not_orphaned():
    if not shutil.which("node"):
        return
    run = subprocess.run(
        ["node", "tests/live_stop_during_start_v230.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + "\n" + run.stderr


def test_v230_file_handoff_is_single_sqlite_transaction():
    source = (ROOT / "app/generated_files_v196.py").read_text(encoding="utf-8")
    assert "BEGIN IMMEDIATE" in source
    # Archive handoff and guarded deletion are independent transactions.
    import ast
    methods = {node.name: ast.get_source_segment(source, node)
               for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef)}
    assert sum(body.count('conn.execute("BEGIN IMMEDIATE")') for name, body in methods.items()
               if name != "delete") >= 2
    assert 'conn.execute("BEGIN IMMEDIATE")' in methods["delete"]
    assert "def _store_in_transaction" in source
    assert "def _rollback_unpublished_objects" in source
    assert 'marker_positions != {m.start() for m in matches}' in source
