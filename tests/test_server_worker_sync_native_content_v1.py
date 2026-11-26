from app.server_worker_sync_patch import worker_update_path


def test_native_content_worker_asset_change_forces_worker_refresh() -> None:
    assert worker_update_path("chrome_extension/content_native_content_v1.js") is True
    assert worker_update_path("chrome_extension/manifest.json") is True
    assert worker_update_path("app/chat_native_renderer_v1.js") is False
