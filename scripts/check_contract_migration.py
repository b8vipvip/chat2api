from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "chrome_extension"

RUNTIME_TRIGGERS = {
    "app/runtime_contract.py",
    "chrome_extension/manifest.json",
    "chrome_extension/content_request_v5.js",
    "chrome_extension/content_request_v6.js",
    "chrome_extension/content_response_stream_recovery_v49.js",
    "chrome_extension/content_response_semantic_recovery_v51.js",
    "chrome_extension/background_runtime_preflight_v48.js",
    "chrome_extension/content_runtime_contract_v48.js",
    "chrome_extension/content_bundle_marker_v48.js",
}

CONTRACT_TESTS = {
    "tests/test_runtime_contract.py",
    "tests/test_request_chain_v68.py",
    "tests/test_single_response_observer_v53.py",
    "tests/test_response_epoch_rich_v69.py",
    "tests/test_worker_runtime_refresh_v71.py",
    "tests/runtime_preflight_refresh_v71.mjs",
}

READ_PATTERNS = (
    re.compile(r'''EXT\s*/\s*["']([^"']+\.js)["']'''),
    re.compile(r'''(?:readFileSync|read_text)\(\s*["']chrome_extension/([^"']+\.js)["']'''),
    re.compile(r'''Path\(\s*["']chrome_extension/([^"']+\.js)["']\s*\)'''),
)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def changed_paths() -> set[str]:
    base = os.environ.get("CONTRACT_BASE_SHA", "").strip()
    if not base or set(base) == {"0"}:
        try:
            base = git("rev-parse", "HEAD^")
        except subprocess.CalledProcessError:
            return set()
    try:
        git("cat-file", "-e", f"{base}^{{commit}}")
    except subprocess.CalledProcessError:
        subprocess.run(["git", "fetch", "--no-tags", "origin", base], cwd=ROOT, check=True)
    return {line for line in git("diff", "--name-only", base, "HEAD").splitlines() if line}


def stale_runtime_reads() -> list[str]:
    stale: list[str] = []
    for path in sorted((ROOT / "tests").glob("**/*")):
        if path.suffix not in {".py", ".mjs", ".js"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in READ_PATTERNS:
            for match in pattern.finditer(text):
                rel = match.group(1)
                if not (EXT / rel).exists():
                    stale.append(f"{path.relative_to(ROOT)} -> chrome_extension/{rel}")
    return sorted(set(stale))


def main() -> int:
    stale = stale_runtime_reads()
    if stale:
        print("::error::CONTRACT_MIGRATION stale runtime file references detected")
        for item in stale:
            print(f"  - {item}")
        print("Update/remove the stale contract tests in the same architecture migration.")
        return 1

    changed = changed_paths()
    runtime_changes = sorted(changed & RUNTIME_TRIGGERS)
    if runtime_changes and not (changed & CONTRACT_TESTS):
        print("::error::CONTRACT_MIGRATION runtime ownership changed without contract-test migration")
        print("Runtime files changed:")
        for item in runtime_changes:
            print(f"  - {item}")
        print("Update at least one ownership/runtime contract test in the same change.")
        return 1

    print(
        "Contract migration guard passed: "
        f"runtime_changes={len(runtime_changes)} contract_tests_changed={len(changed & CONTRACT_TESTS)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
