from pathlib import Path

root = Path(__file__).resolve().parents[1]

for path in (root / "tests").rglob("*"):
    if not path.is_file() or path.suffix not in {".py", ".mjs", ".js"}:
        continue
    text = path.read_text(encoding="utf-8")
    updated = text
    updated = updated.replace("IDLE_CLOSE_MS = 2 * 60 * 1000", "IDLE_CLOSE_MS = 5 * 60 * 1000")
    updated = updated.replace("ROUTE_IDLE_CLOSE_MS = 2 * 60 * 1000", "ROUTE_IDLE_CLOSE_MS = 5 * 60 * 1000")
    updated = updated.replace("route.last_active_at + 2 * 60 * 1000", "route.last_active_at + 5 * 60 * 1000")
    updated = updated.replace("idle deadline must be normalized to two minutes", "idle deadline must be normalized to five minutes")
    updated = updated.replace("two-minute route close alarm", "five-minute route close alarm")
    updated = updated.replace("test_conversation_affinity_is_two_minutes", "test_conversation_affinity_is_five_minutes")
    updated = updated.replace('["multimodal_revision"] == 84', '["multimodal_revision"] == 85')
    if path.name == "test_v02252_multimodal_upload_ready_gate.py":
        updated = updated.replace("multimodal revision 84", "multimodal revision 85")
    if path.name == "runtime_preflight_refresh_v71.mjs":
        updated = updated.replace("last?.multimodal_revision, 84", "last?.multimodal_revision, 85")
    if path.name == "reserve_pool_v29.mjs":
        updated = updated.replace("reserve_window_idle_close_seconds, 120", "reserve_window_idle_close_seconds, 300")
    if path.name == "test_v12.py":
        updated = updated.replace(
            'chrome.windows.create({ url: requestedUrl, focused: false',
            'createManagedWindow({ url: requestedUrl, focused: false',
        )
    if updated != text:
        path.write_text(updated, encoding="utf-8")
