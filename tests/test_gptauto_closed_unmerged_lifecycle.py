from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github"))

from gptauto.executor import next_action
from gptauto.model import Task
from gptauto.observer import capture


def observe(tmp_path: Path, *, pr_state: str, pr_merged: str = "false", pr_draft: str = "true"):
    result = capture(
        repo="b8vipvip/chat2api",
        event="pull_request",
        default_branch="main",
        branch="feature/example",
        head_sha="a" * 40,
        current_pr_head_sha="a" * 40,
        pr_number="813",
        pr_title="W-03 example",
        pr_state=pr_state,
        pr_merged=pr_merged,
        pr_draft=pr_draft,
        log_root=str(tmp_path),
    )
    task = Task.load(result["paths"]["state"])
    return result, task


def test_closed_unmerged_draft_retires_pr_task_without_claiming_done(tmp_path: Path):
    result, task = observe(tmp_path, pr_state="closed", pr_merged="false", pr_draft="true")

    assert result["phase"] == "CLOSED_UNMERGED"
    assert task.metadata["pr_state"] == "closed"
    assert task.metadata["pr_merged"] is False
    assert task.metadata["terminal_done"] is False

    action = next_action(task)
    lease = action["completion_lease"]
    host = lease["host_control"]
    assert action["action"] == "closed_unmerged"
    assert lease["active"] is False
    assert lease["foreground_completion_status"] == "closed_unmerged"
    assert lease["allow_foreground_exit"] is True
    assert host["task_alive"] is False
    assert host["terminal_done"] is False
    assert host["foreground_disposition"] == "PR_CLOSED_UNMERGED"
    assert host["next_host_action"] == "STOP_THIS_PR_TASK"


def test_open_draft_still_requires_human_review(tmp_path: Path):
    result, task = observe(tmp_path, pr_state="open", pr_merged="false", pr_draft="true")

    assert result["phase"] == "USER_ACTION_REQUIRED"
    assert result["blocked_reason"] == "pr_draft_waiting_for_review"
    action = next_action(task)
    assert action["action"] == "user_action_required"
    assert action["completion_lease"]["active"] is True


def test_closed_merged_pr_continues_post_merge_verification(tmp_path: Path):
    result, task = observe(tmp_path, pr_state="closed", pr_merged="true", pr_draft="false")

    assert result["phase"] == "POST_MERGE_CI"
    action = next_action(task)
    assert action["action"] == "verify"
    assert action["completion_lease"]["active"] is True
    assert action["completion_lease"]["host_control"]["terminal_done"] is False
