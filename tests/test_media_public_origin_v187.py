"""Reverse-proxy public image URL regression for HTTPS admin/testing clients."""
from app.media_public_origin_v187 import effective_media_base_url


def test_browser_media_origin_uses_verified_https_same_host():
    # 0.22.175: TLS terminates at the proxy and request.base_url becomes
    # http://chat2api.mv3.cn, exposing mixed-content image links.
    assert effective_media_base_url(
        "http://chat2api.mv3.cn/", "https://chat2api.mv3.cn"
    ) == "https://chat2api.mv3.cn"
    assert effective_media_base_url(
        "http://chat2api.mv3.cn/", "https://chat2api.mv3.cn/"
    ) == "https://chat2api.mv3.cn"


def test_untrusted_or_cross_host_origin_never_controls_media_links():
    base = "http://chat2api.mv3.cn"
    for origin in (
        None, "", "http://chat2api.mv3.cn",
        "https://evil.example", "https://chat2api.mv3.cn.evil.example",
        "https://user:password@chat2api.mv3.cn",
        "https://chat2api.mv3.cn/malicious",
        "https://chat2api.mv3.cn?redirect=1",
        "https://chat2api.mv3.cn#fragment",
        "https://chat2api.mv3.cn:444",
        "https://chat2api.mv3.cn:badport",
    ):
        assert effective_media_base_url(base, origin) == base

    assert effective_media_base_url(
        "https://chat2api.mv3.cn", "https://chat2api.mv3.cn"
    ) == "https://chat2api.mv3.cn"
    assert effective_media_base_url(
        "http://localhost:5000", "https://localhost:5000"
    ) == "http://localhost:5000"


def test_only_media_archive_urls_change_not_existing_objects(tmp_path):
    import base64
    import io
    from PIL import Image
    from app.media_archive_v173 import MediaArchive

    img = Image.new("RGB", (64, 64), "red")
    out = io.BytesIO()
    img.save(out, format="PNG")
    store = MediaArchive(tmp_path, "test-media-key")
    markdown = "![Generated image](data:image/png;base64," + base64.b64encode(out.getvalue()).decode() + ")"
    normalized, rows = store.rewrite_markdown(
        markdown, "owner-key", "req-123",
        effective_media_base_url("http://chat2api.mv3.cn", "https://chat2api.mv3.cn"),
    )
    assert normalized.startswith("![Generated image](https://chat2api.mv3.cn/api/media/")
    assert rows[0]["url"].startswith("https://chat2api.mv3.cn/api/media/")
    assert rows[0]["thumbnail_url"].startswith(rows[0]["url"])
    # The exact same stored file is served as full-resolution original.
    ref = rows[0]["url"].split("/api/media/")[1].split("/")
    assert store.load(ref[0], ref[1])[0] == out.getvalue()


def test_all_public_media_api_routes_use_same_https_projection():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "app/main.py").read_text(encoding="utf8")
    assert source.count('media_archive._config().get("public_base_url") or media_base_for_request(request)') == 3, (
        "media, generated-file metadata, and Images URL mode must share the chat archive's HTTPS origin handling"
    )
    assert 'media_archive._config().get("public_base_url") or str(request.base_url)' not in source
    assert source.count('media_base_for_request(request),') == 4, (
        "connected/detached stream and nonstream media archive need identical origin handling"
    )
