from __future__ import annotations

from typing import Any, Iterable

MODEL_ALIASES = {
    "gpt-5.6-sol-wm": "gpt-5.6-sol",
    "gpt-5.6-terra-wm": "gpt-5.6-terra",
    "gpt-5.6-luna-wm": "gpt-5.6-luna",
    "gpt-5.5-wm": "gpt-5.5",
    "gpt-5-5-instant": "gpt-5.5",
    "gpt-5-5-thinking": "gpt-5.5",
    "gpt-5-6-thinking": "gpt-5.6-sol",
    "gpt-5-6": "gpt-5.6-sol",
    "gpt-6-astra-wm": "gpt-6-astra",
    "gpt-6-sol-wm": "gpt-6-sol",
    "gpt-6-luna-wm": "gpt-6-luna",
}

MODEL_TRANSPORT_IDS = {
    "gpt-5.6-sol": "gpt-5.6-sol-wm",
    "gpt-5.6-terra": "gpt-5.6-terra-wm",
    "gpt-5.6-luna": "gpt-5.6-luna-wm",
    "gpt-6-astra": "gpt-6-astra-wm",
    "gpt-6-sol": "gpt-6-sol-wm",
    "gpt-6-luna": "gpt-6-luna-wm",
}

NON_CONCRETE_MODEL_IDS = {"auto", "default", "chatgpt-web"}


def _known_family(model: str) -> str | None:
    for family in ("gpt-6-astra", "gpt-6-pro", "gpt-6-sol", "gpt-6-terra", "gpt-6-luna"):
        if model == family or any(model.startswith(family + suffix) for suffix in (".", ":", "_", "-")):
            return family
    return None


def normalize_model_id(value: Any) -> str | None:
    if value is None:
        return None
    model = "-".join(str(value).strip().lower().split())
    if not model or len(model) > 128 or not all(ch.isalnum() or ch in "._:-" for ch in model):
        return None
    return _known_family(model) or MODEL_ALIASES.get(model, model)


def normalize_concrete_model_id(value: Any) -> str | None:
    model = normalize_model_id(value)
    return model if model and model not in NON_CONCRETE_MODEL_IDS else None


def model_transport_id(value: Any) -> str | None:
    model = normalize_model_id(value)
    return MODEL_TRANSPORT_IDS.get(model, model) if model else None


def normalize_reasoning_level(value: Any) -> str | None:
    level = str(value or "").strip().lower().replace("_", "-")
    if level in {"extra high", "extra-high", "xhigh"}:
        return "extra-high"
    if level == "extended":
        return "high"
    return level if level in {"low", "medium", "high"} else None


def model_priority_score(value: Any) -> int:
    model = normalize_concrete_model_id(value)
    if not model:
        return -1
    import re

    match = re.match(r"^gpt-(\d+)(?:[.-](\d+))?", model)
    major = int(match.group(1)) if match else 0
    minor = int(match.group(2)) if match and match.group(2) else 0
    tier = 500 if "astra" in model else 450 if "pro" in model else 300 if "sol" in model else 200 if "terra" in model else 100 if "luna" in model else 0
    return major * 1_000_000 + minor * 10_000 + tier


def prioritize_models(values: Iterable[Any]) -> list[str]:
    seen: dict[str, int] = {}
    for index, value in enumerate(values):
        model = normalize_concrete_model_id(value)
        if model and model not in seen:
            seen[model] = index
    return sorted(seen, key=lambda model: (-model_priority_score(model), seen[model]))
