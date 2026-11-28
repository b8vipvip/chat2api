"""D-01/D-02: user-owned Dot profiles and explicit, durable model runs.

The first slice is deliberately manual. No stored session credentials, automatic
replay of ambiguous requests, external tools, or host execution.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .admin_auth import same_origin_admin_request
from .models import ChatCompletionRequest
from .web_chat_identity_v1 import resolve_web_chat_principal, web_chat_model_decision

PRIVATE = {"Cache-Control": "private, no-store", "Vary": "Cookie"}
LEASE_SECONDS = 1800


class DotProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=4000)


class DotGoal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    goal: str = Field(min_length=1, max_length=8000)
    model: str = Field(min_length=1, max_length=120)
    idempotency_key: uuid.UUID


class DotStore:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "dot_v1.sqlite3"
        with self.tx() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS dot_profiles (
                owner TEXT PRIMARY KEY, name TEXT NOT NULL, instructions TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS dot_tasks (
                owner TEXT NOT NULL, id TEXT NOT NULL, goal TEXT NOT NULL,
                model TEXT NOT NULL, instructions TEXT NOT NULL, status TEXT NOT NULL,
                request_id TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
                updated REAL NOT NULL, lease_until REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(owner,id))""")
            db.execute("CREATE INDEX IF NOT EXISTS dot_owner_created ON dot_tasks(owner,created)")

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def profile(self, owner, profile=None):
        with self.tx() as db:
            if profile is not None:
                db.execute("""INSERT INTO dot_profiles VALUES(?,?,?)
                    ON CONFLICT(owner) DO UPDATE SET name=excluded.name,
                    instructions=excluded.instructions""", (owner, profile.name, profile.instructions))
            row = db.execute("SELECT name,instructions FROM dot_profiles WHERE owner=?", (owner,)).fetchone()
            return dict(row) if row else {"name": "我的 Dot", "instructions": ""}

    def _expire(self, db, owner, now):
        db.execute("""UPDATE dot_tasks SET status='interrupted', updated=?,
            error='执行租约已过期；请核对原请求记录，不会自动重复执行'
            WHERE owner=? AND status='running' AND lease_until<=?""", (now, owner, now))

    def list(self, owner):
        with self.tx() as db:
            self._expire(db, owner, time.time())
            return [dict(row) for row in db.execute(
                "SELECT * FROM dot_tasks WHERE owner=? ORDER BY created DESC LIMIT 100", (owner,))]

    def create(self, owner, body):
        now = time.time()
        task_id = str(body.idempotency_key)
        with self.tx() as db:
            old = db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
            if old:
                if old["goal"] != body.goal or old["model"] != body.model:
                    raise ValueError("idempotency key already used")
                return dict(old)
            if db.execute("SELECT count(*) FROM dot_tasks WHERE owner=?", (owner,)).fetchone()[0] >= 100:
                raise ValueError("task limit reached; delete terminal tasks first")
            profile = db.execute("SELECT instructions FROM dot_profiles WHERE owner=?", (owner,)).fetchone()
            db.execute("""INSERT INTO dot_tasks(owner,id,goal,model,instructions,status,created,updated)
                VALUES(?,?,?,?,?,'queued',?,?)""",
                (owner, task_id, body.goal, body.model, profile[0] if profile else "", now, now))
            return dict(db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone())

    def claim(self, owner, task_id):
        now = time.time()
        with self.tx() as db:
            self._expire(db, owner, now)
            row = db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
            if not row:
                raise KeyError(task_id)
            if row["status"] != "queued":
                raise ValueError("task is not queued")
            if db.execute("SELECT 1 FROM dot_tasks WHERE owner=? AND status='running'", (owner,)).fetchone():
                raise ValueError("another task is running")
            request_id = "req_" + uuid.uuid4().hex
            db.execute("""UPDATE dot_tasks SET status='running', request_id=?, updated=?, lease_until=?
                WHERE owner=? AND id=?""", (request_id, now, now + LEASE_SECONDS, owner, task_id))
            return dict(db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone())

    def finish(self, owner, task_id, request_id, status, result="", error=""):
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError("invalid terminal status")
        with self.tx() as db:
            changed = db.execute("""UPDATE dot_tasks SET status=?,result=?,error=?,updated=?,lease_until=0
                WHERE owner=? AND id=? AND request_id=? AND status='running'""",
                (status, result[:65536], error[:500], time.time(), owner, task_id, request_id)).rowcount
            if not changed:
                raise ValueError("task lease no longer owns the terminal transition")
            return dict(db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone())

    def remove(self, owner, task_id):
        with self.tx() as db:
            self._expire(db, owner, time.time())
            row = db.execute("SELECT status FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
            if not row:
                raise KeyError(task_id)
            if row["status"] == "running":
                raise ValueError("running task cannot be deleted")
            db.execute("DELETE FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id))


def install_dot_v1(app: FastAPI):
    if getattr(app.state, "dot_v1_installed", False):
        return
    store = DotStore(Path(app.state.settings.data_dir))
    app.state.dot_store = store

    async def identity(request, mutate=False):
        if mutate and not same_origin_admin_request(request):
            raise HTTPException(403, "Same-origin request required", headers=PRIVATE)
        user, principal = await resolve_web_chat_principal(request)
        if user.get("is_guest"):
            raise HTTPException(403, "Dot requires a registered account", headers=PRIVATE)
        owner = str(user["user_id"])
        if mutate and request.headers.get("X-Chat2API-User-ID") != owner:
            raise HTTPException(409, "Account changed; reload Dot", headers=PRIVATE)
        return owner, principal

    def response(data):
        # Never expose the internal ownership namespace or lease credentials.
        def clean(row):
            return {k: v for k, v in row.items() if k not in {"owner", "lease_until"}}
        return JSONResponse({"data": [clean(x) for x in data] if isinstance(data, list) else clean(data)}, headers=PRIVATE)

    def model_allowed(request, principal, model):
        if model.lower() in {"gpt-image", "gpt-live", "gpt-live-mini", "chatgpt-web"}:
            raise HTTPException(422, "Dot only supports text models", headers=PRIVATE)
        if web_chat_model_decision(request.app, principal, model).get("allowed") is not True:
            raise HTTPException(403, "Model not entitled", headers=PRIVATE)

    def conflict(error):
        return HTTPException(404 if isinstance(error, KeyError) else 409,
                             "Task not found" if isinstance(error, KeyError) else str(error),
                             headers=PRIVATE)

    @app.get("/chat/dot", include_in_schema=False)
    async def dot_page(request: Request):
        await identity(request)
        return HTMLResponse(Path(__file__).with_name("dot_v1.html").read_text(encoding="utf-8"),
                            headers={**PRIVATE, "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})

    @app.get("/assets/chat2api-dot-v1.js", include_in_schema=False)
    async def dot_asset():
        return Response(Path(__file__).with_name("dot_v1.js").read_bytes(),
                        media_type="application/javascript",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/user/chat/dot")
    async def dot_state(request: Request):
        owner, _ = await identity(request)
        profile = await asyncio.to_thread(store.profile, owner)
        tasks = await asyncio.to_thread(store.list, owner)
        return JSONResponse({"user_id": owner, "profile": profile,
                             "tasks": [{k: v for k, v in row.items() if k not in {"owner", "lease_until"}} for row in tasks]}, headers=PRIVATE)

    @app.put("/api/user/chat/dot/profile")
    async def dot_profile(body: DotProfile, request: Request):
        owner, _ = await identity(request, True)
        return response(await asyncio.to_thread(store.profile, owner, body))

    @app.post("/api/user/chat/dot/tasks")
    async def dot_create(body: DotGoal, request: Request):
        owner, principal = await identity(request, True)
        model_allowed(request, principal, body.model)
        try:
            return response(await asyncio.to_thread(store.create, owner, body))
        except ValueError as error:
            raise conflict(error) from error

    @app.post("/api/user/chat/dot/tasks/{task_id}/run")
    async def dot_run(task_id: uuid.UUID, request: Request):
        owner, principal = await identity(request, True)
        adapter = getattr(app.state, "chat_completions_adapter", None)
        if not callable(adapter):
            raise HTTPException(503, "Model service unavailable", headers=PRIVATE)
        try:
            task = await asyncio.to_thread(store.claim, owner, str(task_id))
        except (KeyError, ValueError) as error:
            raise conflict(error) from error
        rid = task["request_id"]
        try:
            model_allowed(request, principal, task["model"])
            messages = []
            if task["instructions"]:
                messages.append({"role": "system", "content": task["instructions"]})
            messages.append({"role": "user", "content": task["goal"]})
            body = ChatCompletionRequest(model=task["model"], messages=messages, stream=False, timeout=120)
            request.scope["chat2api_mode1_web_chat"] = True
            request.scope["chat2api_web_chat_user_id"] = owner
            result = await adapter(body, request, None, rid, principal)
            if not isinstance(result, JSONResponse) or result.status_code != 200:
                raise RuntimeError("Unexpected model response")
            content = json.loads(result.body)["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("Empty model response")
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(store.finish, owner, str(task_id), rid,
                                                   "interrupted", "", "Request interrupted; inspect original request"))
            raise
        except HTTPException as error:
            return response(await asyncio.to_thread(store.finish, owner, str(task_id), rid,
                                                    "failed", "", f"Model request failed (HTTP {error.status_code})"))
        except Exception:
            return response(await asyncio.to_thread(store.finish, owner, str(task_id), rid,
                                                    "interrupted", "", "Model outcome uncertain; inspect original request"))
        return response(await asyncio.to_thread(store.finish, owner, str(task_id), rid, "completed", content))

    @app.delete("/api/user/chat/dot/tasks/{task_id}")
    async def dot_delete(task_id: uuid.UUID, request: Request):
        owner, _ = await identity(request, True)
        try:
            await asyncio.to_thread(store.remove, owner, str(task_id))
        except (KeyError, ValueError) as error:
            raise conflict(error) from error
        return JSONResponse({"ok": True}, headers=PRIVATE)

    app.state.dot_v1_installed = True
