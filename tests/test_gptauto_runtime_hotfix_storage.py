from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "gptauto"))
import runtime_hotfix


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and hard-link assertions")
@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_runtime_hotfix_does_not_accept_linked_state_as_deployment_proof(
    tmp_path, monkeypatch, link_kind,
):
    from_sha = "a" * 40
    outside = tmp_path / "outside-runtime-state.json"
    outside.write_text(json.dumps({
        "deployed_sha": from_sha,
        "container_id": "container-1",
        "image_id": "sha256:trusted",
    }), encoding="utf-8")
    state = tmp_path / "runtime-state.json"
    if link_kind == "symlink":
        state.symlink_to(outside)
    else:
        os.link(outside, state)

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("docker", "inspect"):
            return json.dumps([{
                "Config": {"Env": [f"CHAT2API_BUILD_COMMIT={from_sha}"]},
            }])
        raise AssertionError(f"unexpected Docker command: {args}")

    monkeypatch.setattr(runtime_hotfix, "run", fake_run)
    runtime_hotfix.verify_current_container(
        "container-1", from_sha, state, "sha256:trusted",
        "CHAT2API_BUILD_COMMIT",
    )
    assert calls == [("docker", "inspect", "container-1")]
    assert json.loads(outside.read_text(encoding="utf-8"))["deployed_sha"] == from_sha
