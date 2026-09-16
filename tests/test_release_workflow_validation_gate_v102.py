from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_workflow_uses_event_driven_post_merge_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "workflow_run:" in workflow
    assert "      - CI" in workflow
    assert "      - Production image smoke" in workflow
    assert "types: [completed]" in workflow
    assert "branches: [main]" in workflow
    assert "Verify both post-merge validations" in workflow
    assert "head_sha=${SHA}" in workflow
    assert 'select(.name==\"CI\")' in workflow
    assert 'select(.name==\"Production image smoke\")' in workflow
    assert "completed:success" in workflow
    assert "required post-merge validation failed" in workflow
    assert "Peer validation still pending" in workflow

    # v4 gate must not occupy a runner while the peer workflow is still running.
    assert "deadline=$((SECONDS + 900))" not in workflow
    assert "sleep 5" not in workflow
    assert "timed out waiting for CI and Production image smoke" not in workflow

    gate = workflow.index("- name: Verify both post-merge validations")
    checkout = workflow.index("- name: Checkout validated source")
    resolve = workflow.index("- name: Resolve and validate release versions")
    existing = workflow.index("- name: Check whether release already exists")
    create = workflow.index("- name: Create GitHub Release")
    assert gate < checkout < resolve < existing < create


def test_release_workflow_validates_exact_gated_source_before_release() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "ref: ${{ steps.candidate.outputs.sha }}" in workflow
    assert "SERVER_RUNTIME_VERSION" in workflow
    assert "CHROME_BRIDGE_BUNDLE_VERSION" in workflow
    assert "server = one(" in workflow
    assert "bundle = one(" in workflow
    assert 'manifest.get("version") != bundle' in workflow
    assert '--target "$SOURCE_SHA"' in workflow


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
