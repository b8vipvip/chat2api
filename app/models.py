from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExtensionRegistration(BaseModel):
    name: str = Field(default="Chrome", min_length=1, max_length=120)
    browser_name: str = Field(default="Chrome", max_length=80)
    version: str = Field(default="unknown", max_length=40)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtensionRegistrationResult(BaseModel):
    client_id: str
    token: str


class ChatMessage(BaseModel):
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: Any

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content.strip()
        if isinstance(self.content, list):
            parts: list[str] = []
            for part in self.content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and part.get("type") in {"text", "input_text"}:
                    value = part.get("text") or part.get("input_text") or ""
                    if isinstance(value, str):
                        parts.append(value)
            return "\n".join(item for item in parts if item).strip()
        return str(self.content or "").strip()


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "chatgpt-web"
    messages: list[ChatMessage]
    stream: bool = False
    client_id: str | None = None
    prompt_mode: Literal["last_user", "full"] = "last_user"
    timeout: int | None = Field(default=None, ge=5, le=900)

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, value: list[ChatMessage]) -> list[ChatMessage]:
        if not value:
            raise ValueError("messages must not be empty")
        if not any(item.role == "user" and item.text() for item in value):
            raise ValueError("messages must contain a non-empty user message")
        return value


class ClientSummary(BaseModel):
    client_id: str
    name: str
    version: str
    online: bool
    busy: bool
    last_seen_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
