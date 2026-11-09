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

def test_empty_and_merged_table_cells_roundtrip_without_shifting_columns(tmp_path):
    from app.native_content_v1 import native_public_v1_tree

    doc = {"version": 1, "revision": 1, "root": {
        "tag": "table", "children": [
            {"tag": "thead", "children": [{"tag": "tr", "children": [
                {"tag": "th", "col_span": 3, "row_span": 2, "text": "合并表头"},
                {"tag": "th"},
            ]}]},
            {"tag": "tbody", "children": [{"tag": "tr", "children": [
                {"tag": "td"},
                {"tag": "td", "text": "保留原列"},
            ]}]},
        ]}}
    value = validate_native_snapshot(doc)
    header = value["root"]["children"][0]["children"][0]["children"]
    body = value["root"]["children"][1]["children"][0]["children"]
    assert len(header) == len(body) == 2
    assert header[0]["col_span"] == 3 and header[0]["row_span"] == 2
    assert header[1] == {"tag": "th"}
    assert body[0] == {"tag": "td"}
    assert body[1]["text"] == "保留原列"
    store = NativeContentStore(tmp_path)
    assert store.publish("req_tablemerge12345", "owner", doc)
    assert store.persist("req_tablemerge12345", "owner")
    assert NativeContentStore(tmp_path).get("req_tablemerge12345", "owner") == value
    public = native_public_v1_tree(value["root"])
    assert "col_span" not in public["children"][0]["children"][0]["children"][0]
    assert "row_span" not in public["children"][0]["children"][0]["children"][0]


@pytest.mark.parametrize("tag,spans", [
    ("div", {"col_span": 2}),
    ("tr", {"row_span": 2}),
    ("td", {"col_span": 0}),
    ("th", {"col_span": True}),
    ("th", {"col_span": 257}),
    ("td", {"row_span": -1}),
    ("td", {"row_span": 1}),
    ("td", {"row_span": 257}),
    ("td", {"row_span": "2"}),
])
def test_table_spans_reject_non_cells_and_untrusted_bounds(tag, spans):
    with pytest.raises(ValueError):
        validate_native_snapshot({"version": 1, "revision": 1,
                                  "root": {"tag": tag, **spans}})


def test_bounded_table_box_model_preserves_private_layout_not_legacy_v1():
    from app.native_content_v1 import native_public_v1_tree

    doc = {"version": 1, "revision": 1, "root": {
        "tag": "table",
        "style": {"boxSizing": "border-box", "borderCollapse": "collapse",
                  "borderSpacing": "0px 2.5px"},
        "children": [{"tag": "tr", "children": [{"tag": "td"}]}]}}
    safe = validate_native_snapshot(doc)["root"]
    assert safe["style"] == doc["root"]["style"]
    legacy = native_public_v1_tree(safe)
    assert legacy["style"] == {}
    assert legacy["children"][0]["children"][0] == {"tag": "td"}


@pytest.mark.parametrize("tag,style", [
    ("table", {"borderSpacing": "0px 0px;url(https://evil.invalid)"}),
    ("table", {"borderSpacing": "calc(10px)"}),
    ("table", {"borderSpacing": "1px 2px 3px"}),
    ("td", {"borderSpacing": "0px 0px"}),
    ("table", {"boxSizing": "border-box;position:fixed"}),
    ("table", {"borderCollapse": "collapse;display:none"}),
])
def test_table_box_model_refuses_unsafe_style_values(tag, style):
    with pytest.raises(ValueError):
        validate_native_snapshot({"version": 1, "revision": 1, "root": {
            "tag": tag, "style": style}})


@pytest.mark.parametrize("color", [
    "oklab(0.683029 -0.16619 0.102668 / 0.15)",
    "oklab(0.159065 0.00000723451 0.00000317395 / 0.08)",
    "color(srgb 0.952941 0.952941 0.952941 / 0.82)",
])
def test_observed_windows_linux_modern_colors_survive_native_contract(color):
    card = {"version": 1, "revision": 1, "root": {
        "tag": "div", "style": {"color": color, "backgroundColor": color,
                                "borderColor": color},
        "children": [{"tag": "p", "text": "安全颜色"}]}}
    assert validate_native_snapshot(card)["root"]["style"]["backgroundColor"] == color
    vector = {"version": 1, "revision": 1, "root": {
        "tag": "svg", "attrs": {"viewBox": "0 0 650 300"},
        "children": [{"tag": "path", "attrs": {
            "d": "M0 0 L20 50", "stroke": color}}]}}
    assert validate_native_snapshot(vector)["root"]["children"][0]["attrs"]["stroke"] == color


