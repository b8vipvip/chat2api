"""W-02 Web Chat conversation API, separated from Mode 2 request execution."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

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


class MessageSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)
    createdAt: str = Field(default="", max_length=48)


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
            raise HTTPException(status_code=503, detail="user account service is unavailable")
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            raise HTTPException(status_code=401, detail="user login required")
        return str(user["user_id"])

    def mutation_origin(request: Request) -> None:
        # The session is stored in a cookie. A strict Origin check prevents
        # cross-site writes, even if a browser misapplies SameSite policies.
        if not same_origin_admin_request(request):
            raise HTTPException(status_code=403, detail="same-origin request required")

    def valid_id(conversation_id: str) -> str:
        if not _CONVERSATION_ID_RE.fullmatch(conversation_id):
            raise HTTPException(status_code=422, detail="invalid conversation ID")
        return conversation_id

    @app.get("/api/user/chat/conversations")
    async def list_conversations(
        request: Request,
        response: Response,
        limit: int = Query(default=30, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        result = store.list(principal(request), limit=limit, offset=offset)
        return result

    @app.get("/api/user/chat/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request, response: Response) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        row = store.get(principal(request), valid_id(conversation_id))
        if row is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return row

    @app.put("/api/user/chat/conversations/{conversation_id}")
    async def put_conversation(
        conversation_id: str, body: ConversationPutBody, request: Request, response: Response
    ) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        owner = principal(request)
        mutation_origin(request)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(status_code=415, detail="application/json required")
        try:
            return store.put(
                owner, valid_id(conversation_id),
                title=body.title, model=body.model,
                messages=[message.model_dump() for message in body.messages],
                expected_revision=body.expected_revision, mutation_id=body.mutation_id,
            )
        except ConversationConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ConversationLimit as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

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
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not found:
            raise HTTPException(status_code=404, detail="conversation not found")
        return Response(status_code=204, headers={"Cache-Control": "private, no-store"})

    return app
