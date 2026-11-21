from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_gate_does_not_treat_superseded_validation_as_code_failure():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "completed:failure|completed:timed_out|completed:startup_failure" in workflow
    assert 'is_validation_failure "$ci_latest"' in workflow
    assert 'is_validation_failure "$smoke_latest"' in workflow
    assert 'completed:* && "$ci_latest" != "completed:success"' not in workflow
    assert 'completed:* && "$smoke_latest" != "completed:success"' not in workflow
