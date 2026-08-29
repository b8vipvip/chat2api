from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from app.playground_random_prompt_patch import (
    PATCH_ID,
    _PromptClientProxy,
    _chat_prompt,
    _decorate_result,
    _image_prompt,
)


ROOT = Path(__file__).resolve().parents[1]
MARKER = re.compile(r"PG-[0-9A-F]{10}")


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((url, kwargs))
        return {"ok": True}


def test_text_prompts_are_unique_and_keep_a_machine_visible_marker() -> None:
    seen_prompts: set[str] = set()
    seen_markers: set[str] = set()
    for _ in range(12):
        prompt, marker, variant = _chat_prompt("text")
        assert MARKER.fullmatch(marker)
        assert marker in prompt
        assert variant in {"arithmetic", "rewrite", "classification", "micro-explanation"}
        seen_prompts.add(prompt)
        seen_markers.add(marker)
    assert len(seen_prompts) == 12
    assert len(seen_markers) == 12


def test_vision_file_and_image_prompts_are_randomized_without_changing_test_intent() -> None:
    vision, vision_marker, vision_variant = _chat_prompt("vision")
    document, file_marker, file_variant = _chat_prompt("file")
    image, image_marker, image_variant = _image_prompt(False)
    edit, edit_marker, edit_variant = _image_prompt(True)

    assert vision_marker in vision and "附件图片" in vision
    assert file_marker in document and "附件文件" in document
    assert image_marker in image and "生成一张" in image
    assert edit_marker in edit and "附件主体" in edit
    assert vision_variant in {"main-object", "dominant-color", "composition"}
    assert file_variant in {"summary", "purpose-detail", "keyword"}
    assert image_variant in {"kite", "lighthouse", "plant", "sunset", "robot"}
    assert edit_variant in {"palette", "illustration", "composition"}
    assert len({vision_marker, file_marker, image_marker, edit_marker}) == 4


def test_client_proxy_rewrites_only_playground_payload_copy_and_records_diagnostics() -> None:
    base = FakeClient()
    proxy = _PromptClientProxy(base, kind="text")
    original = {
        "model": "gpt-5.5-mini",
        "messages": [{"role": "user", "content": "fixed old prompt"}],
        "stream": True,
    }
    asyncio.run(proxy.post("/v1/chat/completions", json=original))
    assert original["messages"][0]["content"] == "fixed old prompt"
    sent = base.calls[0][1]["json"]
    assert sent["messages"][0]["content"] != "fixed old prompt"
    assert proxy.prompt_id in sent["messages"][0]["content"]
    assert proxy.prompt_variant
    assert proxy.prompt_preview

    result = _decorate_result({"status": "passed"}, proxy)
    assert result["prompt_randomization"] == PATCH_ID
    assert result["prompt_id"] == proxy.prompt_id
    assert result["prompt_variant"] == proxy.prompt_variant
    assert result["prompt_preview"] == proxy.prompt_preview


def test_production_entry_installs_random_prompts_after_multimodal_defaults() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .playground_random_prompt_patch import install_playground_random_prompt_patch" in entry
    assert "install_playground_random_prompt_patch(app)" in entry
    assert entry.index("install_playground_lifecycle_patch(app)") < entry.index("install_playground_multimodal_defaults_patch(app)")
    assert entry.index("install_playground_multimodal_defaults_patch(app)") < entry.index("install_playground_random_prompt_patch(app)")
    assert entry.index("install_playground_random_prompt_patch(app)") < entry.index("install_runtime_contract(app)")
