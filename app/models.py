from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_model_id(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return "-".join(value.strip().split()).lower()


class ExtensionRegistration(BaseModel):
    name: str = Field(default="Chrome", min_length=1, max_length=120)
    browser_name: str = Field(default="Chrome", max_length=80)
    version: str = Field(default="unknown", max_length=40)
    # v0.17 production registration enforces device_id in its managed-pairing
    # middleware. Keep it optional here so historical patch/unit-test contracts
    # remain valid when v0.17 is not installed.
    device_id: str | None = Field(default=None, min_length=8, max_length=160)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtensionRegistrationResult(BaseModel):
    client_id: str
    token: str


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None


class FileUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=180)
    mime_type: str | None = Field(default=None, max_length=160)
    data_base64: str = Field(min_length=1)
    purpose: Literal["vision", "file-understanding", "image-reference", "voice-input", "chat2api"] = "chat2api"


class AttachmentRef(BaseModel):
    file_id: str = Field(min_length=1, max_length=120)


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

    def referenced_file_ids(self) -> list[str]:
        if not isinstance(self.content, list):
            return []
        result: list[str] = []
        for part in self.content:
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type") or "")
            if kind in {"file", "input_file"}:
                value = part.get("file_id") or (part.get("file") or {}).get("file_id")
                if value:
                    result.append(str(value))
            elif kind in {"image_url", "input_image"}:
                value = part.get("image_url")
                if isinstance(value, dict):
                    value = value.get("url")
                if isinstance(value, str) and value.startswith("file_"):
                    result.append(value)
        return result


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = "gpt-5.6-sol"
    messages: list[ChatMessage]
    stream: bool = False
    client_id: str | None = None
    prompt_mode: Literal["last_user", "full"] = "last_user"
    timeout: int | None = Field(default=None, ge=5, le=900)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=4)
    reasoning_effort: str | None = Field(default=None, max_length=40)
    reasoning: dict[str, Any] | None = None
    max_completion_tokens: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)

    @field_validator("model", mode="before")
    @classmethod
    def normalize_model(cls, value: Any) -> Any:
        return normalize_model_id(value)

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, value: list[ChatMessage]) -> list[ChatMessage]:
        if not value:
            raise ValueError("messages must not be empty")
        if not any(item.role == "user" and item.text() for item in value):
            raise ValueError("messages must contain a non-empty user message")
        return value

    def all_file_ids(self) -> list[str]:
        result = [item.file_id for item in self.attachments]
        for message in self.messages:
            result.extend(message.referenced_file_ids())
        return list(dict.fromkeys(result))


class ImageGenerationRequest(BaseModel):
    model: Literal["gpt-image"] = "gpt-image"
    prompt: str = Field(min_length=1, max_length=12000)
    n: int = Field(default=1, ge=1, le=1)
    size: str | None = Field(default=None, max_length=40)
    response_format: Literal["url", "b64_json"] = "b64_json"
    client_id: str | None = None
    timeout: int | None = Field(default=None, ge=30, le=900)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=4)


class TestRunCreate(BaseModel):
    run_id: str = Field(min_length=1, max_length=120)
    test_type: str = Field(min_length=1, max_length=80)
    status: Literal["passed", "warning", "failed", "skipped"]
    model: str | None = Field(default=None, max_length=120)
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    summary: str = Field(default="", max_length=4000)
    results: list[dict[str, Any]] = Field(default_factory=list)
    quality: dict[str, Any] = Field(default_factory=dict)

    @field_validator("model", mode="before")
    @classmethod
    def normalize_test_model(cls, value: Any) -> Any:
        return normalize_model_id(value)


class ClientSummary(BaseModel):
    client_id: str
    name: str
    version: str
    online: bool
    busy: bool
    connection_enabled: bool = True
    device_id: str | None = None
    pairing_id: str | None = None
    bound_api_keys: int = 0
    last_seen_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
