"""V5 bank starts fresh without deleting/redoing previous issued prompts."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from app.playground_abcd_pool_v198 import OneShotPool
from app.playground_abcd_v5_cases import (
    _TOPICS, V5_VERSION, build_v5_case,
)
from app.playground_abcd_v4_cases import TITLES as OLD_V4_TITLES


def test_new_v5_bank_has_64_distinct_predefined_real_tasks():
    titles = set()
    prompts = set()
    for scene in "ABCD":
        assert len(_TOPICS[scene]) >= 16
        for i in range(len(_TOPICS[scene])):
            case = build_v5_case(scene, i)
            assert case["case_version"] == "v5"
            assert case["pool_version"] == V5_VERSION
            assert case["kind"] in {"rich_text", "three_images", "four_files", "web_app"}
            assert case["title"] not in titles
            assert case["title"] not in OLD_V4_TITLES.values()
            assert case["prompts"][0] not in prompts
            assert len(case["prompts"]) == 1
            assert len(case["prompts"][0]) > 160
            titles.add(case["title"])
            prompts.add(case["prompts"][0])
        assert build_v5_case(scene, len(_TOPICS[scene]))["prompts"][0] not in prompts
    assert len(titles) >= 64


def test_v5_is_a_new_cursor_even_after_prior_v4_and_legacy_consumption(tmp_path):
    pool = OneShotPool(tmp_path)
    with pool.connection() as db:
        db.execute("INSERT INTO abcd_counter(scene,serial) VALUES ('A',37)")
        db.execute("INSERT INTO abcd_v4_consumed(scene,claim_id) VALUES ('A','old')")
        old = {"scene": "A", "title": "old-retained-audit", "serial": 34, "prompts": ["old"]}
        db.execute("INSERT INTO abcd_claim(claim_id,scene,case_json,claimed_at) "
                   "VALUES (?,?,?,?)", ("old_claim", "A", json.dumps(old), "2026-10-01"))
        db.commit()

    assert pool.preview("A")["title"] == _TOPICS["A"][0][0]
    assert pool.preview("A")["serial"] == 0
    first = pool.claim("A", "new_v5_first_claim_0000000001")
    assert first["case_version"] == "v5"
    assert first["title"] == _TOPICS["A"][0][0]
    assert pool.preview("A")["title"] == _TOPICS["A"][1][0]
    assert OneShotPool(tmp_path).claim("A", "new_v5_first_claim_0000000001") == first
    assert OneShotPool(tmp_path).claim("A", "old_claim")["title"] == "old-retained-audit"
    with pool.connection() as db:
        assert db.execute("SELECT serial FROM abcd_counter WHERE scene='A'").fetchone() == (38,)
        assert db.execute("SELECT start_serial FROM abcd_v5_start WHERE scene='A'").fetchone() == (37,)
        assert db.execute("SELECT claim_id FROM abcd_v4_consumed WHERE scene='A'").fetchone() == ("old",)


def test_concurrent_new_v5_claims_do_not_duplicate_a_scene(tmp_path):
    def claim(n):
        return OneShotPool(tmp_path).claim("C", f"v5_new_claim_{n:020d}")

    with ThreadPoolExecutor(max_workers=5) as pool:
        cases = list(pool.map(claim, range(12)))
    assert {x["serial"] for x in cases} == set(range(12))
    assert len({x["title"] for x in cases}) == 12
    assert OneShotPool(tmp_path).preview("C")["serial"] == 12
