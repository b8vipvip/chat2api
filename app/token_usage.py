from __future__ import annotations

import math
import re
from dataclasses import dataclass


CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]")
LATIN_WORD_RE = re.compile(r"[A-Za-z0-9_]+(?:[-'][A-Za-z0-9_]+)*")
NONSPACE_RE = re.compile(r"\S")


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated: bool = True
    estimator: str = "chat2api-heuristic-v1"

    def as_dict(self) -> dict[str, object]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated": self.estimated,
            "estimator": self.estimator,
        }


def estimate_tokens(text: str) -> int:
    """Estimate ChatGPT-style token count without pretending it is provider usage.

    The ChatGPT web UI does not expose authoritative token usage. This heuristic
    treats CJK/Kana/Hangul characters close to one token each, Latin words as
    roughly one token per 3.4 characters, and remaining punctuation/symbols as
    roughly one token per 2 characters.
    """
    value = str(text or "")
    if not value:
        return 0

    cjk_count = len(CJK_RE.findall(value))
    latin_words = LATIN_WORD_RE.findall(value)
    latin_chars = sum(len(item) for item in latin_words)

    consumed = cjk_count + latin_chars
    nonspace_count = len(NONSPACE_RE.findall(value))
    remainder = max(0, nonspace_count - consumed)

    latin_tokens = math.ceil(latin_chars / 3.4) if latin_chars else 0
    remainder_tokens = math.ceil(remainder / 2) if remainder else 0
    return max(1, cjk_count + latin_tokens + remainder_tokens)


def usage_for(prompt: str, completion: str) -> TokenUsage:
    prompt_tokens = estimate_tokens(prompt)
    completion_tokens = estimate_tokens(completion)
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
