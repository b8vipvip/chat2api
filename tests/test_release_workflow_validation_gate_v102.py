from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_workflow_waits_for_post_merge_ci_and_image_smoke() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "Wait for post-merge validation" in workflow
    assert "head_sha=${SHA}" in workflow
    assert 'select(.name=="CI")' in workflow
    assert 'select(.name=="Production image smoke")' in workflow
    assert "completed:success" in workflow
    assert "deadline=$((SECONDS + 900))" in workflow
    assert "sleep 5" in workflow
    assert "required post-merge validation failed" in workflow
    assert "timed out waiting for CI and Production image smoke" in workflow

    gate = workflow.index("- name: Wait for post-merge validation")
    existing = workflow.index("- name: Check whether release already exists")
    create = workflow.index("- name: Create GitHub Release")
    assert gate < existing < create


def test_release_workflow_still_validates_runtime_contract_before_gating() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    resolve = workflow.index("- name: Resolve and validate release versions")
    gate = workflow.index("- name: Wait for post-merge validation")
    assert resolve < gate
    assert "SERVER_RUNTIME_VERSION" in workflow
    assert "CHROME_BRIDGE_BUNDLE_VERSION" in workflow
    assert "server = one(" in workflow
    assert "bundle = one(" in workflow
    assert 'manifest.get("version") != bundle' in workflow


def test_production_image_smoke_runs_for_every_main_push() -> None:
    workflow = (ROOT / ".github" / "workflows" / "production-image-smoke.yml").read_text(encoding="utf-8")

    push = workflow.index("  push:\n")
    jobs = workflow.index("\njobs:\n", push)
    push_block = workflow[push:jobs]
    assert "branches: [main]" in push_block
    assert "paths:" not in push_block

    pull_request = workflow.index("  pull_request:\n")
    assert pull_request < push
    assert "paths:" in workflow[pull_request:push]


def test_validated_release_epoch_is_not_blocked_by_legacy_gate_run() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "group: chat2api-release-validated" in workflow
    assert "cancel-in-progress: false" in workflow
