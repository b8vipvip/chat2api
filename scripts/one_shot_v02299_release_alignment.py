from __future__ import annotations

from pathlib import Path

OLD = "0.22.98"
NEW = "0.22.99"
ROOTS = (Path("app"), Path("chrome_extension"), Path("tests"))
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".json", ".html", ".md", ".txt", ".sh"}


def replace_versions(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
        return False
    text = path.read_text(encoding="utf-8")
    updated = text.replace(OLD, NEW)
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def align_versions() -> list[str]:
    changed: list[str] = []
    for root in ROOTS:
        for path in root.rglob("*"):
            if replace_versions(path):
                changed.append(str(path))
    return changed


def patch_runtime_contract() -> None:
    path = Path("app/runtime_contract.py")
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "# v0.22.98+: one release version is the single source of truth for the",
        "# v0.22.99+: one release version is the single source of truth for the",
        1,
    )

    if "release-v02299" not in text:
        old_revision = "release-v02298-runtime-ready-standby-v146\""
        new_revision = "release-v02298-runtime-ready-standby-v146-release-v02299\""
        if old_revision not in text:
            raise SystemExit("runtime v0.22.99 feature revision anchor missing")
        text = text.replace(old_revision, new_revision, 1)

    if "release-v0845" not in text:
        old_build = "release-v0844-runtime-ready-standby-v146\","
        new_build = "release-v0844-runtime-ready-standby-v146-release-v0845\","
        if old_build not in text:
            raise SystemExit("bridge v0.22.99 build revision anchor missing")
        text = text.replace(old_build, new_build, 1)

    path.write_text(text, encoding="utf-8")


def validate() -> None:
    required = {
        "app/runtime_contract.py": [
            'RELEASE_VERSION = "0.22.99"',
            'SERVER_RUNTIME_VERSION = "0.22.99"',
            'CHROME_BRIDGE_VERSION = "0.22.99"',
            'CHROME_BRIDGE_BUNDLE_VERSION = "0.22.99"',
            '"persistent_window_runtime_preflight_v146": True',
            '"runtime_ready_standby_semantics_v146": True',
            "release-v02299",
            "release-v0845",
        ],
        "chrome_extension/manifest.json": ['"version": "0.22.99"'],
        "chrome_extension/background_runtime_preflight_v48.js": ['const REQUIRED_BUNDLE = "0.22.99"'],
        "chrome_extension/content_runtime_contract_v48.js": ['const REQUIRED_BUNDLE = "0.22.99"'],
        "chrome_extension/content_runtime_contract_v71.js": ['const REQUIRED_BUNDLE = "0.22.99"'],
        "chrome_extension/content_bundle_marker_v48.js": ['bundle: "0.22.99"'],
        "chrome_extension/content_bundle_marker_v71.js": ['bundle: "0.22.99"'],
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
