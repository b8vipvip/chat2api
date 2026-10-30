from app.worker_probe_store import _sanitize_facts

TOPOLOGY = (
    "scene_roleless_fresh_assistant_heading_count",
    "scene_uncovered_connected_count",
    "scene_uncovered_after_assistant_heading_count",
    "scene_uncovered_before_last_toolbar_count",
    "scene_uncovered_between_heading_toolbar_count",
    "scene_uncovered_after_last_toolbar_count",
    "scene_uncovered_shared_bounded_ancestor_count",
    "scene_uncovered_outside_candidate_ancestry_count",
)


def test_v253_topology_counters_survive_server_sanitization():
    incoming = {key: index + 1 for index, key in enumerate(TOPOLOGY)}
    result = _sanitize_facts(incoming)
    assert result == incoming


def test_v253_topology_cannot_leak_html_or_private_payloads():
    incoming = {key: "<div>private prompt</div>" for key in TOPOLOGY}
    incoming["scene_reply_topology"] = "<script>private</script>"
    incoming["untrusted_field"] = "private"
    assert _sanitize_facts(incoming) == {}


def test_legacy_probe_schema_and_numeric_safety_unaffected():
    result = _sanitize_facts({
        "scene_uncovered_block_count": 255,
        "scene_uncovered_between_heading_toolbar_count": 255,
        "scene_uncovered_outside_candidate_ancestry_count": 255,
        "scene_uncovered_connected_count": 2_000_000_000,
    })
    assert result == {
        "scene_uncovered_block_count": 255,
        "scene_uncovered_between_heading_toolbar_count": 255,
        "scene_uncovered_outside_candidate_ancestry_count": 255,
    }
