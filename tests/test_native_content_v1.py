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

def test_large_safe_native_tree_is_preserved_without_silent_truncation():
    # This many blocks exceeded the earlier 420-node cutoff, causing MV3C
    # to quietly revert to Markdown even though the original answer was ready.
    rich = {"version": 1, "revision": 7, "root": {
        "tag": "div", "children": [
            {"tag": "div", "children": [{"tag": "p", "text": f"block {g}-{i}"}
                for i in range(60)]}
            for g in range(12)
        ],
    }}
    assert validate_native_snapshot(rich) == rich
    # Keep a hard, deterministic limit; never publish a truncated tree.
    rich["root"]["children"].extend([
        {"tag": "div", "children": [{"tag": "p", "text": f"extra {g}-{i}"}
            for i in range(60)]} for g in range(50)
    ])
    with pytest.raises(ValueError, match="exceeds limit"):
        validate_native_snapshot(rich)


def test_private_snapshot_permissions(tmp_path):
    import os
    store = NativeContentStore(tmp_path)
    rid = "req_private12345678"
    assert store.publish(rid, "private_key", snapshot())
    assert store.persist(rid, "private_key")
    assert os.stat(store.root).st_mode & 0o777 == 0o700
    assert os.stat(store.root / (rid + ".json")).st_mode & 0o777 == 0o600

def test_native_worker_observer_node_regression():
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["node", "--test", "tests/native_content_v1.mjs"],
                   cwd=root, check=True, timeout=20)

def test_expired_disk_and_live_snapshots_are_unreadable(tmp_path, monkeypatch):
    import os
    import time
    from app import native_content_v1 as native
    store = NativeContentStore(tmp_path)
    rid = "imgreq_expired1234567"
    assert store.publish(rid, "key_a", snapshot())
    assert store.persist(rid, "key_a")
    old_time = time.time() - 91 * 86400
    path = store.root / (rid + ".json")
    os.utime(path, (old_time, old_time))
    # A freshly restarted store must refuse the expired persisted snapshot.
    assert NativeContentStore(tmp_path).get(rid, "key_a") is None
    assert not path.exists()
    # Long-running workers must also expire in-memory snapshots after 90 days.
    future_time = time.time() + 91 * 86400
    monkeypatch.setattr(native.time, "time", lambda: future_time)
    assert store.get(rid, "key_a") is None


def test_periodic_cleanup_covers_images_responses_and_chat_requests(tmp_path):
    import os
    import time
    store = NativeContentStore(tmp_path)
    stale = ["imgreq_oldimage12345", "resp_oldreply12345", "req_oldchat12345"]
    for rid in stale:
        assert store.publish(rid, "key_a", snapshot())
        assert store.persist(rid, "key_a")
        path = store.root / (rid + ".json")
        age = time.time() - 91 * 86400
        os.utime(path, (age, age))
    # 32 persisted snapshots trigger the same periodic cleanup across all
    # supported request namespaces, not just req_*.json.
    for i in range(29):
        rid = f"req_freshsnapshot{i:04d}"
        assert store.publish(rid, "key_a", snapshot())
        assert store.persist(rid, "key_a")
    for rid in stale:
        assert not (store.root / (rid + ".json")).exists()


@pytest.mark.parametrize("columns", [
    "348.5px 348.25px 1024px",
    "minmax(0px, 1fr) 348.25px",
    "33.33% 33.33% 33.34%",
])
def test_fractional_computed_grid_columns_survive_safe_contract(columns):
    source = {"version": 1, "revision": 9, "root": {
        "tag": "div", "style": {"display": "grid", "gridTemplateColumns": columns},
        "children": [{"tag": "p", "text": "safe layout"}],
    }}
    assert validate_native_snapshot(source) == source


@pytest.mark.parametrize("columns", [
    "url(https://evil.example/x)",
    "348.5px;position:fixed",
    "100000px 100px",
    "repeat(3, auto)",
])
def test_fractional_grid_columns_still_reject_unsafe_css(columns):
    with pytest.raises(ValueError):
        validate_native_snapshot({"version": 1, "revision": 9, "root": {
            "tag": "div", "style": {"gridTemplateColumns": columns},
        }})


def test_f7c8_deep_game_asset_gallery_retains_media_and_grid():
    # 2026-10-31 req_*f7c8: 594 original nodes, 4 image assets,
    # max source DOM depth 16; the old MAX_DEPTH=14 lost the complete tree.
    root = {"tag": "div", "style": {"display": "grid", "gridTemplateColumns": "1fr 1fr"},
            "children": [{"tag": "img", "media_index": i} for i in range(4)]}
    for _ in range(17):
        root = {"tag": "div", "children": [root]}
    data = {"version": 1, "revision": 11, "root": root}
    validated = validate_native_snapshot(data)
    from app.native_content_v1 import native_layout_summary
    stats = native_layout_summary(validated, archived_assets=4)
    assert stats["images"] == 4
    assert stats["grid"] == 1
    assert stats["media_mapping_complete"] is True
    for _ in range(10):
        root = {"tag": "div", "children": [root]}
    with pytest.raises(ValueError, match="exceeds limit"):
        validate_native_snapshot({"version": 1, "revision": 12, "root": root})


