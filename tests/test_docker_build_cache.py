from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_keeps_dependency_layer_cacheable():
    source = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert source.startswith("# syntax=docker/dockerfile:1.7")
    assert "COPY requirements.txt ./" in source
    assert "COPY app ./app" in source
    assert source.index("COPY requirements.txt ./") < source.index("COPY app ./app")
    assert "--mount=type=cache,id=chat2api-pip,target=/root/.cache/pip,sharing=locked" in source
    assert "pip install -r requirements.txt" in source
    assert "--no-cache-dir" not in source
    assert "--prefer-binary" not in source


def test_dockerignore_excludes_runtime_and_development_churn():
    entries = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for required in {
        ".git",
        ".github",
        ".env",
        "data",
        "chrome_extension",
        "docs",
        "tests",
    }:
        assert required in entries

    # Worker-only scripts remain excluded from the server image context, while
    # the public bootstrap is explicitly allowed so Docker can COPY it.
    assert "scripts" not in entries
    assert "scripts/*" in entries
    assert "!scripts/bootstrap_linux_worker.sh" in entries
