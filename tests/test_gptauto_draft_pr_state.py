"""GPTAuto must treat a Draft PR as a review gate, not a failed merge."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github"))

from gptauto.executor import next_action
from gptauto.model import Task
from gptauto.observer import capture


HEAD = "a" * 40


def _observe(log_root: Path, *, draft: bool, ci_conclusion: str = "success") -> dict:
    return capture(
        repo="b8vipvip/chat2api",
        event="pull_request",
        default_branch="main",
        branch="feature/draft-pr-test",
        head_sha=HEAD,
        pr_number="824",
        pr_title="Draft PR lifecycle regression",
        pr_state="open",
        pr_draft="true" if draft else "false",
        pr_merged="false",
        current_pr_head_sha=HEAD,
        pr_ci_status="completed",
        pr_ci_conclusion=ci_conclusion,
        pr_ci_run_id="123456",
        log_root=log_root,
    )


def test_passing_draft_pr_becomes_user_action_required_not_merge_or_repair(tmp_path: Path):
    observed = _observe(tmp_path, draft=True)
    task = Task.load(observed["paths"]["state"])
    action = next_action(task)

    assert observed["phase"] == "USER_ACTION_REQUIRED"
    assert observed["state"] == "BLOCKED"
    assert observed["blocked_reason"] == "pr_draft_waiting_for_review"
    assert action["action"] == "user_action_required"
    assert action["blocked_reason"] == "pr_draft_waiting_for_review"
    assert "Draft" in action["reason"]
    assert action["completion_lease"]["active"] is True
    assert action["completion_lease"]["host_control"]["foreground_disposition"] == "USER_ACTION_REQUIRED"
    assert task.repair_attempts == 0
    assert task.metadata["generation"] == 0


def test_ready_for_review_resumes_same_task_and_allows_merge_gate(tmp_path: Path):
    draft = _observe(tmp_path, draft=True)
    ready = _observe(tmp_path, draft=False)
    task = Task.load(ready["paths"]["state"])
    action = next_action(task)

    assert draft["task_id"] == ready["task_id"]
    assert draft["dispatch_key"] != ready["dispatch_key"]
    assert ready["dispatch_required"] is True
    assert ready["phase"] == "MERGE"
    assert ready["blocked_reason"] == ""
    assert task.metadata["pr_draft"] is False
    assert action["action"] == "merge"
    assert action["head_sha"] == HEAD


def test_draft_does_not_mask_a_real_current_head_ci_failure(tmp_path: Path):
    observed = _observe(tmp_path, draft=True, ci_conclusion="failure")
    task = Task.load(observed["paths"]["state"])
    action = next_action(task)

    assert observed["phase"] == "REPAIR_REQUIRED"
    assert action["action"] == "repair_request"
    assert action["run_id"] == ""
    assert action["head_sha"] == HEAD
    assert action["failed_gates"] == ["PR_CI"]


def test_gptauto_workflows_wake_on_review_transition_and_never_merge_draft():
    observer = (ROOT / ".github" / "workflows" / "gptauto-observer.yml").read_text(encoding="utf-8")
    executor = (ROOT / ".github" / "workflows" / "gptauto-executor.yml").read_text(encoding="utf-8")

    assert "ready_for_review" in observer
    assert "converted_to_draft" in observer
    assert '--pr-draft "$PR_DRAFT"' in observer
    assert "PR_DRAFT=\"$(jq -r '.draft // false'" in observer
    assert 'if [[ "$(jq -r \' .draft' not in executor  # Guard against accidental template corruption.
    assert 'PR #$PR_NUMBER is still Draft; merge deferred until ready_for_review' in executor
    assert 'BLOCKED_REASON: ${{ steps.state.outputs.blocked-reason }}' in executor
    assert "pr_draft_waiting_for_review" in executor
