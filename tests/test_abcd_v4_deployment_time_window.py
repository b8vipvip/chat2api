import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.playground_abcd_pool_v198 import OneShotPool
from app.telemetry import TelemetryStore, current_deployment_start


def test_new_v4_case_is_next_claim_even_after_previous_rotation(tmp_path):
    pool = OneShotPool(tmp_path)
    with pool.connection() as db:
        db.execute("INSERT INTO abcd_counter(scene,serial) VALUES ('A',35)")
        db.commit()
    preview = pool.preview("A")
    assert "旧住宅" in preview["title"]
    first = pool.claim("A", "abcd_test_unique_id_20261030")
    assert first["case_version"] == "v4"
    assert "96平方米" in first["prompts"][0]
    assert pool.claim("A", "abcd_test_unique_id_20261030")["prompts"] == first["prompts"]
    assert pool.preview("A")["title"] != preview["title"]
    assert OneShotPool(tmp_path).preview("A")["title"] != preview["title"]


def test_all_four_v4_real_scenes_are_distinct(tmp_path):
    pool = OneShotPool(tmp_path)
    titles = []
    for scene in "ABCD":
        item = pool.claim(scene, "abcd_distinct_scene_" + scene + "_test")
        titles.append(item["title"])
        assert len(item["prompts"]) == 1
        assert not any(term in item["prompts"][0] for term in ("接口测试", "Worker探针"))
    assert len(set(titles)) == 4


def test_request_history_current_commit_filters_same_sha_older_deployment(tmp_path, monkeypatch):
    sha = "a" * 40
    now = datetime.now(timezone.utc)
    activated = now - timedelta(minutes=30)
    (tmp_path / "admin-update-status.json").write_text(json.dumps({
        "status": "succeeded",
        "deployed_commit": sha,
        "completed_at": activated.isoformat(),
    }), encoding="utf-8")
    assert current_deployment_start(tmp_path, sha) == activated
    store = TelemetryStore(tmp_path)
    for name, at in (
        ("older", activated - timedelta(minutes=20)),
        ("current", activated + timedelta(minutes=5)),
        ("future", now + timedelta(hours=1)),
    ):
        asyncio.run(store.upsert({
            "request_id": name, "status": "completed", "recorded_at": at.isoformat(),
        }))
        # Simulate a previous run using exactly the same SHA, not a different version.
        row = store.items[-1]
        row["request_server_commit"] = sha
    selected = store.query(current_version_only=True,server_commit=sha)
    assert [row["request_id"] for row in selected["data"]] == ["current"]
    selected = store.query(current_version_only=True,server_commit=sha,
                           since=now-timedelta(minutes=20))
    assert selected["data"] == []
