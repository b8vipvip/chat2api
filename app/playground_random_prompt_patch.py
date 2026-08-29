from __future__ import annotations

import secrets
from typing import Any

from fastapi import FastAPI


PATCH_ID = "playground-random-prompts-v1"
_PROMPT_MARKER_PREFIX = "PG-"


def _marker() -> str:
    return _PROMPT_MARKER_PREFIX + secrets.token_hex(5).upper()


def _chat_prompt(kind: str) -> tuple[str, str, str]:
    marker = _marker()
    if kind == "text":
        variant = secrets.choice(("arithmetic", "rewrite", "classification", "micro-explanation"))
        if variant == "arithmetic":
            left = 17 + secrets.randbelow(63)
            right = 11 + secrets.randbelow(71)
            prompt = f"请计算 {left}+{right}，并用一句简短中文给出结果。回答末尾原样附上测试标识：{marker}"
        elif variant == "rewrite":
            phrase = secrets.choice(("稳定、快速、可验证", "清晰、可靠、易维护", "准确、简洁、可复现"))
            prompt = f"请把“{phrase}”改写成一句自然中文，不超过30个汉字。回答末尾原样附上测试标识：{marker}"
        elif variant == "classification":
            number = 20 + secrets.randbelow(80)
            prompt = f"请判断整数 {number} 是奇数还是偶数，并只用一句话说明。回答末尾原样附上测试标识：{marker}"
        else:
            topic = secrets.choice(("为什么白天的天空通常呈蓝色", "为什么冰会浮在水面", "为什么影子会随光源方向变化"))
            prompt = f"请用一句简短中文解释：{topic}。回答末尾原样附上测试标识：{marker}"
        return prompt, marker, variant

    if kind == "vision":
        variant = secrets.choice(("main-object", "dominant-color", "composition"))
        prompts = {
            "main-object": "请识别附件图片的主要内容，并指出一个最显眼的物体或区域。",
            "dominant-color": "请用一句话描述附件图片，并指出最明显的一种颜色。",
            "composition": "请概括附件图片的画面构成，并说明一个容易核验的视觉细节。",
        }
        return f"{prompts[variant]} 回答末尾原样附上测试标识：{marker}", marker, variant

    if kind == "file":
        variant = secrets.choice(("summary", "purpose-detail", "keyword"))
        prompts = {
            "summary": "请阅读附件文件，用一句话概括核心内容。",
            "purpose-detail": "请阅读附件文件，说明它的主要目的，并摘出一个具体事实。",
            "keyword": "请阅读附件文件，用一句话概括主题，并给出一个最关键的词。",
        }
        return f"{prompts[variant]} 回答末尾原样附上测试标识：{marker}", marker, variant

    # Future chat-like playground kinds also receive a unique marker if routed
    # through the normal chat path later.
    variant = "generic"
    return f"请完成本次 chat2api 测试请求，并在回答末尾原样附上测试标识：{marker}", marker, variant


def _image_prompt(has_attachment: bool) -> tuple[str, str, str]:
    marker = _marker()
    if has_attachment:
        variant = secrets.choice(("palette", "illustration", "composition"))
        prompts = {
            "palette": "参考附件主体，保留主体关系但改成明显不同的配色与光照，生成一张新图片。",
            "illustration": "参考附件主体，将其改造成简洁插画风格并改变背景环境，生成一张新图片。",
            "composition": "参考附件主体，保留主题但重新设计构图与视角，生成一张明显不同的新图片。",
        }
    else:
        variant = secrets.choice(("kite", "lighthouse", "plant", "sunset", "robot"))
        prompts = {
            "kite": "生成一张简洁测试图片：晴朗天空中有一只颜色鲜明的风筝，构图清楚。",
            "lighthouse": "生成一张简洁测试图片：海边灯塔、少量云层和清晰地平线，画面干净。",
            "plant": "生成一张简洁测试图片：白色桌面上的绿色盆栽，柔和自然光，主体明确。",
            "sunset": "生成一张简洁测试图片：低矮山丘上的橙色日落，层次简单，主体明确。",
            "robot": "生成一张简洁测试图片：小型友好机器人站在星空下，背景简洁。",
        }
    # The marker makes every request prompt unique without asking the image model
    # to render fragile text. It is diagnostic input only.
    return f"{prompts[variant]} 本次测试随机标识为 {marker}，无需把该标识绘制到图片中。", marker, variant


class _PromptClientProxy:
    def __init__(self, client: Any, *, kind: str) -> None:
        self._client = client
        self.kind = kind
        self.prompt_id = ""
        self.prompt_variant = ""
        self.prompt_preview = ""

    async def post(self, url: str, *args: Any, **kwargs: Any) -> Any:
        payload = kwargs.get("json")
        if url == "/v1/chat/completions" and isinstance(payload, dict):
            body = dict(payload)
            messages = [dict(item) if isinstance(item, dict) else item for item in list(body.get("messages") or [])]
            prompt, marker, variant = _chat_prompt(self.kind)
            if messages and isinstance(messages[0], dict):
                messages[0]["content"] = prompt
            else:
                messages = [{"role": "user", "content": prompt}]
            body["messages"] = messages
            kwargs["json"] = body
            self._remember(prompt, marker, variant)
        elif url == "/v1/images/generations" and isinstance(payload, dict):
            body = dict(payload)
            has_attachment = bool(body.get("attachments"))
            prompt, marker, variant = _image_prompt(has_attachment)
            body["prompt"] = prompt
            kwargs["json"] = body
            self._remember(prompt, marker, variant)
        return await self._client.post(url, *args, **kwargs)

    def _remember(self, prompt: str, marker: str, variant: str) -> None:
        self.prompt_id = marker
        self.prompt_variant = variant
        self.prompt_preview = prompt[:360]


def _decorate_result(result: dict[str, Any], proxy: _PromptClientProxy) -> dict[str, Any]:
    value = dict(result)
    value["prompt_randomization"] = PATCH_ID
    value["prompt_id"] = proxy.prompt_id
    value["prompt_variant"] = proxy.prompt_variant
    value["prompt_preview"] = proxy.prompt_preview
    return value


def install_playground_random_prompt_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "playground_random_prompt_patch_installed", False):
        return app
    manager = getattr(app.state, "playground_run_manager", None)
    if manager is None:
        raise RuntimeError("playground lifecycle must be installed before randomized prompts")

    base_run_chat = manager._run_chat
    base_run_image = manager._run_image

    async def run_chat(client: Any, **kwargs: Any) -> dict[str, Any]:
        kind = str(kwargs.get("kind") or "text")
        proxy = _PromptClientProxy(client, kind=kind)
        result = await base_run_chat(proxy, **kwargs)
        return _decorate_result(result, proxy)

    async def run_image(client: Any, **kwargs: Any) -> dict[str, Any]:
        proxy = _PromptClientProxy(client, kind="image_generation")
        result = await base_run_image(proxy, **kwargs)
        return _decorate_result(result, proxy)

    manager._run_chat = run_chat
    manager._run_image = run_image
    app.state.playground_random_prompt_patch_installed = True
    return app
