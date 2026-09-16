#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROTECTED_WORDS = ("release", "deploy", "publish")
DEBUG_WORDS = ("debug", "diagnostic", "one-shot", "oneshot", "tmp-")
INTERNAL_FILES = {"actions-governor.yml", "actions-recovery.yml"}


def block(text: str, key: str) -> str | None:
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if re.match(rf"^{re.escape(key)}:\s*(?:#.*)?$", line.rstrip("\n"))), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i].startswith((" ", "\t", "#")):
            end = i
            break
    return "".join(lines[start:end])


def validate(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    name_match = re.search(r"(?m)^name:\s*(.+?)\s*$", text)
    name = name_match.group(1).strip().strip("'\"") if name_match else path.stem
    hay = f"{name} {path.name}".lower()
    protected = any(word in hay for word in PROTECTED_WORDS)
    debug = any(word in hay for word in DEBUG_WORDS)
    internal = path.name in INTERNAL_FILES
    errors: list[str] = []

    on = block(text, "on")
    if on is None:
        errors.append("missing top-level on:")
    else:
        if "workflow_dispatch:" not in on:
            errors.append("missing workflow_dispatch recovery entry point")
        if debug and any(token in on for token in ("\n  push:", "\n  pull_request:", "\n  schedule:")):
            errors.append("debug/diagnostic workflow may only use workflow_dispatch")

    if block(text, "permissions") is None:
        errors.append("missing top-level permissions:")

    concurrency = block(text, "concurrency")
    if concurrency is None:
        errors.append("missing top-level concurrency:")
    else:
        cancel_match = re.search(r"(?m)^\s+cancel-in-progress:\s*(.+?)\s*$", concurrency)
        if cancel_match is None:
            errors.append("concurrency is missing cancel-in-progress")
        else:
            cancel_value = cancel_match.group(1).strip()
            has_pr = on is not None and "\n  pull_request:" in on
            has_push = on is not None and "\n  push:" in on
            if protected and cancel_value != "false":
                errors.append("release/deploy/publish workflow must use cancel-in-progress: false")
            elif not protected and not internal and has_pr and has_push:
                if cancel_value in {"true", "false"} or "github.event_name" not in cancel_value or "pull_request" not in cancel_value:
                    errors.append("workflow with push + pull_request must cancel PR superseded work only; use a PR-aware expression")
            elif not protected and not internal and cancel_value == "false":
                errors.append("ordinary non-PR workflow should allow superseded work to be cancelled")

    lines = text.splitlines(keepends=True)
    jobs_start = next((i for i, line in enumerate(lines) if re.match(r"^jobs:\s*(?:#.*)?$", line.rstrip("\n"))), None)
    if jobs_start is None:
        errors.append("missing jobs:")
        return errors
    starts = [i for i in range(jobs_start + 1, len(lines)) if re.match(r"^  [A-Za-z0-9_.-]+:\s*(?:#.*)?$", lines[i].rstrip("\n"))]
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        job = "".join(lines[start:end])
        if re.search(r"(?m)^    uses:", job):
            continue
        if "runs-on:" in job and not re.search(r"(?m)^    timeout-minutes:\s*\d+", job):
            job_name_match = re.match(r"^  ([A-Za-z0-9_.-]+):", lines[start])
            job_name = job_name_match.group(1) if job_name_match else "unknown"
            errors.append(f"job '{job_name}' is missing timeout-minutes")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    violations: list[str] = []
    for path in args.paths:
        if path.suffix not in {".yml", ".yaml"}:
            continue
        errors = validate(path)
        if errors:
            violations.append(f"{path}:")
            violations.extend(f"  - {error}" for error in errors)
    if violations:
        print("GitHub Agent v4 policy violations:", file=sys.stderr)
        print("\n".join(violations), file=sys.stderr)
        return 1
    print("GitHub Agent v4 policy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
