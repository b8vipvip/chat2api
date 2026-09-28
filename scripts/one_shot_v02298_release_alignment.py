from __future__ import annotations

from pathlib import Path

OLD = "0.22.97"
NEW = "0.22.98"
ROOTS = (Path("app"), Path("chrome_extension"), Path("tests"))
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".json", ".html", ".md", ".txt", ".sh"}


def align_versions() -> list[str]:
    changed: list[str] = []
    for root in ROOTS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8")
            updated = text.replace(OLD, NEW)
            if updated != text:
                path.write_text(updated, encoding="utf-8")
                changed.append(str(path))
    return changed


def patch_runtime_contract() -> None:
    path = Path("app/runtime_contract.py")
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "# v0.22.97+: one release version is the single source of truth for the",
        "# v0.22.98+: one release version is the single source of truth for the",
        1,
    )
    old_revision = "model-evidence-v144-final-model-authority-v144-release-v02297\""
    new_revision = "model-evidence-v144-final-model-authority-v144-release-v02297-worker-runtime-preflight-repair-v145-routable-standby-v145-release-v02298\""
    if old_revision not in text:
        raise SystemExit("runtime feature revision anchor missing")
    text = text.replace(old_revision, new_revision, 1)

    old_build = "model-evidence-v144-release-v0843\","
    new_build = "model-evidence-v144-release-v0843-worker-runtime-preflight-repair-v145-routable-standby-v145-release-v0844\","
    if old_build not in text:
        raise SystemExit("bridge build revision anchor missing")
    text = text.replace(old_build, new_build, 1)

    anchor = '            "worker_redesigned_chatgpt_model_evidence_v144": True,\n'
    flags = (
        anchor
        + '            "worker_runtime_preflight_repair_v145": True,\n'
        + '            "routable_standby_semantics_v145": True,\n'
    )
    if anchor not in text:
        raise SystemExit("v144 feature flag anchor missing")
    text = text.replace(anchor, flags, 1)
    path.write_text(text, encoding="utf-8")


def validate() -> None:
    required = {
        "app/runtime_contract.py": [
            'RELEASE_VERSION = "0.22.98"',
            'SERVER_RUNTIME_VERSION = "0.22.98"',
            'CHROME_BRIDGE_VERSION = "0.22.98"',
            'CHROME_BRIDGE_BUNDLE_VERSION = "0.22.98"',
            '"worker_runtime_preflight_repair_v145": True',
            '"routable_standby_semantics_v145": True',
        ],
        "chrome_extension/manifest.json": ['"version": "0.22.98"'],
        "chrome_extension/background_runtime_preflight_v48.js": ['const REQUIRED_BUNDLE = "0.22.98"'],
        "chrome_extension/content_runtime_contract_v48.js": ['const REQUIRED_BUNDLE = "0.22.98"'],
        "chrome_extension/content_runtime_contract_v71.js": ['const REQUIRED_BUNDLE = "0.22.98"'],
        "chrome_extension/content_bundle_marker_v48.js": ['bundle: "0.22.98"'],
        "chrome_extension/content_bundle_marker_v71.js": ['bundle: "0.22.98"'],
    }
    for filename, tokens in required.items():
        text = Path(filename).read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                raise SystemExit(f"{filename}: missing {token}")


def main() -> None:
    changed = align_versions()
    patch_runtime_contract()
    validate()
    print(f"aligned {len(changed)} files from {OLD} to {NEW}")


if __name__ == "__main__":
    main()