@pytest.mark.parametrize("unsafe", [
    "color(srgb 0 0 0);background:url(https://evil.invalid)",
    "oklab(var(--secret) 0 0)",
    "color(srgb 0 0 0 / url(#x))",
    "color(display-p3 1 1 1)",
    "oklab(1 0 0) url(https://evil.invalid)",
    "oklab(1 0 0 / 0.5)!important",
])
def test_modern_css_colors_are_numeric_and_reject_nested_or_active_values(unsafe):
    with pytest.raises(ValueError):
        validate_native_snapshot({"version": 1, "revision": 1,
            "root": {"tag": "div", "style": {"backgroundColor": unsafe}}})
    with pytest.raises(ValueError):
        validate_native_snapshot({"version": 1, "revision": 1,
            "root": {"tag": "svg", "attrs": {"fill": unsafe}}})


@pytest.mark.parametrize("padding,width", [
    ("8px 8px 8px 0px", "159.219px"),
    ("8px 0px 8px 8px", "385.609px"),
    ("8px", "229.531px"),
    ("0", "36.5px"),
])
def test_windows_linux_table_cell_padding_and_fractional_width_contract(padding, width):
    value = validate_native_snapshot({"version": 1, "revision": 1,
        "root": {"tag": "th", "style": {"padding": padding, "width": width},
                 "text": "表格完整保真"}})
    assert value["root"]["style"]["padding"] == padding
    assert value["root"]["style"]["width"] == width


@pytest.mark.parametrize("unsafe", [
    "8px 8px 8px 0px;position:absolute",
    "8px 8px 8px 0px 1px",
    "8px calc(10px)",
    "8px url(https://evil.invalid)",
    "-1px 0px",
    "8px 8px !important",
])
def test_table_padding_stays_strictly_length_only(unsafe):
    with pytest.raises(ValueError):
        validate_native_snapshot({"version": 1, "revision": 1,
            "root": {"tag": "td", "style": {"padding": unsafe}, "text": "安全"}})


def test_request_scoped_mathml_preserves_units_operators_and_scripts(tmp_path):
    from app.native_content_v1 import native_public_v1_tree
    formula = {"tag": "math", "style": {"display": "block math"},
        "children": [{"tag": "mrow", "children": [
            {"tag": "msub", "children": [
                {"tag": "mi", "text": "N"}, {"tag": "mtext", "text": "每炉"}]},
            {"tag": "mo", "text": "="},
            {"tag": "mover", "children": [
                {"tag": "mo", "text": "∑"}, {"tag": "mi", "text": "n"}]},
            {"tag": "mtext", "text": "万元"}]}]}
    value = {"version": 1, "revision": 1,
        "root": {"tag": "div", "children": [formula]}}
    assert validate_native_snapshot(value) == value
    store = NativeContentStore(tmp_path)
    assert store.publish("req_mathtestv26701", "owner", value)
    assert store.persist("req_mathtestv26701", "owner")
    assert NativeContentStore(tmp_path).get("req_mathtestv26701", "owner") == value
    # Old clients receive inert HTML-only span nodes, not unrecognized MathML.
    public = native_public_v1_tree(formula)
    assert public["tag"] == "span"
    assert public["children"][0]["children"][0]["tag"] == "span"
    assert "display" not in public.get("style", {})


@pytest.mark.parametrize("bad", [
    {"tag": "math", "children": [{"tag": "div", "text": "spoof"}]},
    {"tag": "mfrac", "children": [{"tag": "img", "media_index": 0}]},
    {"tag": "mtext", "style": {"backgroundImage": "url(x)"}},
    {"tag": "mtext", "onclick": "alert(1)"},
    {"tag": "math", "children": [{"tag": "script", "text": "bad"}]},
])
def test_request_scoped_mathml_fails_closed_on_unsafe_nodes(bad):
    with pytest.raises(ValueError):
        validate_native_snapshot({"version": 1, "revision": 1, "root": bad})


