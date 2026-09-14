#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

PROTECTED_WORDS = ("release", "deploy", "publish", "store package", "store-package")
INTERNAL_FILES = {"actions-governor.yml", "actions-recovery.yml", "actions-policy-check.yml"}


def top_level_block(text: str, key: str) -> tuple[int, int] | None:
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
    return start, end


def workflow_name(text: str, path: Path) -> str:
    match = re.search(r"(?m)^name:\s*(.+?)\s*$", text)
    return match.group(1).strip().strip("'\"") if match else path.stem


def is_protected(name: str, path: Path) -> bool:
    haystack = f"{name} {path.name}".lower()
    return any(word in haystack for word in PROTECTED_WORDS)


def ensure_workflow_dispatch(text: str) -> tuple[str, bool]:
    block = top_level_block(text, "on")
    if not block:
        return text, False
    lines = text.splitlines(keepends=True)
    start, end = block
    current = "".join(lines[start:end])
    if "workflow_dispatch:" in current:
        return text, False
    if lines[start].strip() != "on:":
        return text, False
    lines.insert(end, "  workflow_dispatch:\n")
    return "".join(lines), True


def ensure_top_level_policy(text: str, protected: bool) -> tuple[str, bool]:
    jobs = top_level_block(text, "jobs")
    if jobs is None:
        return text, False
    insert_at = jobs[0]
    lines = text.splitlines(keepends=True)
    additions: list[str] = []

    if re.search(r"(?m)^permissions:\s*(?:#.*)?$", text) is None:
        additions.append("permissions:\n  contents: read\n\n")

    if re.search(r"(?m)^concurrency:\s*(?:#.*)?$", text) is None:
        if protected:
            additions.append(
                "concurrency:\n"
                "  group: ${{ github.workflow }}-${{ github.repository }}\n"
                "  cancel-in-progress: false\n\n"
            )
        else:
            additions.append(
                "concurrency:\n"
                "  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}\n"
                "  cancel-in-progress: true\n\n"
            )

    if not additions:
        return text, False
    lines[insert_at:insert_at] = additions
    return "".join(lines), True


def ensure_job_timeouts(text: str, protected: bool) -> tuple[str, bool]:
    jobs = top_level_block(text, "jobs")
    if jobs is None:
        return text, False

    lines = text.splitlines(keepends=True)
    jobs_start, jobs_end = jobs
    starts: list[int] = []
    job_re = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$")
    for i in range(jobs_start + 1, jobs_end):
        if job_re.match(lines[i].rstrip("\n")):
            starts.append(i)

    default_timeout = 120 if protected else 30
    inserts: list[tuple[int, str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else jobs_end
        block_text = "".join(lines[start:end])
        if re.search(r"(?m)^    uses:\s*", block_text):
            continue
        if "runs-on:" not in block_text:
            continue
        if re.search(r"(?m)^    timeout-minutes:\s*", block_text):
            continue
        for line_number in range(start + 1, end):
            if re.match(r"^    runs-on:\s*", lines[line_number]):
                inserts.append((line_number + 1, f"    timeout-minutes: {default_timeout}\n"))
                break

    if not inserts:
        return text, False
    for index, payload in reversed(inserts):
        lines.insert(index, payload)
    return "".join(lines), True


def repair(path: Path) -> tuple[bool, list[str]]:
    text = path.read_text(encoding="utf-8")
    original = text
    name = workflow_name(text, path)
    protected = is_protected(name, path)
    notes: list[str] = []

    text, changed = ensure_workflow_dispatch(text)
    if changed:
        notes.append("added workflow_dispatch")

    text, changed = ensure_top_level_policy(text, protected)
    if changed:
        notes.append("added missing top-level policy guards")

    text, changed = ensure_job_timeouts(text, protected)
    if changed:
        notes.append("added missing job timeouts")

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True, notes
    return False, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely repair GitHub Actions policy-level defects.")
    parser.add_argument("workflow", type=Path)
    args = parser.parse_args()

    path = args.workflow
    if not path.exists():
        print(f"workflow does not exist: {path}")
        return 2
    if path.name in INTERNAL_FILES:
        print(f"internal strategy workflow is not auto-rewritten: {path}")
        return 0

    changed, notes = repair(path)
    if changed:
        print(f"repaired {path}: {', '.join(notes)}")
    else:
        print(f"no safe policy repair needed for {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