def test_062f_19_archived_images_and_1800_rich_nodes_fit_complete_safe_tree():
    from app.native_content_v1 import validate_native_snapshot, native_layout_summary
    sections=[]
    for group in range(26):
        children=[{"tag":"p","text":f"GameFlow category {group} section {index}"}
                  for index in range(68)]
        if group<19:
            children.append({"tag":"img","media_index":group})
        sections.append({"tag":"div","style":{"display":"grid","gridTemplateColumns":"1fr 1fr"},
                         "children":children})
    payload={"version":1,"revision":30,"root":{"tag":"div","children":sections}}
    accepted=validate_native_snapshot(payload)
    summary=native_layout_summary(accepted,archived_assets=19)
    assert summary["nodes"]>1800
    assert summary["images"]==19
    assert summary["grid"]==26
    assert summary["media_mapping_complete"] is True
    # Raising the limit never permits silent partial tree acceptance.
    larger=[]
    for group in range(60):
        larger.append({"tag":"div","children":[{"tag":"p","text":"x"} for _ in range(60)]})
    with pytest.raises(ValueError,match="exceeds limit"):
        validate_native_snapshot({"version":1,"revision":31,
                                  "root":{"tag":"div","children":larger}})


def test_native_image_mapping_detects_duplicate_missing_and_reordered_indices():
    from app.native_content_v1 import native_layout_summary
    def rich(*indices):
        return validate_native_snapshot({"version": 1, "revision": 40,
            "root": {"tag": "div", "children": [
                {"tag": "img", "media_index": i} for i in indices]}})
    # Previously both cases were falsely flagged complete because 2==2.
    assert native_layout_summary(rich(0, 0), archived_assets=2)["media_mapping_complete"] is False
    assert native_layout_summary(rich(0, 2), archived_assets=2)["media_mapping_complete"] is False
    # Rendering order is allowed to differ, provided each asset is unique.
    assert native_layout_summary(rich(1, 0), archived_assets=2)["media_mapping_complete"] is True
    assert native_layout_summary(rich(0, 1), archived_assets=3)["media_mapping_complete"] is False
    assert native_layout_summary(rich(), archived_assets=0)["media_mapping_complete"] is True


def test_33da_wide_assistant_reply_111_siblings_preserved_in_safe_snapshot():
    # 33da was completed, but evidence captured 2921 elements and a single
    # 111-child top-level body. Old 80-child validator left a stale snapshot.
    children = [
        {"tag": "div", "children": [
            {"tag": "p", "text": f"ceramics plan {i} section {j}"}
            for j in range(19)
        ]} for i in range(102)
    ] + [{"tag": "img", "media_index": i} for i in range(9)]
    payload = {"version": 1, "revision": 27,
               "root": {"tag": "div", "children": [
                   {"tag": "div", "children": children},
               ]}}
    accepted = validate_native_snapshot(payload)
    assert len(accepted["root"]["children"][0]["children"]) == 111
    from app.native_content_v1 import native_layout_summary
    stats = native_layout_summary(accepted, archived_assets=10)
    assert stats["nodes"] > 2000
    assert stats["images"] == 9
    # Asset mapping remains incomplete for this request (9 DOM images vs 10
    # archived assets). Never call it a verified pixel-for-pixel rendering.
    assert stats["media_mapping_complete"] is False
    payload["root"]["children"][0]["children"].extend(
        [{"tag": "p", "text": "overflow"} for _ in range(50)]
    )
    with pytest.raises(ValueError, match="Invalid children"):
        validate_native_snapshot(payload)


def test_33da_subset_has_unique_dimension_candidates_but_no_identity_proof():
    from app.native_content_v1 import native_unique_dimension_candidates
    originals=[(900,900),(1292,860),(1290,860),(1303,860),(1200,687),(1289,860)]
    extra=[(337,450),(1290,860),(1075,860),(1024,1024)]
    # Canonical archive also includes six actual native card sizes.
    archived=[{"width":width,"height":height} for width,height in
              [extra[0],extra[2],extra[3],*originals]]
    payload={"version":1,"revision":30,"root":{"tag":"div","children":[
        {"tag":"img","media_index":i,"intrinsic_width":w,"intrinsic_height":h}
        for i,(w,h) in enumerate(originals)]}}
    saved=validate_native_snapshot(payload)
    matched=native_unique_dimension_candidates(saved,archived)
    assert matched=={"status":"unique-dimensions-unverified","indices":[3,4,5,6,7,8]}
    # A duplicate intrinsic size is ambiguous: refuse rather than guess.
    archived.append({"width":1292,"height":860})
    assert native_unique_dimension_candidates(saved,archived)["status"]=="unavailable"
    archived.pop()
    archived[4]["height"]=900
    assert native_unique_dimension_candidates(saved,archived)["status"]=="unavailable"
    with pytest.raises(ValueError,match="Incomplete image dimensions"):
        validate_native_snapshot({"version":1,"revision":1,"root":{
            "tag":"img","media_index":0,"intrinsic_width":900}})
