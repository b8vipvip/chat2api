"""R-07: fake GitHub API proves fail-closed permission probe lifecycle."""
from __future__ import annotations

import pytest

from scripts.gptauto_workflow_write_probe_r07 import ProbeError, main, run_probe

NONCE = "abcdef123456"
PREFIX = "/repos/b8vipvip/chat2api"


class FakeApi:
    def __init__(self, fail_at: str | None = None) -> None:
        self.calls = []
        self.fail_at = fail_at

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == self.fail_at:
            raise ProbeError(f"simulated {method} denial")
        if method == "GET":
            return {"object": {"sha": "a" * 40}}
        if method == "PUT":
            return {"commit": {"sha": "b" * 40}}
        return {}


def test_disposable_workflow_write_is_removed_without_touching_main() -> None:
    api = FakeApi()
    result = run_probe(api, NONCE)
    methods = [item[0] for item in api.calls]
    assert methods == ["GET", "POST", "PUT", "DELETE"]
    assert api.calls[0][1] == PREFIX + "/git/ref/heads/main"
    assert api.calls[1][2]["ref"] == "refs/heads/" + result["branch"]
    assert api.calls[2][2]["branch"] == result["branch"]
    assert api.calls[2][1].startswith(PREFIX + "/contents/.github/workflows/")
    assert api.calls[3][1] == PREFIX + "/git/refs/heads/" + result["branch"]
    assert result["result"] == "workflows-write-verified-and-cleaned"
    assert not any(method == "PATCH" or "main" in path and method != "GET"
                   for method, path, _payload in api.calls)


def test_denied_workflows_write_still_deletes_probe_branch() -> None:
    api = FakeApi(fail_at="PUT")
    with pytest.raises(ProbeError, match="simulated PUT denial"):
        run_probe(api, NONCE)
    assert [method for method, _, _ in api.calls] == ["GET", "POST", "PUT", "DELETE"]


def test_failed_cleanup_reports_exact_manual_recovery_branch() -> None:
    api = FakeApi(fail_at="DELETE")
    with pytest.raises(ProbeError, match="delete it manually"):
        run_probe(api, NONCE)
    assert [method for method, _, _ in api.calls] == ["GET", "POST", "PUT", "DELETE"]


def test_no_execute_is_real_dry_run_without_reading_token(monkeypatch, capsys) -> None:
    # Absent --execute, no credential or network path should be instantiated.
    monkeypatch.delenv("GPTAUTO_GITHUB_TOKEN", raising=False)
    assert main([]) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert main(["--execute"]) == 2


def test_invalid_nonce_fails_before_any_github_calls() -> None:
    api = FakeApi()
    with pytest.raises(ValueError, match="nonce"):
        run_probe(api, "unsafe/branch")
    assert api.calls == []
