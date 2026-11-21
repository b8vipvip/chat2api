import ast
from pathlib import Path


def test_production_entrypoint_installs_each_voice_router_once():
    source = Path("app/entry.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert calls.count("install_voice_patch") == 1
    assert calls.count("install_live_voice_patch") == 1
