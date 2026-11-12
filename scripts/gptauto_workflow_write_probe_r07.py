#!/usr/bin/env python3
"""R-07: explicitly verify GPTAuto token permission to write GitHub workflows.

Default mode is read-only: it does not access a token or call GitHub. In
--execute mode, create a unique throwaway branch from main, write ONE harmless
workflow YAML *only on that branch* using the configured GPTAuto token, and
delete the branch in a finally block. No push/merge/release of main occurs.

This is a permission probe, not a shortcut around PR/CI or GPTAuto's own gate.
The API token stays in memory and is never printed, persisted or placed in a
command argument. A failed branch cleanup is reported with the exact ref.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any

EXPECTED_REPO = "b8vipvip/chat2api"


class ProbeError(RuntimeError):
    pass


class GitHubApi:
    def __init__(self, token: str) -> None:
        self._token = token

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            "https://api.github.com" + path,
            method=method,
            data=data,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + self._token,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "chat2api-r07-workflow-write-probe",
                **({"Content-Type": "application/json"} if payload is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                raw = response.read(2_000_000)
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            # Never print token values, request headers, response bodies or
            # credentials embedded in providers' generic exception messages.
            raise ProbeError(f"GitHub {method} rejected with HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise ProbeError(f"GitHub {method} failed to reach API") from None


def run_probe(api: Any, nonce: str) -> dict[str, str]:
    """Write and clean up a disposable workflow, using the injected API."""
    if not re.fullmatch(r"[a-f0-9]{12}", nonce):
        raise ValueError("nonce must be 12 lowercase hexadecimal characters")
    root = f"/repos/{EXPECTED_REPO}"
    branch = f"diagnostics/gptauto-workflow-write-probe-{nonce}"
    branch_ref = f"refs/heads/{branch}"
    workflow_path = f".github/workflows/gptauto-permission-probe-{nonce}.yml"
    upstream = api.request("GET", root + "/git/ref/heads/main")
    base_sha = upstream.get("object", {}).get("sha") if isinstance(upstream, dict) else None
    if not isinstance(base_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", base_sha):
        raise ProbeError("Cannot resolve immutable main commit")
    api.request("POST", root + "/git/refs", {"ref": branch_ref, "sha": base_sha})
    workflow = (
        "name: GPTAuto Workflows permission probe\n"
        "on:\n  workflow_dispatch:\n"
        "permissions:\n  contents: read\n"
        "jobs:\n  noop:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - run: echo 'Probe branch only; do not deploy'\n"
    )
    did_write = False
    try:
        result = api.request("PUT", root + "/contents/" + workflow_path, {
            "message": "test: verify GPTAuto token Workflows write on disposable branch",
            "content": base64.b64encode(workflow.encode("utf-8")).decode("ascii"),
            "branch": branch,
        })
        committed_sha = result.get("commit", {}).get("sha") if isinstance(result, dict) else None
        if not isinstance(committed_sha, str) or not committed_sha:
            raise ProbeError("GitHub did not return a workflow write commit")
        did_write = True
    finally:
        try:
            api.request("DELETE", root + "/git/refs/heads/" + branch)
        except Exception as exc:
            # A failed cleanup is actionable and must not be silently ignored,
            # even if the write itself succeeded.
            raise ProbeError(
                f"Temporary branch cleanup failed: {branch_ref}; "
                "delete it manually before rerunning"
            ) from exc
    if not did_write:
        raise ProbeError("Workflow write was not confirmed")
    return {"branch": branch, "result": "workflows-write-verified-and-cleaned"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="Explicitly authorize a disposable workflow write and cleanup")
    parser.add_argument("--repo", default=EXPECTED_REPO)
    args = parser.parse_args(argv)
    if args.repo != EXPECTED_REPO:
        parser.error("This probe is scoped only to b8vipvip/chat2api")
    if not args.execute:
        print("DRY RUN: no GitHub API calls, no token read, and no branch created.")
        print("To test Workflows write on a disposable branch: "
              "python scripts/gptauto_workflow_write_probe_r07.py --execute")
        return 0
    token = (os.environ.get("GPTAUTO_GITHUB_TOKEN")
             or os.environ.get("GPTAUTO_SYNC_TOKEN")
             or os.environ.get("GPTAUTO_EXECUTOR_TOKEN") or "")
    if not token:
        print("ERROR: configure an explicit GPTAUTO_* token with "
              "Contents, Pull requests and Workflows write", file=sys.stderr)
        return 2
    try:
        result = run_probe(GitHubApi(token), uuid.uuid4().hex[:12])
    except (ProbeError, ValueError) as exc:
        print(f"PROBE FAILED: {exc}", file=sys.stderr)
        return 1
    print("SUCCESS:", result["result"], "(main unchanged)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
