from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.playground_abcd_pool_v198 import OneShotPool, build_case, A_TOPICS, B_TOPICS, C_TOPICS, D_TOPICS


def test_first_12_tasks_have_different_real_world_topics():
    for scene, topics in (("A", A_TOPICS), ("B", B_TOPICS), ("C", C_TOPICS), ("D", D_TOPICS)):
        assert len(topics) >= 10
        items = [build_case(scene, i) for i in range(len(topics))]
        titles = [x["title"] for x in items]
        assert len(set(titles)) == len(topics)
        assert len(set(x["prompts"][0] for x in items)) == len(topics)
        assert all("PG-" not in x["prompts"][0] and "测试标识" not in x["prompts"][0] for x in items)
        assert len(build_case(scene, len(topics))["prompts"][0]) > 100


def test_claim_consumes_even_if_no_response_and_stays_after_restart():
    with TemporaryDirectory() as tmp:
        folder = Path(tmp)
        store = OneShotPool(folder)
        a = store.preview("A")
        claimed = store.claim("A", "claim_a_00000000000000000001")
        assert claimed["title"] == a["title"]
        assert store.preview("A")["title"] != claimed["title"]
        assert store.preview("B")["serial"] == 0
        another_store = OneShotPool(folder)
        assert another_store.preview("A")["serial"] == 1
        assert another_store.claim("A", "claim_a_00000000000000000001")["serial"] == 0
        assert another_store.preview("A")["serial"] == 1
        assert another_store.claim("A", "claim_a_00000000000000000002")["serial"] == 1
        assert another_store.preview("A")["serial"] == 2


def test_atomic_claim_in_multiple_process_style_connections():
    with TemporaryDirectory() as tmp:
        folder = Path(tmp)
        def run(index):
            return OneShotPool(folder).claim("C", f"claim_c_{index:024d}")["serial"]
        with ThreadPoolExecutor(max_workers=6) as executor:
            serials = list(executor.map(run, range(12)))
        assert sorted(serials) == list(range(12))
        assert OneShotPool(folder).preview("C")["serial"] == 12


def test_claim_id_cannot_cross_scene():
    with TemporaryDirectory() as tmp:
        store=OneShotPool(Path(tmp))
        store.claim("A","claim_valid_01234567890")
        with pytest.raises(ValueError,match="another scene"):
            store.claim("B","claim_valid_01234567890")
        assert store.preview("B")["serial"]==0
