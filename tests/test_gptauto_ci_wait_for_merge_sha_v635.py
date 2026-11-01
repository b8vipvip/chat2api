"""Regression for PR-merge CI timing and exact-SHA hotfix safety."""
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "gptauto"))
import runtime_hotfix as hotfix


def _run(name, sha, event, status, conclusion):
    return {"name": name, "head_sha": sha, "event": event,
            "status": status, "conclusion": conclusion}


def test_exact_sha_ci_rejects_pr_success_and_pending_merge(monkeypatch):
    tested_sha = "a" * 40
    merged_sha = "b" * 40
    payload = {"workflow_runs": [
        _run("CI", tested_sha, "pull_request", "completed", "success"),
        _run("CI", merged_sha, "push", "in_progress", None),
    ]}
    observed_urls = []

    def urlopen(req, timeout=18):
        observed_urls.append(req.full_url)
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(hotfix.urllib.request, "urlopen", urlopen)
    assert not hotfix.github_ci_succeeded("chat2api", merged_sha, ("CI",))
    assert observed_urls == [
        "https://api.github.com/repos/b8vipvip/chat2api/actions/runs"
        "?head_sha=" + merged_sha + "&per_page=100"
    ]
    payload["workflow_runs"].append(
        _run("CI", merged_sha, "push", "completed", "success")
    )
    assert hotfix.github_ci_succeeded("chat2api", merged_sha, ("CI",))
    assert not hotfix.github_ci_succeeded(
        "chat2api", merged_sha, ("CI", "Production image smoke")
    )


def test_exact_sha_ci_wait_handles_pending_until_success(monkeypatch, capsys):
    sha = "c" * 40
    calls = []
    clock = [0.0]

    def probe(repo, value, checks):
        calls.append((repo, value, checks))
        return len(calls) >= 3

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(hotfix, "github_ci_succeeded", probe)
    monkeypatch.setattr(hotfix.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(hotfix.time, "sleep", sleep)
    assert hotfix.wait_for_github_ci(
        "chat2api", sha, ("CI",), max_wait_seconds=120, poll_seconds=20
    )
    assert clock[0] == 40
    assert len(calls) == 3
    assert all(call == ("chat2api", sha, ("CI",)) for call in calls)
    assert "exact-SHA CI not yet verified" in capsys.readouterr().err


def test_exact_sha_ci_wait_times_out_without_deploy(monkeypatch):
    sha = "d" * 40
    clock = [0.0]
    calls = []

    def no_success(repo, value, checks):
        calls.append((repo, value, checks))
        return False

    monkeypatch.setattr(hotfix, "github_ci_succeeded", no_success)
    monkeypatch.setattr(hotfix.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(hotfix.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    assert not hotfix.wait_for_github_ci(
        "chat2api", sha, ("CI",), max_wait_seconds=45, poll_seconds=20
    )
    assert clock[0] == 45
    assert len(calls) == 4
    assert all(call[1] == sha for call in calls)


def test_ci_wait_rejects_invalid_identity_or_unbounded_poll(monkeypatch):
    monkeypatch.setattr(hotfix, "github_ci_succeeded",
                        lambda *args: (_ for _ in ()).throw(AssertionError("unexpected CI call")))
    assert not hotfix.wait_for_github_ci("chat2api", "not-a-sha", ("CI",))
    assert not hotfix.wait_for_github_ci("unknown", "e" * 40, ("CI",))
    assert not hotfix.wait_for_github_ci("chat2api", "e" * 40, ())
    import pytest
    with pytest.raises(ValueError, match="budget"):
        hotfix.wait_for_github_ci("chat2api", "e" * 40, ("CI",),
                                 max_wait_seconds=10000)