def test_sanitized_svg_chart_roundtrip_with_internal_clip(tmp_path):
    """True chart geometry/axis labels stay in the private validated SVG tree."""
    from app.native_content_v1 import native_public_v1_tree
    path = {"tag": "path", "attrs": {"d": "M0 220 L100 210 L200 185 L500 20",
                                    "fill": "none", "stroke": "#2288cc"}}
    chart = {"tag": "svg", "attrs": {"viewBox": "0 0 650 300",
                                    "width": "650", "height": "300"},
             "children": [
                 {"tag": "defs", "children": [
                     {"tag": "clippath", "attrs": {"id": "recharts-clip"},
                      "children": [{"tag": "rect", "attrs": {
                          "x": "0", "y": "0", "width": "600", "height": "280"}}]}]},
                 {"tag": "g", "attrs": {"clip-path": "url(#recharts-clip)"},
                  "children": [path]},
                 {"tag": "text", "text": "75万元",
                  "attrs": {"x": "24", "y": "38", "fill": "#333333",
                            "text-anchor": "end"}}]}
    data = {"version": 1, "revision": 1, "root": {"tag": "div", "children": [chart]}}
    assert validate_native_snapshot(data) == data
    store = NativeContentStore(tmp_path)
    assert store.publish("req_sanitizedsvg12345", "owner", data)
    assert store.persist("req_sanitizedsvg12345", "owner")
    assert NativeContentStore(tmp_path).get("req_sanitizedsvg12345", "owner") == data
    public = native_public_v1_tree(chart)
    assert public["tag"] == "span"
    assert "attrs" not in public
    assert public["children"][2]["text"] == "75万元"
    assert "attrs" not in public["children"][1]["children"][0]


@pytest.mark.parametrize("invalid", [
    {"tag": "svg", "attrs": {"href": "https://evil.example"}},
    {"tag": "svg", "attrs": {"onclick": "alert(1)"}},
    {"tag": "svg", "attrs": {"fill": "url(#external)"}},
    {"tag": "svg", "attrs": {"viewBox": "0 0 1 1;position:fixed"}},
    {"tag": "svg", "children": [{"tag": "foreignobject", "text": "script"}]},
    {"tag": "svg", "children": [{"tag": "script", "text": "alert(1)"}]},
    {"tag": "path", "attrs": {"d": "M0 0"}},
    {"tag": "svg", "children": [{"tag": "div", "text": "HTML inside SVG"}]},
    {"tag": "svg", "children": [{"tag": "path", "attrs": {
        "d": "M0 0 L10 10", "stroke": "url(https://evil.example)"}}]},
    {"tag": "svg", "children": [{"tag": "path", "attrs": {
        "d": "M0 0 L10 10", "clip-path": "url(https://evil.example)"}}]},
    {"tag": "svg", "children": [{"tag": "path", "attrs": {"d": "M0 0 L10 10",
        "onload": "alert(1)"}}]},
    {"tag": "svg", "children": [{"tag": "image", "attrs": {"href": "data:..."}}]},
    {"tag": "svg", "children": [{"tag": "circle", "text": "bad"}]},
])
def test_svg_rejects_active_or_nonlocal_markup(invalid):
    with pytest.raises(ValueError):
        validate_native_snapshot({"version": 1, "revision": 1, "root": invalid})


