"""R-07: fake GitHub API proves fail-closed permission probe lifecycle."""
from __future__ import annotations

import io
import urllib.error

import pytest

from scripts.gptauto_workflow_write_probe_r07 import GitHubApi, ProbeError, main, run_probe

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



def test_http_permission_denial_never_exposes_token_or_response_body(monkeypatch) -> None:
    token = "gptauto-secret-token-must-not-appear"
    body = ("private response body " + token).encode("utf-8")

    def denied(_request, timeout):
        assert timeout == 15
        raise urllib.error.HTTPError(
            "https://api.github.com/private", 403, "forbidden",
            hdrs=None, fp=io.BytesIO(body),
        )

    monkeypatch.setattr(
        "scripts.gptauto_workflow_write_probe_r07.urllib.request.urlopen", denied,
    )
    with pytest.raises(ProbeError) as error:
        GitHubApi(token).request("PUT", PREFIX + "/contents/.github/workflows/probe.yml", {})
    assert "403" in str(error.value)
    assert token not in str(error.value)
    assert "private response body" not in str(error.value)


def test_network_timeout_is_reported_without_raw_transport_exception(monkeypatch) -> None:
    def timed_out(_request, timeout):
        raise TimeoutError("socket timed out with secret header")

    monkeypatch.setattr(
        "scripts.gptauto_workflow_write_probe_r07.urllib.request.urlopen", timed_out,
    )
    with pytest.raises(ProbeError, match="failed to reach API") as error:
        GitHubApi("token").request("GET", PREFIX + "/git/ref/heads/main")
    assert "secret header" not in str(error.value)



def test_ambiguous_branch_create_response_reconciles_and_cleans_created_ref() -> None:
    api = FakeApi(fail_at="POST")
    with pytest.raises(ProbeError, match="simulated POST denial"):
        run_probe(api, NONCE)
    assert [method for method, _, _ in api.calls] == ["GET", "POST", "GET", "DELETE"]
    assert api.calls[2][1].endswith("/git/ref/heads/diagnostics/gptauto-workflow-write-probe-" + NONCE)
    assert api.calls[3][1].endswith("/git/refs/heads/diagnostics/gptauto-workflow-write-probe-" + NONCE)


def test_rejected_branch_create_with_confirmed_404_does_not_delete_unknown_ref() -> None:
    class NoBranchApi(FakeApi):
        def request(self, method, path, payload=None):
            if method == "POST":
                self.calls.append((method, path, payload))
                raise ProbeError("simulated Contents write denial", status_code=403)
            if method == "GET" and "/git/ref/heads/diagnostics/" in path:
                self.calls.append((method, path, payload))
                raise ProbeError("GitHub GET rejected with HTTP 404", status_code=404)
            return super().request(method, path, payload)

    api = NoBranchApi()
    with pytest.raises(ProbeError, match="simulated Contents write denial"):
        run_probe(api, NONCE)
    assert [method for method, _, _ in api.calls] == ["GET", "POST", "GET"]
