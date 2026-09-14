#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROTECTED_WORDS = ("release", "deploy", "publish", "store package", "store-package")
DEBUG_WORDS = ("debug", "diagnostic", "one-shot", "oneshot", "tmp-")


def top_level_block(text: str, key: str) -> str | None:
    lines = text.splitlines(keepends=True)
    start = None
    key_re = re.compile(rf"^{re.escape(key)}:\s*(?:#.*)?$")
    for i, line in enumerate(lines):
        if key_re.match(line.rstrip("\n")):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end = j
            break
    return "".join(lines[start:end])


def workflow_name(text: str, path: Path) -> str:
    match = re.search(r"(?m)^name:\s*(.+?)\s*$", text)
    return match.group(1).strip().strip("'\"") if match else path.stem


def is_protected(name: str, path: Path) -> bool:
    haystack = f"{name} {path.name}".lower()
    return any(word in haystack for word in PROTECTED_WORDS)


def is_debug(name: str, path: Path) -> bool:
    haystack = f"{name} {path.name}".lower()
    return any(word in haystack for word in DEBUG_WORDS)


def job_blocks(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^jobs:\s*(?:#.*)?$", line.rstrip("\n")):
            start = i
            break
    if start is None:
        return []

    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end = j
            break

    starts: list[tuple[int, str]] = []
    pattern = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$")
    for i in range(start + 1, end):
        match = pattern.match(lines[i].rstrip("\n"))
        if match:
            starts.append((i, match.group(1)))

    blocks: list[tuple[str, str]] = []
    for index, (line_number, name) in enumerate(starts):
        block_end = starts[index + 1][0] if index + 1 < len(starts) else end
        blocks.append((name, "".join(lines[line_number:block_end])))
    return blocks


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return errors

    text = path.read_text(encoding="utf-8")
    name = workflow_name(text, path)
    protected = is_protected(name, path)
    debug = is_debug(name, path)

    on_block = top_level_block(text, "on")
    if on_block is None:
        errors.append("missing top-level on:")
    else:
        if "workflow_dispatch:" not in on_block:
            errors.append("missing workflow_dispatch recovery entry point")
        if debug and any(token in on_block for token in ("\n  push:", "\n  pull_request:", "\n  schedule:")):
            errors.append("debug/diagnostic workflow may only use workflow_dispatch")

    if top_level_block(text, "permissions") is None:
        errors.append("missing top-level permissions:")

    concurrency = top_level_block(text, "concurrency")
    if concurrency is None:
        errors.append("missing top-level concurrency:")
    elif protected:
        if "cancel-in-progress: false" not in concurrency:
            errors.append("release/deploy/publish workflow must use cancel-in-progress: false")
    elif "cancel-in-progress: true" not in concurrency:
        errors.append("ordinary workflow must use cancel-in-progress: true")

    blocks = job_blocks(text)
    if not blocks:
        errors.append("missing jobs:")
    for job_name, block in blocks:
        if re.search(r"(?m)^    uses:\s*", block):
            continue
        if "runs-on:" in block and not re.search(r"(?m)^    timeout-minutes:\s*\d+", block):
            errors.append(f"job '{job_name}' is missing timeout-minutes")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate changed GitHub Actions workflows against Strategy v3.")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()

    all_errors: list[str] = []
    for path in args.paths:
        if path.suffix not in {".yml", ".yaml"}:
            continue
        errors = validate(path)
        if errors:
            all_errors.append(f"{path}:")
            all_errors.extend(f"  - {error}" for error in errors)

    if all_errors:
        print("GitHub Actions Strategy v3 policy violations:", file=sys.stderr)
        print("\n".join(all_errors), file=sys.stderr)
        return 1

    print("GitHub Actions Strategy v3 policy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
