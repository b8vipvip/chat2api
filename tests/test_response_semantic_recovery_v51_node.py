from __future__ import annotations

import subprocess


def test_response_semantic_recovery_v51_node_contract() -> None:
    subprocess.run(
        ["node", "--check", "chrome_extension/content_response_semantic_recovery_v51.js"],
        check=True,
        text=True,
    )
    subprocess.run(
        ["node", "tests/response_semantic_recovery_v51.mjs"],
        check=True,
        text=True,
    )