def test_image_free_native_reply_does_not_claim_extra_archive_identity():
    from app.native_content_v1 import native_layout_summary, native_image_identity_candidates
    value = validate_native_snapshot({"version": 1, "revision": 1,
        "root": {"tag": "div", "children": [{"tag": "table", "children": [
            {"tag": "tbody", "children": [{"tag": "tr", "children": [
                {"tag": "td", "text": "安全表格"}]}]}]}]}})
    facts = native_layout_summary(value, 1)
    assert facts["images"] == 0
    assert facts["archived_assets"] == 1
    assert facts["media_mapping_complete"] is False
    assert native_image_identity_candidates(value, [{"sha256": "a" * 64}])["status"] == "unavailable"


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
        {"tag": "div", "children": [
            {"tag": "div", "children": [{"tag": "p", "text": f"extra {g}-{part}-{i}"}
                for i in range(140)]} for part in range(2)]} for g in range(110)
    ])
    with pytest.raises(ValueError, match="exceeds limit|too large"):
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
    # 2a43: preserve the exact final tree beyond the former 24-level cap.
    for _ in range(21):
        root = {"tag": "div", "children": [root]}
    assert validate_native_snapshot({"version": 1, "revision": 12, "root": root})["root"] == root
    for _ in range(50):
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
    for group in range(120):
        larger.append({"tag":"div","children":[{"tag":"div","children":[{"tag":"p","text":"x"} for _ in range(140)]} for part in range(2)]})
    with pytest.raises(ValueError,match="exceeds limit"):
        validate_native_snapshot({"version":1,"revision":31,
                                  "root":{"tag":"div","children":larger}})


