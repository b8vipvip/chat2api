from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .api_keys import ApiPrincipal
from .models import PlaygroundRunRequest
from .timezone_utils import beijing_now_iso


PATCH_ID = "playground-lifecycle-v1"
TERMINAL_RUN_STATUSES = {"passed", "warning", "failed", "skipped", "cancelled", "stalled"}
TEST_KINDS = ["text", "vision", "file", "image_generation", "voice_generation", "voice_conversation"]
TEST_LABELS = {
    "text": "文本",
    "vision": "视觉理解",
    "file": "文件理解",
    "image_generation": "图片生成",
    "voice_generation": "语音生成",
    "voice_conversation": "语音对话",
}
logger = logging.getLogger("chat2api.playground")


def _response_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if isinstance(payload, dict) and payload.get("detail"):
        return str(payload["detail"])
    return str(response.text or f"HTTP {response.status_code}")[:1000]


async def _response_bytes(response: Response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


class PlaygroundRunManager:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.store = app.state.test_runs
        self.telemetry = app.state.telemetry
        self.api_keys = app.state.api_keys
        self.registry = app.state.registry
        self.broker = app.state.broker
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.cancel_requested: set[str] = set()

    async def _credential(self, body: PlaygroundRunRequest) -> tuple[str, ApiPrincipal, str]:
        if body.api_key is not None:
            token = body.api_key.get_secret_value().strip()
            source = "pasted"
        else:
            try:
                token = self.api_keys.reveal(str(body.api_key_id or ""))
            except KeyError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            source = "managed"
        principal = await self.api_keys.authenticate(token)
        if principal is None:
            raise HTTPException(status_code=401, detail="Invalid, disabled, expired, or revoked business API key")
        if body.api_key_id and principal.key_id != body.api_key_id:
            raise HTTPException(status_code=409, detail="Selected API key does not match the supplied credential")
        return token, principal, source

    @staticmethod
    def _kinds(test_type: str) -> list[str]:
        return list(TEST_KINDS) if test_type == "all" else [test_type]

    @staticmethod
    def _has_image(body: PlaygroundRunRequest) -> bool:
        return any(str(item.mime_type or "").lower().startswith("image/") for item in body.files)

    @staticmethod
    def _has_document(body: PlaygroundRunRequest) -> bool:
        return any(not str(item.mime_type or "").lower().startswith("image/") for item in body.files)

    def _planned_request_ids(self, body: PlaygroundRunRequest, kinds: list[str]) -> dict[str, str]:
        planned: dict[str, str] = {}
        for kind in kinds:
            if kind == "vision" and not self._has_image(body):
                continue
            if kind == "file" and not self._has_document(body):
                continue
            if kind in {"text", "vision", "file"}:
                planned[kind] = "req_" + uuid.uuid4().hex
            elif kind == "image_generation":
                planned[kind] = "imgreq_" + uuid.uuid4().hex
        return planned

    async def start(self, body: PlaygroundRunRequest) -> dict[str, Any]:
        token, principal, credential_source = await self._credential(body)
        run_id = "testrun_" + uuid.uuid4().hex
        kinds = self._kinds(body.test_type)
        request_ids = self._planned_request_ids(body, kinds)
        ordered_request_ids = [request_ids[kind] for kind in kinds if kind in request_ids]
        rows = [
            {
                "kind": kind,
                "label": TEST_LABELS.get(kind, kind),
                "status": "pending",
                "message": "等待执行",
                "request_id": request_ids.get(kind),
            }
            for kind in kinds
        ]
        now = beijing_now_iso()
        run = await self.store.upsert(
            {
                "run_id": run_id,
                "request_id": ordered_request_ids[0] if ordered_request_ids else None,
                "request_ids": ordered_request_ids,
                "test_type": body.test_type,
                "model": body.model,
                "api_key_id": principal.key_id,
                "api_key_name": principal.name,
                "status": "running",
                "started_at": now,
                "updated_at": now,
                "finished_at": None,
                "duration_ms": None,
                "error": None,
                "summary": "Playground run accepted",
                "results": rows,
                "quality": {
                    "api_key_id": principal.key_id,
                    "api_key_label": principal.name,
                    "api_key_source": credential_source,
                    "lifecycle": PATCH_ID,
                },
            }
        )
        logger.info(
            "Playground test accepted run_id=%s request_id=%s type=%s model=%s",
            run_id,
            run.get("request_id"),
            body.test_type,
            body.model,
            extra={"run_id": run_id, "request_id": run.get("request_id")},
        )
        task = asyncio.create_task(
            self._execute(run_id, body, token, request_ids),
            name=f"chat2api-playground-{run_id}",
        )
        self.tasks[run_id] = task
        task.add_done_callback(lambda completed, value=run_id: self._task_finished(value, completed))
        return run

    def _task_finished(self, run_id: str, task: asyncio.Task[None]) -> None:
        self.tasks.pop(run_id, None)
        if not task.cancelled():
            try:
                task.exception()
            except (asyncio.CancelledError, Exception):
                pass

    def _client(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
        return httpx.AsyncClient(transport=transport, base_url="http://chat2api.internal", timeout=None)

    async def _upload_files(
        self,
        client: httpx.AsyncClient,
        body: PlaygroundRunRequest,
        token: str,
    ) -> list[dict[str, Any]]:
        uploaded: list[dict[str, Any]] = []
        for item in body.files:
            response = await client.post(
                "/v1/files",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "filename": item.filename,
                    "mime_type": item.mime_type or "application/octet-stream",
                    "data_base64": item.data_base64,
                    "purpose": "chat2api",
                },
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Playground attachment upload failed: {_response_error(response)}")
            value = response.json()
            uploaded.append(
                {
                    "id": str(value.get("id") or ""),
                    "filename": item.filename,
                    "mime_type": item.mime_type or "application/octet-stream",
                }
            )
        return uploaded

    async def _cleanup_files(
        self,
        client: httpx.AsyncClient,
        uploaded: list[dict[str, Any]],
        token: str,
    ) -> None:
        for item in uploaded:
            file_id = str(item.get("id") or "")
            if not file_id:
                continue
            try:
                await client.delete(
                    f"/v1/files/{file_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            except Exception:
                logger.warning("Playground temporary file cleanup failed run_file_id=%s", file_id)

    @staticmethod
    def _file_for(uploaded: list[dict[str, Any]], want_image: bool) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in uploaded
                if str(item.get("mime_type") or "").lower().startswith("image/") is want_image
            ),
            None,
        )

    async def _run_chat(
        self,
        client: httpx.AsyncClient,
        *,
        kind: str,
        model: str,
        reasoning_effort: str | None,
        token: str,
        request_id: str,
        attachment: dict[str, Any] | None,
    ) -> dict[str, Any]:
        prompts = {
            "text": "请只回复：chat2api 文本测试成功",
            "vision": "请识别附件图片，并用一句话描述主要内容。",
            "file": "请阅读附件，并用一句话概括核心内容。",
        }
        request_body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompts[kind]}],
            "attachments": [{"file_id": attachment["id"]}] if attachment else [],
            "stream": True,
            "timeout": 300,
        }
        if model != "gpt-5.5-mini" and reasoning_effort:
            request_body["reasoning_effort"] = reasoning_effort
        started = time.perf_counter()
        response = await client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Chat2API-Request-ID": request_id,
            },
            json=request_body,
        )
        if response.status_code >= 400:
            raise RuntimeError(_response_error(response))
        first_token_ms: float | None = None
        response_chars = 0
        raw_error = ""
        meta: dict[str, Any] | None = None
        for line in response.text.splitlines():
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw == "[DONE]":
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if event.get("error"):
                raw_error = str((event.get("error") or {}).get("message") or "stream error")
                if isinstance(event.get("chat2api"), dict):
                    meta = event["chat2api"]
                continue
            content = str((((event.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""))
            if content:
                if first_token_ms is None:
                    first_token_ms = (time.perf_counter() - started) * 1000
                response_chars += len(content)
            if isinstance(event.get("chat2api"), dict):
                meta = event["chat2api"]
        telemetry = self.telemetry.get(request_id) or {}
        status_value = str(telemetry.get("status") or "")
        if raw_error:
            return {
                "kind": kind,
                "label": TEST_LABELS[kind],
                "status": status_value if status_value in {"cancelled", "stalled"} else "failed",
                "message": raw_error,
                "error": raw_error,
                "request_id": request_id,
                "total_ms": (time.perf_counter() - started) * 1000,
            }
        issues: list[str] = []
        if response_chars == 0:
            issues.append("没有捕获到文本输出")
        if not meta:
            issues.append("缺少 chat2api diagnostics")
        status = "warning" if issues else "passed"
        return {
            "kind": kind,
            "label": TEST_LABELS[kind],
            "status": status,
            "message": "调用完成" if not issues else "；".join(issues),
            "request_id": request_id,
            "first_token_ms": first_token_ms,
            "total_ms": (time.perf_counter() - started) * 1000,
            "response_chars": response_chars,
            "quality": {"grade": status, "issues": issues},
            "meta": meta,
        }

    async def _run_image(
        self,
        client: httpx.AsyncClient,
        *,
        token: str,
        request_id: str,
        attachment: dict[str, Any] | None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        response = await client.post(
            "/v1/images/generations",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Chat2API-Request-ID": request_id,
            },
            json={
                "model": "gpt-image",
                "prompt": (
                    "参考附件图片，生成一张风格明显不同但主体相关的新图片。"
                    if attachment
                    else "生成一张简洁的蓝天白云测试图片。"
                ),
                "response_format": "b64_json",
                "attachments": [{"file_id": attachment["id"]}] if attachment else [],
                "timeout": 600,
            },
        )
        if response.status_code >= 400:
            telemetry = self.telemetry.get(request_id) or {}
            status_value = str(telemetry.get("status") or "")
            message = _response_error(response)
            return {
                "kind": "image_generation",
                "label": TEST_LABELS["image_generation"],
                "status": status_value if status_value in {"cancelled", "stalled"} else "failed",
                "message": message,
                "error": message,
                "request_id": request_id,
                "total_ms": (time.perf_counter() - started) * 1000,
            }
        payload = response.json()
        image_count = len(payload.get("data") or [])
        if image_count < 1:
            raise RuntimeError("图片接口没有返回图片数据")
        return {
            "kind": "image_generation",
            "label": TEST_LABELS["image_generation"],
            "status": "passed",
            "message": "调用完成",
            "request_id": request_id,
            "total_ms": (time.perf_counter() - started) * 1000,
            "image_count": image_count,
            "meta": {"usage": payload.get("usage"), "chat2api": payload.get("chat2api")},
        }

    async def _run_kind(
        self,
        client: httpx.AsyncClient,
        *,
        kind: str,
        body: PlaygroundRunRequest,
        token: str,
        request_id: str | None,
        uploaded: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if kind in {"voice_generation", "voice_conversation"}:
            return {
                "kind": kind,
                "label": TEST_LABELS[kind],
                "status": "skipped",
                "message": "当前测试场尚未接入该语音用例",
                "reason": "当前测试场尚未接入该语音用例",
                "request_id": None,
            }
        if kind == "vision":
            attachment = self._file_for(uploaded, True)
            if attachment is None:
                return {
                    "kind": kind,
                    "label": TEST_LABELS[kind],
                    "status": "skipped",
                    "message": "未选择图片附件",
                    "reason": "未选择图片附件",
                    "request_id": None,
                }
        elif kind == "file":
            attachment = self._file_for(uploaded, False)
            if attachment is None:
                return {
                    "kind": kind,
                    "label": TEST_LABELS[kind],
                    "status": "skipped",
                    "message": "未选择文档/文件附件",
                    "reason": "未选择文档/文件附件",
                    "request_id": None,
                }
        else:
            attachment = self._file_for(uploaded, True) if kind == "image_generation" else None
        if not request_id:
            raise RuntimeError(f"Missing planned request ID for playground test: {kind}")
        if kind == "image_generation":
            return await self._run_image(
                client,
                token=token,
                request_id=request_id,
                attachment=attachment,
            )
        return await self._run_chat(
            client,
            kind=kind,
            model=body.model,
            reasoning_effort=body.reasoning_effort,
            token=token,
            request_id=request_id,
            attachment=attachment,
        )

    async def _execute(
        self,
        run_id: str,
        body: PlaygroundRunRequest,
        token: str,
        request_ids: dict[str, str],
    ) -> None:
        started = time.perf_counter()
        uploaded: list[dict[str, Any]] = []
        try:
            async with self._client() as client:
                uploaded = await self._upload_files(client, body, token)
                run = self.store.get(run_id) or {}
                results = list(run.get("results") or [])
                for index, kind in enumerate(self._kinds(body.test_type)):
                    if run_id in self.cancel_requested:
                        break
                    current = dict(results[index])
                    current.update({"status": "running", "message": "执行中"})
                    results[index] = current
                    await self.store.update(
                        run_id,
                        {
                            "request_id": request_ids.get(kind) or (self.store.get(run_id) or {}).get("request_id"),
                            "status": "running",
                            "summary": f"Executing {kind}",
                            "results": results,
                        },
                    )
                    try:
                        result = await self._run_kind(
                            client,
                            kind=kind,
                            body=body,
                            token=token,
                            request_id=request_ids.get(kind),
                            uploaded=uploaded,
                        )
                    except Exception as error:
                        message = str(error)
                        telemetry = self.telemetry.get(str(request_ids.get(kind) or "")) or {}
                        status_value = str(telemetry.get("status") or "")
                        result = {
                            "kind": kind,
                            "label": TEST_LABELS.get(kind, kind),
                            "status": status_value if status_value in {"cancelled", "stalled"} else "failed",
                            "message": message,
                            "error": message,
                            "request_id": request_ids.get(kind),
                        }
                    results[index] = result
                    await self.store.update(run_id, {"results": results, "summary": f"Finished {kind}"})
                if run_id in self.cancel_requested:
                    await self._finalize_cancelled(run_id, started)
                else:
                    await self._finalize(run_id, results, started)
        except Exception as error:
            if run_id in self.cancel_requested:
                await self._finalize_cancelled(run_id, started)
            else:
                message = str(error)
                await self.store.update(
                    run_id,
                    {
                        "status": "failed",
                        "finished_at": beijing_now_iso(),
                        "duration_ms": (time.perf_counter() - started) * 1000,
                        "error": message,
                        "summary": message,
                    },
                )
                logger.exception("Playground run failed run_id=%s", run_id, extra={"run_id": run_id})
        finally:
            if uploaded:
                try:
                    async with self._client() as cleanup_client:
                        await self._cleanup_files(cleanup_client, uploaded, token)
                except Exception:
                    logger.warning("Playground temporary file cleanup failed run_id=%s", run_id)
            self.cancel_requested.discard(run_id)

    async def _finalize(self, run_id: str, results: list[dict[str, Any]], started: float) -> None:
        statuses = [str(item.get("status") or "") for item in results]
        if "stalled" in statuses:
            status = "stalled"
        elif "failed" in statuses:
            status = "failed"
        elif "warning" in statuses:
            status = "warning"
        elif "passed" in statuses:
            status = "passed"
        else:
            status = "skipped"
        counts = {name: statuses.count(name) for name in {"passed", "warning", "failed", "skipped", "stalled"}}
        summary = ", ".join(f"{counts[name]} {name}" for name in ("passed", "warning", "failed", "skipped", "stalled"))
        error = next((str(item.get("error") or "") for item in results if item.get("error")), None)
        await self.store.update(
            run_id,
            {
                "status": status,
                "finished_at": beijing_now_iso(),
                "duration_ms": (time.perf_counter() - started) * 1000,
                "summary": summary,
                "error": error,
                "results": results,
            },
        )
        logger.info(
            "Playground run finalized run_id=%s status=%s",
            run_id,
            status,
            extra={"run_id": run_id, "status": status},
        )

    async def _finalize_cancelled(self, run_id: str, started: float) -> None:
        row = self.store.get(run_id) or {}
        results = []
        for item in list(row.get("results") or []):
            value = dict(item)
            if value.get("status") in {"pending", "running"}:
                value.update({"status": "cancelled", "message": "用户取消测试"})
            results.append(value)
        await self.store.update(
            run_id,
            {
                "status": "cancelled",
                "finished_at": beijing_now_iso(),
                "duration_ms": (time.perf_counter() - started) * 1000,
                "summary": "Playground run cancelled by administrator",
                "error": None,
                "results": results,
            },
        )
        logger.info("Playground run finalized run_id=%s status=cancelled", run_id, extra={"run_id": run_id})

    async def _force_release_cancelled(self, request_id: str, state: Any) -> None:
        await asyncio.sleep(2.0)
        if self.broker.requests.get(request_id) is state:
            await self.broker.release(request_id)
            logger.warning(
                "Cancelled playground request force-released request_id=%s client=%s",
                request_id,
                state.client_id,
                extra={"request_id": request_id, "client_id": state.client_id},
            )

    async def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.store.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Test run not found")
        if str(run.get("status") or "") in TERMINAL_RUN_STATUSES:
            return run
        self.cancel_requested.add(run_id)
        results = []
        for item in list(run.get("results") or []):
            value = dict(item)
            if value.get("status") in {"pending", "running"}:
                value.update({"status": "cancelled", "message": "用户取消测试"})
            results.append(value)
        await self.store.update(
            run_id,
            {
                "status": "cancelled",
                "finished_at": beijing_now_iso(),
                "summary": "Cancellation requested",
                "results": results,
            },
        )
        for request_id in list(run.get("request_ids") or []):
            state = self.broker.requests.get(str(request_id))
            if state is None:
                continue
            state.diagnostics["playground_cancelled"] = True
            is_image = str(request_id).startswith("imgreq_")
            cancel_type = "image.cancel" if is_image else "chat.cancel"
            event_type = "image.cancelled" if is_image else "chat.cancelled"
            try:
                await self.registry.send(state.client_id, {"type": cancel_type, "request_id": request_id})
            except Exception:
                pass
            await self.broker.publish(
                request_id,
                {"type": event_type, "request_id": request_id, "reason": "Playground test cancelled by administrator"},
            )
            asyncio.create_task(self._force_release_cancelled(request_id, state))
        return self.store.get(run_id) or run


def install_playground_lifecycle_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "playground_lifecycle_patch_installed", False):
        return app
    app.state.playground_lifecycle_patch_installed = True
    manager = PlaygroundRunManager(app)
    app.state.playground_run_manager = manager

    @app.get("/assets/chat2api-playground-lifecycle.js", include_in_schema=False)
    async def playground_lifecycle_js() -> Response:
        path = Path(__file__).with_name("admin_playground_lifecycle.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/admin/playground/runs")
    async def start_playground_run(body: PlaygroundRunRequest) -> dict[str, Any]:
        return {"run": await manager.start(body)}

    @app.post("/api/admin/playground/runs/{run_id}/cancel")
    async def cancel_playground_run(run_id: str) -> dict[str, Any]:
        return {"run": await manager.cancel(run_id)}

    @app.middleware("http")
    async def playground_lifecycle_console(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")
        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-playground-lifecycle.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)
        if path == "/api/admin/overview" and "application/json" in content_type:
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                capabilities = payload.setdefault("capabilities", {})
                if isinstance(capabilities, dict):
                    capabilities.update(
                        {
                            "persistent_playground_runs": True,
                            "playground_cancellation": True,
                            "running_request_history": True,
                            "generation_activity_watchdog": True,
                        }
                    )
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            return JSONResponse(payload, status_code=response.status_code, headers=headers)
        return response

    return app
