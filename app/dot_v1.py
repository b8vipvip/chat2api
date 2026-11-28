"""D-01/D-02: user-owned Dot profiles and explicit, durable model runs.

Durable explicit submissions run independently of the HTTP connection. No stored
session credentials, automatic replay of ambiguous requests or external tools.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .admin_auth import same_origin_admin_request
from .models import ChatCompletionRequest
from .web_chat_identity_v1 import (
    resolve_dot_task_principal, resolve_web_chat_principal, web_chat_model_decision,
)
from .web_chat_recovery_api import _completed_delivery_text

logger = logging.getLogger(__name__)

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


class DotSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delay_seconds: int = Field(default=0, ge=0, le=30 * 86400)


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
            columns = {row[1] for row in db.execute("PRAGMA table_info(dot_tasks)")}
            if "run_at" not in columns:
                db.execute("ALTER TABLE dot_tasks ADD COLUMN run_at REAL NOT NULL DEFAULT 0")
            if "stop_requested" not in columns:
                db.execute("ALTER TABLE dot_tasks ADD COLUMN stop_requested INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE INDEX IF NOT EXISTS dot_owner_created ON dot_tasks(owner,created)")
            db.execute("CREATE INDEX IF NOT EXISTS dot_due ON dot_tasks(status,run_at,created)")

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

    def claim(self, owner, task_id, *, scheduled=False):
        now = time.time()
        with self.tx() as db:
            row = db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
            if not row:
                raise KeyError(task_id)
            if row["status"] != ("scheduled" if scheduled else "queued") or (scheduled and row["run_at"] > now):
                raise ValueError("task is not queued")
            if db.execute("SELECT 1 FROM dot_tasks WHERE owner=? AND status='running'", (owner,)).fetchone():
                raise ValueError("another task is running")
            request_id = "req_" + uuid.uuid4().hex
            db.execute("""UPDATE dot_tasks SET status='running', request_id=?, updated=?, lease_until=?
                WHERE owner=? AND id=?""", (request_id, now, now + LEASE_SECONDS, owner, task_id))
            return dict(db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone())

    def finish(self, owner, task_id, request_id, status, result="", error=""):
        if status not in {"completed", "failed", "interrupted", "cancelled"}:
            raise ValueError("invalid terminal status")
        with self.tx() as db:
            changed = db.execute("""UPDATE dot_tasks SET status=?,result=?,error=?,updated=?,lease_until=0
                WHERE owner=? AND id=? AND request_id=? AND status='running'""",
                (status, result[:65536], error[:500], time.time(), owner, task_id, request_id)).rowcount
            if not changed:
                raise ValueError("task lease no longer owns the terminal transition")
            return dict(db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone())

    def schedule(self, owner, task_id, delay_seconds=0):
        with self.tx() as db:
            row = db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
            if not row:
                raise KeyError(task_id)
            if row["status"] != "queued":
                raise ValueError("task is not queued")
            now = time.time()
            db.execute("UPDATE dot_tasks SET status='scheduled',run_at=?,updated=? WHERE owner=? AND id=?",
                       (now + delay_seconds, now, owner, task_id))
            return dict(db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone())

    def control(self, owner, task_id, action):
        with self.tx() as db:
            row = db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
            if not row:
                raise KeyError(task_id)
            status = row["status"]
            stop = row["stop_requested"]
            if action == "pause" and status == "scheduled":
                status = "paused"
            elif action == "resume" and status == "paused":
                status = "scheduled"
            elif action == "stop" and status in {"queued", "scheduled", "paused"}:
                status = "cancelled"
            elif action == "stop" and status == "running":
                stop = 1
            else:
                raise ValueError("invalid task transition")
            db.execute("UPDATE dot_tasks SET status=?,stop_requested=?,updated=? WHERE owner=? AND id=?",
                       (status, stop, time.time(), owner, task_id))
            return dict(db.execute("SELECT * FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone())

    def due(self):
        with self.tx() as db:
            rows = db.execute("""SELECT owner,id FROM dot_tasks AS t
                WHERE status='scheduled' AND run_at<=?
                AND NOT EXISTS (SELECT 1 FROM dot_tasks AS active WHERE active.owner=t.owner AND active.status='running')
                ORDER BY run_at,created LIMIT 16""", (time.time(),)).fetchall()
            return [dict(row) for row in rows]

    def active(self):
        with self.tx() as db:
            return [dict(row) for row in db.execute("SELECT * FROM dot_tasks WHERE status='running'")]

    def stopped(self, owner, task_id, rid):
        with self.tx() as db:
            row = db.execute("SELECT stop_requested FROM dot_tasks WHERE owner=? AND id=? AND request_id=? AND status='running'",
                             (owner, task_id, rid)).fetchone()
            return not row or bool(row[0])

    def remove(self, owner, task_id):
        with self.tx() as db:
            row = db.execute("SELECT status FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id)).fetchone()
            if not row:
                raise KeyError(task_id)
            if row["status"] in {"running", "scheduled", "paused"}:
                raise ValueError("active task cannot be deleted; stop it first")
            db.execute("DELETE FROM dot_tasks WHERE owner=? AND id=?", (owner, task_id))


class DotRuntime:
    """One bounded executor per app lifespan; SQLite claims fence dispatch.

    Requests already dispatched at crash are reconciled, never blindly replayed.
    A persisted submission that has not been claimed survives restart.
    """
    def __init__(self, app, store):
        self.app = app
        self.store = store
        self.jobs = {}
        self.loop_task = None

    async def start(self):
        self.loop_task = asyncio.create_task(self.loop())

    async def close(self):
        if self.loop_task:
            self.loop_task.cancel()
            await asyncio.gather(self.loop_task, return_exceptions=True)
            self.loop_task = None
        jobs = list(self.jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self.jobs.clear()

    async def finish(self, task, status, result="", error=""):
        try:
            return await asyncio.to_thread(self.store.finish, task["owner"], task["id"],
                                           task["request_id"], status, result, error)
        except ValueError:
            # Another reconciler has committed a terminal state.
            return None

    async def run(self, task):
        owner, rid = task["owner"], task["request_id"]
        try:
            if await asyncio.to_thread(self.store.stopped, owner, task["id"], rid):
                await self.finish(task, "cancelled")
                return
            _, principal = await resolve_dot_task_principal(self.app, owner)
            check_dot_model(self.app, principal, task["model"])
            adapter = getattr(self.app.state, "chat_completions_adapter", None)
            if not callable(adapter):
                raise HTTPException(503)
            messages = []
            if task["instructions"]:
                messages.append({"role": "system", "content": task["instructions"]})
            messages.append({"role": "user", "content": task["goal"]})
            body = ChatCompletionRequest(model=task["model"], messages=messages, stream=False, timeout=120)
            # No client headers, cookies or request receive channel survive.
            request = Request({"type": "http", "method": "POST", "scheme": "http",
                               "path": "/api/user/chat/dot/internal", "headers": [],
                               "query_string": b"", "app": self.app,
                               "chat2api_mode1_web_chat": True,
                               "chat2api_web_chat_user_id": owner})
            if await asyncio.to_thread(self.store.stopped, owner, task["id"], rid):
                await self.finish(task, "cancelled")
                return
            result = await adapter(body, request, None, rid, principal)
            if not isinstance(result, JSONResponse) or result.status_code != 200:
                raise RuntimeError("Unexpected model response")
            content = json.loads(result.body)["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("Empty model response")
            await self.finish(task, "completed", content)
        except asyncio.CancelledError:
            # The adapter may have detached an in-flight Worker. Keep the
            # durable request ID for next startup's terminal reconciliation.
            raise
        except HTTPException as error:
            # If an actual Worker request exists, use its terminal evidence.
            if not await self.reconcile(task):
                telemetry = getattr(self.app.state, "telemetry", None)
                if telemetry is None or telemetry.get(rid) is None:
                    await self.finish(task, "failed", error=f"Model request failed (HTTP {error.status_code})")
        except Exception:
            logger.exception("Dot model execution failed request_id=%s", rid)
            await self.reconcile(task)

    async def reconcile(self, task):
        rid = task["request_id"]
        telemetry = getattr(self.app.state, "telemetry", None)
        accounts = getattr(self.app.state, "user_accounts", None)
        row = telemetry.get(rid) if telemetry else None
        if row is None and telemetry is not None:
            row = (await asyncio.to_thread(telemetry.get_many_persisted, {rid})).get(rid)
        if row and accounts and accounts.owns_key(task["owner"], str(row.get("api_key_id") or "")) and row.get("web_chat_mode") is True:
            status = row.get("status")
            if status == "completed":
                journal = getattr(self.app.state, "web_chat_event_journal", None)
                snapshot = await journal.snapshot(rid, row["api_key_id"]) if journal else {}
                text = _completed_delivery_text(row, snapshot)
                if text:
                    await self.finish(task, "completed", text)
                    return True
            elif status in {"cancelled", "error", "stalled"}:
                await self.finish(task, {"cancelled":"cancelled", "error":"failed", "stalled":"interrupted"}[status],
                                  error="Original Worker request: " + status)
                return True
        if task["lease_until"] <= time.time():
            broker = getattr(self.app.state, "broker", None)
            if broker and rid in broker.requests:
                return False
            await self.finish(task, "interrupted", error="Execution outcome unavailable; not replayed")
            return True
        return False

    async def signal_stop(self, task):
        """Signal the existing admission/Worker cancellation path, not a fake terminal."""
        rid = task["request_id"]
        lock = getattr(self.app.state, "web_chat_cancel_lock", None)
        if lock is None:
            return
        async with lock:
            signal = getattr(self.app.state, "web_chat_cancel_events", {}).get(rid)
            broker = getattr(self.app.state, "broker", None)
            state = broker.requests.get(rid) if broker else None
            if state is None:
                if signal is not None:
                    signal.requested.set()
                return
            accounts = getattr(self.app.state, "user_accounts", None)
            if (not accounts or not accounts.owns_key(task["owner"], getattr(state, "mode1_owner_key_id", ""))
                    or getattr(state, "terminal_event_type", None)):
                if signal is not None:
                    signal.requested.set()
                return
            if state.diagnostics.get("web_chat_cancel_requested"):
                return
            state.diagnostics["web_chat_cancel_requested"] = True
            try:
                await self.app.state.registry.send(state.client_id, {"type":"chat.cancel", "request_id":rid})
            except Exception:
                state.diagnostics.pop("web_chat_cancel_requested", None)

    async def tick(self):
        for rid, job in list(self.jobs.items()):
            if job.done():
                self.jobs.pop(rid)
                if not job.cancelled():
                    job.exception()
        for task in await asyncio.to_thread(self.store.active):
            rid = task["request_id"]
            if task["stop_requested"]:
                await self.signal_stop(task)
            if rid not in self.jobs:
                await self.reconcile(task)
        if self.jobs:
            return
        for candidate in await asyncio.to_thread(self.store.due):
            try:
                task = await asyncio.to_thread(self.store.claim, candidate["owner"], candidate["id"], scheduled=True)
            except (ValueError, KeyError):
                continue
            self.jobs[task["request_id"]] = asyncio.create_task(self.run(task))
            break

    async def loop(self):
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Dot scheduler tick failed")
            await asyncio.sleep(1)


def check_dot_model(app, principal, model):
    if model.lower() in {"gpt-image", "gpt-live", "gpt-live-mini", "chatgpt-web"}:
        raise HTTPException(422, "Dot only supports text models", headers=PRIVATE)
    if web_chat_model_decision(app, principal, model).get("allowed") is not True:
        raise HTTPException(403, "Model not entitled", headers=PRIVATE)


def install_dot_v1(app: FastAPI):
    if getattr(app.state, "dot_v1_installed", False):
        return
    store = DotStore(Path(app.state.settings.data_dir))
    app.state.dot_store = store
    runtime = DotRuntime(app, store)
    app.state.dot_runtime = runtime
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(inner):
        async with previous_lifespan(inner) as state:
            await runtime.start()
            try:
                yield state
            finally:
                await runtime.close()

    app.router.lifespan_context = lifespan

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
        check_dot_model(request.app, principal, body.model)
        try:
            return response(await asyncio.to_thread(store.create, owner, body))
        except ValueError as error:
            raise conflict(error) from error

    @app.post("/api/user/chat/dot/tasks/{task_id}/run")
    async def dot_run(task_id: uuid.UUID, request: Request, body: DotSchedule | None = None):
        owner, _ = await identity(request, True)
        try:
            return response(await asyncio.to_thread(store.schedule, owner, str(task_id),
                                                     body.delay_seconds if body else 0))
        except (KeyError, ValueError) as error:
            raise conflict(error) from error

    @app.post("/api/user/chat/dot/tasks/{task_id}/control/{action}")
    async def dot_control(task_id: uuid.UUID, action: str, request: Request):
        owner, _ = await identity(request, True)
        try:
            return response(await asyncio.to_thread(store.control, owner, str(task_id), action))
        except (KeyError, ValueError) as error:
            raise conflict(error) from error

    @app.delete("/api/user/chat/dot/tasks/{task_id}")
    async def dot_delete(task_id: uuid.UUID, request: Request):
        owner, _ = await identity(request, True)
        try:
            await asyncio.to_thread(store.remove, owner, str(task_id))
        except (KeyError, ValueError) as error:
            raise conflict(error) from error
        return JSONResponse({"ok": True}, headers=PRIVATE)

    app.state.dot_v1_installed = True