def test_sha256_partial_mapping_preserves_only_proven_image_positions():
    from app.native_content_v1 import native_image_identity_candidates

    proof_a = "a" * 64
    proof_b = "b" * 64
    proof_c = "c" * 64
    archive = [{"sha256": "f" * 64}, {"sha256": proof_b},
               {"sha256": proof_a}, {"sha256": "e" * 64}]
    value = validate_native_snapshot({"version": 1, "revision": 1,
        "root": {"tag": "div", "children": [
            {"tag": "img", "media_index": 0, "source_sha256": proof_a},
            {"tag": "p", "text": "表格与文字必须完整保留"},
            {"tag": "img", "media_index": 1},
            {"tag": "img", "media_index": 2, "source_sha256": proof_b},
            {"tag": "img", "media_index": 3, "source_sha256": proof_c},
        ]}})
    candidates = native_image_identity_candidates(value, archive)
    assert candidates == {
        "status": "verified-sha256-partial",
        "indices": [2, None, 1, None],
        "matched_count": 2,
    }
    # Two native slots share the SAME SHA256, but this request owns only
    # one matching archive asset and three different assets. Reusing archive
    # index 2 twice would falsely claim complete visual delivery (v308).
    duplicates = validate_native_snapshot({"version": 1, "revision": 1,
        "root": {"tag": "div", "children": [
            {"tag": "img", "media_index": 0, "source_sha256": proof_a},
            {"tag": "img", "media_index": 1, "source_sha256": proof_a},
        ]}})
    assert native_image_identity_candidates(duplicates, archive) == {
        "status": "verified-sha256-partial",
        "indices": [2, None], "matched_count": 1}
    # Two archives with genuinely identical original bytes can be assigned
    # to two positions without ordinal guesses about DIFFERENT images.
    identical = [{"sha256": proof_a}, {"sha256": proof_a}]
    assert native_image_identity_candidates(duplicates, identical) == {
        "status": "verified-sha256", "indices": [0, 1]}
    # Byte-identical copies and different SHA resources must be mapped in
    # exact DOM source identity order rather than archive insertion order.
    mixed = validate_native_snapshot({"version": 1, "revision": 1,
        "root": {"tag": "div", "children": [
            {"tag": "img", "media_index": 0, "source_sha256": proof_a},
            {"tag": "img", "media_index": 1, "source_sha256": proof_b},
            {"tag": "img", "media_index": 2, "source_sha256": proof_a},
        ]}})
    assert native_image_identity_candidates(mixed, [
        {"sha256": proof_b}, {"sha256": proof_a}, {"sha256": proof_a}
    ]) == {"status": "verified-sha256", "indices": [1, 0, 2]}
    # A duplicate match with a missing second distinct archive must NEVER
    # report all slots covered, even when the raw counts happen to equal.
    incorrect = native_image_identity_candidates(duplicates, [
        {"sha256": proof_a}, {"sha256": proof_b}])
    assert incorrect == {"status": "verified-sha256-partial",
                         "indices": [0, None], "matched_count": 1}
    # A completed subset of image slots cannot prove a surplus archived
    # image belongs somewhere; every owned archive requires a slot.
    subset = validate_native_snapshot({"version": 1, "revision": 1,
        "root": {"tag": "img", "media_index": 0, "source_sha256": proof_a}})
    assert native_image_identity_candidates(subset, identical)["status"] == (
        "verified-sha256-partial")
    unknown = validate_native_snapshot({"version": 1, "revision": 1,
        "root": {"tag": "div", "children": [
            {"tag": "img", "media_index": 0, "source_sha256": proof_c},
            {"tag": "img", "media_index": 1},
        ]}})
    assert native_image_identity_candidates(unknown, archive) == {
        "status": "identity-incomplete", "indices": []}
    # No proof: dimension matching remains diagnostic and cannot be presented
    # as a byte-verified image identity.
    only_size = validate_native_snapshot({"version": 1, "revision": 1,
        "root": {"tag": "div", "children": [
            {"tag": "img", "media_index": 0,
             "intrinsic_width": 640, "intrinsic_height": 480}]}})
    assert native_image_identity_candidates(only_size,
        [{"width": 640, "height": 480}])["status"] == "unique-dimensions-unverified"


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
        [{"tag": "p", "text": "additional section"} for _ in range(50)]
    )
    assert len(validate_native_snapshot(payload)["root"]["children"][0]["children"]) == 161
    # Genuine overflow remains hard-rejected, not silently cut off.
    payload["root"]["children"][0]["children"].extend(
        [{"tag": "p", "text": "overflow"} for _ in range(8193 - 161)]
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


def test_intrinsic_image_metadata_is_rejected_on_non_image_nodes():
    with pytest.raises(ValueError, match="Image reference"):
        validate_native_snapshot({"version":1,"revision":1,
                                  "root":{"tag":"div","intrinsic_width":900,
                                          "intrinsic_height":900}})


def test_17b4_more_than_3200_nodes_preserves_entire_bounded_native_tree():
    from app.native_content_v1 import validate_native_snapshot, native_layout_summary
    children=[{"tag":"div","children":[{"tag":"span","text":f"plant section {group} item {i}"}
             for i in range(80)]} for group in range(46)]
    snapshot=validate_native_snapshot({"version":1,"revision":5,
                                        "root":{"tag":"div","children":children}})
    summary=native_layout_summary(snapshot, archived_assets=0)
    assert summary["nodes"] > 3200
    assert summary["nodes"] < 6400
    assert len(snapshot["root"]["children"]) == 46
    assert snapshot["root"]["children"][-1]["children"][-1]["text"].startswith("plant section 45")


def test_3a882b_final_large_dom_fanout_keeps_order_without_synthetic_wrappers():
    from app.native_content_v1 import native_layout_summary
    # Multiple tables and raster cards under one original ChatGPT layout.
    children = [{"tag": "table", "children": [
        {"tag": "tbody", "children": [{"tag": "tr", "children": [
            {"tag": "td", "text": f"table {i}"}]}]}
    ]} for i in range(27)]
    children.extend({"tag": "img", "media_index": i} for i in range(9))
    children.extend({"tag": "p", "text": f"section {i}"} for i in range(1800))
    payload = {"version": 1, "revision": 13,
        "root": {"tag": "div", "style": {"display": "flex",
                   "flexDirection": "column"}, "children": children}}
    accepted = validate_native_snapshot(payload)
    assert accepted == payload
    assert len(accepted["root"]["children"]) == 1836
    assert accepted["root"]["children"][-1]["text"] == "section 1799"
    counts = native_layout_summary(accepted, 9)
    assert (counts["tables"], counts["images"], counts["media_mapping_complete"]) == (27, 9, True)
    # Still refuse oversized trees instead of silently trimming trailing rows.
    payload["root"]["children"].extend({"tag": "p", "text": "x"} for _ in range(8192 - 1836 + 1))
    with pytest.raises(ValueError, match="Invalid children"):
        validate_native_snapshot(payload)


def test_3a882b_terminal_diagnostics_never_equate_prior_revision_with_final():
    from app.broker import _terminal_provenance
    observed = _terminal_provenance({"diagnostics": {
        "native_capture_revision": 12,
        "native_final_capture_committed": False,
        "native_capture_failure": "structural-max-children",
        "native_structural_max_children": 1800,
    }})
    assert observed["native_capture_revision"] == 12
    assert observed["native_final_capture_committed"] is False
    assert observed["native_structural_max_children"] == 1800
    assert observed["native_capture_failure"] == "structural-max-children"
