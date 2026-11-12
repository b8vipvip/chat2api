"""W-02 Web Chat conversation API, separated from Mode 2 request execution."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .admin_auth import same_origin_admin_request
from .user_commerce import USER_SESSION_COOKIE
from .web_chat_conversation_store import (
    ConversationConflict,
    ConversationLimit,
    WebChatConversationStore,
)


_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Cookie"}


def _http_error(status_code: int, detail: str) -> None:
    raise HTTPException(status_code=status_code, detail=detail, headers=_PRIVATE_HEADERS)


class TextContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"]
    text: str = Field(max_length=8000)


class ImageURLReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=14, max_length=80, pattern=r"^file_[A-Za-z0-9]{8,64}$")


class ImageContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["image_url"]
    image_url: ImageURLReference


ContentPart = Annotated[TextContentPart | ImageContentPart, Field(discriminator="type")]


class MessageSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    role: Literal["user", "assistant"]
    content: str | list[ContentPart]
    createdAt: str = Field(default="", max_length=48)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value):
        if isinstance(value, str):
            if len(value) > 8000:
                raise ValueError("message content exceeds 8000 characters")
            return value
        if not value or len(value) > 8:
            raise ValueError("structured message content must contain 1 to 8 parts")
        text_chars = sum(len(part.text) for part in value if isinstance(part, TextContentPart))
        image_count = sum(isinstance(part, ImageContentPart) for part in value)
        if text_chars > 8000 or image_count > 4:
            raise ValueError("structured message content exceeds text or image limits")
        return value

    @model_validator(mode="after")
    def image_parts_require_user_role(self):
        if self.role != "user" and isinstance(self.content, list):
            if any(isinstance(part, ImageContentPart) for part in self.content):
                raise ValueError("image attachments are only valid on user messages")
        return self


class ConversationPutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    messages: list[MessageSnapshot] = Field(default_factory=list, max_length=30)
    expected_revision: int = Field(ge=0)
    mutation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("title", "model")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title and model must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_unique_message_ids(self):
        ids = [message.id for message in self.messages]
        if len(ids) != len(set(ids)):
            raise ValueError("message IDs must be unique within a conversation")
        return self


def install_web_chat_conversations(app: FastAPI) -> FastAPI:
    if getattr(app.state, "web_chat_conversations_installed", False):
        return app
    store = WebChatConversationStore(Path(app.state.settings.data_dir))
    app.state.web_chat_conversations = store
    app.state.web_chat_conversations_installed = True

    def principal(request: Request) -> str:
        accounts = getattr(request.app.state, "user_accounts", None)
        if accounts is None:
            _http_error(503, "user account service is unavailable")
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            _http_error(401, "user login required")
        return str(user["user_id"])

    def mutation_origin(request: Request) -> None:
        # The session is stored in a cookie. A strict Origin check prevents
        # cross-site writes, even if a browser misapplies SameSite policies.
        if not same_origin_admin_request(request):
            _http_error(403, "same-origin request required")

    def valid_id(conversation_id: str) -> str:
        if not _CONVERSATION_ID_RE.fullmatch(conversation_id):
            _http_error(422, "invalid conversation ID")
        return conversation_id

    def validate_attachment_ownership(owner: str, messages: list[MessageSnapshot]) -> None:
        file_store = getattr(app.state, "file_store", None)
        accounts = getattr(app.state, "user_accounts", None)
        for message in messages:
            if not isinstance(message.content, list):
                continue
            for part in message.content:
                if not isinstance(part, ImageContentPart):
                    continue
                file_id = part.image_url.url
                item = file_store.get(file_id) if file_store is not None else None
                if (
                    item is None
                    or item.purpose != "vision"
                    or not item.owner_key_id
                    or accounts is None
                    or not accounts.owns_key(owner, str(item.owner_key_id))
                ):
                    _http_error(404, "attachment not found")

    @app.get("/api/user/chat/conversations")
    async def list_conversations(
        request: Request,
        response: Response,
        limit: int = Query(default=30, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        q: str = Query(default="", max_length=120),
    ) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        result = store.list(principal(request), limit=limit, offset=offset, query=q)
        return result

    @app.get("/api/user/chat/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request, response: Response) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        row = store.get(principal(request), valid_id(conversation_id))
        if row is None:
            _http_error(404, "conversation not found")
        return row

    @app.put("/api/user/chat/conversations/{conversation_id}")
    async def put_conversation(
        conversation_id: str, body: ConversationPutBody, request: Request, response: Response
    ) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        owner = principal(request)
        mutation_origin(request)
        validate_attachment_ownership(owner, body.messages)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            _http_error(415, "application/json required")
        try:
            return store.put(
                owner, valid_id(conversation_id),
                title=body.title, model=body.model,
                messages=[message.model_dump() for message in body.messages],
                expected_revision=body.expected_revision, mutation_id=body.mutation_id,
            )
        except ConversationConflict as error:
            _http_error(409, str(error))
        except ConversationLimit as error:
            _http_error(409, str(error))

    @app.delete("/api/user/chat/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(
        conversation_id: str, request: Request,
        expected_revision: int = Query(ge=0),
    ) -> Response:
        owner = principal(request)
        mutation_origin(request)
        try:
            found = store.delete(owner, valid_id(conversation_id), expected_revision=expected_revision)
        except ConversationConflict as error:
            _http_error(409, str(error))
        if not found:
            _http_error(404, "conversation not found")
        return Response(status_code=204, headers={"Cache-Control": "private, no-store", "Vary": "Cookie"})

    return app
