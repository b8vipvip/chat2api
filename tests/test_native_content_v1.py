import pytest
from app.native_content_v1 import NativeContentStore, validate_native_snapshot

def snapshot(revision=1):
    return {"version": 1, "revision": revision,
            "root": {"tag": "div", "style": {"display": "grid",
                       "gridTemplateColumns": "1fr 1fr 1fr", "gap": "8px"},
                     "children": [{"tag": "img", "media_index": 0},
                                  {"tag": "img", "media_index": 1},
                                  {"tag": "p", "text": "example"}]}}

def test_native_sidecar_is_versioned_and_owner_isolated(tmp_path):
    store = NativeContentStore(tmp_path)
    assert store.publish("req_12345678", "key_a", snapshot())
    assert not store.publish("req_12345678", "key_b", snapshot(2))
    assert not store.publish("req_12345678", "key_a", snapshot())
    assert store.publish("req_12345678", "key_a", snapshot(2))
    assert store.get("req_12345678", "key_b") is None
    assert store.persist("req_12345678", "key_a")
    assert NativeContentStore(tmp_path).get("req_12345678", "key_a")["revision"] == 2
    assert NativeContentStore(tmp_path).get("req_12345678", "key_b") is None

@pytest.mark.parametrize("payload", [
    {"version": 1, "revision": 1, "root": {"tag": "script", "text": "alert(1)"}},
    {"version": 1, "revision": 1, "root": {"tag": "img", "media_index": 0, "src": "https://evil"}},
    {"version": 1, "revision": 1, "root": {"tag": "div", "style": {"backgroundImage": "url(x)"}}},
    {"version": 1, "revision": 1, "root": {"tag": "div", "style": {"color": "red;position:fixed"}}},
    {"version": 1, "revision": 1, "root": {"tag": "div", "children": [{"tag": "iframe"}]}},
    {"version": 1, "revision": 1, "root": {"tag": "div", "children": []}, "html": "<div>"},
    {"version": 1, "revision": 0, "root": {"tag": "p"}},
])
def test_disallowed_native_content_is_rejected(payload):
    with pytest.raises(ValueError):
        validate_native_snapshot(payload)

def test_safe_layout_is_preserved():
    assert validate_native_snapshot(snapshot()) == snapshot()
