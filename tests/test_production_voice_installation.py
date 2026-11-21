import ast
from pathlib import Path


def test_production_entrypoint_installs_voice_after_quota_runtime():
    source = Path("app/entry.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "install_voice_patch" in calls
    assert "install_live_voice_patch" in calls
    assert source.index("install_quota_reservation_runtime_v1(app)") < source.index(
        "install_voice_patch(app)"
    )
    assert source.index("install_voice_patch(app)") < source.index(
        "install_live_voice_patch(app)"
    )
    assert source.index("install_live_voice_patch(app)") < source.index(
        "install_web_chat_features_v1(app)"
    )
