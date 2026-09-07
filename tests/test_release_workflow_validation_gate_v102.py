from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_workflow_waits_for_post_merge_ci_and_image_smoke() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "Wait for post-merge validation" in workflow
    assert "head_sha=${SHA}" in workflow
    assert 'select(.name=="CI")' in workflow
    assert 'select(.name=="Production image smoke")' in workflow
    assert 'completed:success' in workflow
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
    assert 'SERVER_RUNTIME_VERSION = \\"([^\\"]+)\\"' in workflow
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = \\"([^\\"]+)\\"' in workflow
    assert 'manifest.get("version") != bundle' in workflow
