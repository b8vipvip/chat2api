from __future__ import annotations

import json
import os
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .timezone_utils import beijing_now_iso


STORE_VERSION = 1
MAX_PREFIX_SUFFIX_CHARS = 20_000
MAX_RULES = 50
MAX_PATTERN_CHARS = 1_000
MAX_REPLACEMENT_CHARS = 1_000

DEFAULT_RULES = [
    {
        "name": "电子邮箱",
        "enabled": False,
        "pattern": r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "replacement": "[REDACTED_EMAIL]",
        "flags": "",
    },
    {
        "name": "Bearer Token",
        "enabled": False,
        "pattern": r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*",
        "replacement": "Bearer [REDACTED_TOKEN]",
        "flags": "",
    },
    {
        "name": "常见 API Key",
        "enabled": False,
        "pattern": r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b",
        "replacement": "[REDACTED_API_KEY]",
        "flags": "",
    },
]


def default_config() -> dict[str, Any]:
    return {
        "version": STORE_VERSION,
        "prefix": "",
        "suffix": "",
        "redaction_enabled": False,
        "rules": deepcopy(DEFAULT_RULES),
        "audit_final_prompt": True,
        "revision": 1,
        "updated_at": beijing_now_iso(),
    }


def _compile_flags(value: str) -> int:
    flags = 0
    for item in str(value or "").lower():
        if item == "i":
            flags |= re.IGNORECASE
        elif item == "m":
            flags |= re.MULTILINE
        elif item == "s":
            flags |= re.DOTALL
        elif item.strip():
            raise ValueError(f"Unsupported regex flag: {item}")
    return flags


def _normalize_rule(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Redaction rule #{index + 1} must be an object")
    name = str(raw.get("name") or f"规则 {index + 1}").strip()[:120]
    pattern = str(raw.get("pattern") or "")
    replacement = str(raw.get("replacement") or "[REDACTED]")
    flags = str(raw.get("flags") or "").strip().lower()
    if not pattern:
        raise ValueError(f"Redaction rule '{name}' has an empty pattern")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"Redaction rule '{name}' pattern is too long")
    if len(replacement) > MAX_REPLACEMENT_CHARS:
        raise ValueError(f"Redaction rule '{name}' replacement is too long")
    try:
        re.compile(pattern, _compile_flags(flags))
    except re.error as exc:
        raise ValueError(f"Redaction rule '{name}' regex is invalid: {exc}") from exc
    return {
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "pattern": pattern,
        "replacement": replacement,
        "flags": flags,
    }


def normalize_config(raw: Any, *, previous_revision: int = 0) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Prompt configuration must be an object")
    prefix = str(raw.get("prefix") or "")
    suffix = str(raw.get("suffix") or "")
    if len(prefix) > MAX_PREFIX_SUFFIX_CHARS:
        raise ValueError(f"Prompt prefix exceeds {MAX_PREFIX_SUFFIX_CHARS} characters")
    if len(suffix) > MAX_PREFIX_SUFFIX_CHARS:
        raise ValueError(f"Prompt suffix exceeds {MAX_PREFIX_SUFFIX_CHARS} characters")
    rules_raw = raw.get("rules")
    if rules_raw is None:
        rules_raw = []
    if not isinstance(rules_raw, list):
        raise ValueError("Redaction rules must be a list")
    if len(rules_raw) > MAX_RULES:
        raise ValueError(f"At most {MAX_RULES} redaction rules are allowed")
    rules = [_normalize_rule(item, index) for index, item in enumerate(rules_raw)]
    return {
        "version": STORE_VERSION,
        "prefix": prefix,
        "suffix": suffix,
        "redaction_enabled": bool(raw.get("redaction_enabled", False)),
        "rules": rules,
        "audit_final_prompt": bool(raw.get("audit_final_prompt", True)),
        "revision": max(1, int(previous_revision or 0) + 1),
        "updated_at": beijing_now_iso(),
    }


class PromptConfigStore:
    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / "prompt_config.json"
        self.config = default_config()
        self.last_error = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            loaded = normalize_config(raw, previous_revision=max(0, int(raw.get("revision") or 1) - 1))
            loaded["revision"] = max(1, int(raw.get("revision") or loaded["revision"]))
            loaded["updated_at"] = str(raw.get("updated_at") or loaded["updated_at"])
            self.config = loaded
            self.last_error = ""
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.last_error = str(exc)
            self.config = default_config()

    def snapshot(self) -> dict[str, Any]:
        result = deepcopy(self.config)
        result["last_error"] = self.last_error
        return result

    def save(self, raw: Any) -> dict[str, Any]:
        next_config = normalize_config(raw, previous_revision=int(self.config.get("revision") or 0))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(next_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self.config = next_config
        self.last_error = ""
        return self.snapshot()

    def apply(self, prompt: str) -> tuple[str, dict[str, Any]]:
        base = str(prompt or "")
        prefix = str(self.config.get("prefix") or "").strip()
        suffix = str(self.config.get("suffix") or "").strip()
        pieces = [piece for piece in (prefix, base, suffix) if piece]
        final = "\n\n".join(pieces)
        applied: list[dict[str, Any]] = []
        if self.config.get("redaction_enabled"):
            for rule in self.config.get("rules") or []:
                if not rule.get("enabled"):
                    continue
                regex = re.compile(str(rule.get("pattern") or ""), _compile_flags(str(rule.get("flags") or "")))
                final, count = regex.subn(str(rule.get("replacement") or "[REDACTED]"), final)
                if count:
                    applied.append({"name": rule.get("name"), "count": count})
        return final, {
            "revision": int(self.config.get("revision") or 1),
            "redaction_enabled": bool(self.config.get("redaction_enabled")),
            "redactions": applied,
            "redaction_count": sum(int(item["count"]) for item in applied),
            "audit_final_prompt": bool(self.config.get("audit_final_prompt", True)),
        }
