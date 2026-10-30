from app.worker_probe_store import _sanitize_facts

def test_visual_layout_geometry_is_kept_in_request_logs():
    facts = {
        "scene_layout_version": 1,
        "scene_layout_image_count": 3,
        "scene_layout_image_rows": 1,
        "scene_layout_element_count": 5,
        "scene_layout_geometry": "I:0:0:132:160:512:800:0:2:1;I:140:0:132:160:512:800:0:2:1;G:0:0:420:162:1:3:8:1",
    }
    assert _sanitize_facts(facts) == facts

def test_visual_layout_geometry_rejects_content_urls_and_markup():
    for candidate in (
        "<div>secret</div>",
        "I:1:1:https://example.com/token",
        "data:image/png;base64,secret",
        "hello world",
        "I:1:2:3:4:" + "9" * 4500,
    ):
        assert "scene_layout_geometry" not in _sanitize_facts({
            "scene_layout_geometry": candidate,
            "untrusted_content": "secret",
        })
