from __future__ import annotations

import asyncio
from pathlib import Path

from app.playground_truth_v170 import reconcile_playground_runs
from app.test_runs import TestRunStore


class FakeTelemetry:
    def __init__(self, rows):
        self.rows = rows

    def get(self, request_id):
        return self.rows.get(request_id)


def _run(run_id: str, request_id: str, result_status: str) -> dict:
    return {
        "run_id": run_id,
        "request_id": request_id,
        "request_ids": [request_id],
        "test_type": "voice_generation",
        "status": "running",
        "quality": {"lifecycle": "playground-lifecycle-v1"},
        "results": [{
            "kind": "voice_generation",
            "status": result_status,
            "request_id": request_id,
            "message": "executor still processing response",
        }],
    }


def test_backend_terminal_does_not_finalize_a_live_playground_executor(tmp_path: Path) -> None:
    async def scenario():
        store = TestRunStore(tmp_path)
        run_id, request_id = "testrun_live_executor", "voicereq_live_executor"
        await store.upsert(_run(run_id, request_id, "running"))
        telemetry = FakeTelemetry({request_id: {
            "request_id": request_id,
            "status": "completed",
            "completion_chars": 12,
            "response_media_count": 1,
        }})

        await reconcile_playground_runs(
            store, telemetry, active_run_ids={run_id},
        )
        current = store.get(run_id)
        assert current["status"] == "running"
        # Backend truth is visible as evidence, but the case itself remains
        # running until the executor persists effective_model, audio bytes,
        # artifact metadata and quality fields.
        assert current["results"][0]["status"] == "running"
        assert current["results"][0]["authoritative_request_status"] == "completed"
        assert "等待 Playground 执行器" in current["results"][0]["message"]

    asyncio.run(scenario())


def test_backend_success_never_overwrites_a_local_executor_failure(tmp_path: Path) -> None:
    async def scenario():
        store = TestRunStore(tmp_path)
        run_id, request_id = "testrun_local_failure", "voicereq_local_failure"
        await store.upsert(_run(run_id, request_id, "failed"))
        telemetry = FakeTelemetry({request_id: {
            "request_id": request_id,
            "status": "completed",
            "completion_chars": 12,
        }})

        await reconcile_playground_runs(store, telemetry)
        current = store.get(run_id)
        assert current["results"][0]["status"] == "failed"
        assert current["status"] == "failed"

    asyncio.run(scenario())
