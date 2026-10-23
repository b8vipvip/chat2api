"""In-process ASGI WebSocket probe for real Worker-hosted ChatGPT Voice calls.

Uses the actual FastAPI WebSocket route, broker, API Key and Chrome Worker.
No mock audio, no alternate TTS provider, no dependency on the public reverse
proxy. Only samples returned by the Worker after a confirmed input are counted.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


async def probe_voice_call(
    app: Any, *, token: str, request_id: str,
    attachments: list[dict[str, object]], pcm16: bytes = b"",
    text: str = "请在语音通话中用普通话自然地说：chat2api 语音通话测试成功。",
) -> dict[str, Any]:
    inbound: asyncio.Queue[dict] = asyncio.Queue()
    outbound: asyncio.Queue[dict] = asyncio.Queue()
    scope = {
        "type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "scheme": "ws", "http_version": "1.1", "path": "/v1/audio/realtime",
        "raw_path": b"/v1/audio/realtime", "query_string": b"",
        "root_path": "", "headers": [(b"authorization", f"Bearer {token}".encode())],
        "client": ("127.0.0.1", 12345), "server": ("chat2api.internal", 80),
        "subprotocols": [], "state": {},
    }

    async def receive() -> dict:
        return await inbound.get()

    async def send(event: dict) -> None:
        await outbound.put(event)

    worker = asyncio.create_task(app(scope, receive, send))

    async def read(timeout: float = 50) -> tuple[str, Any]:
        event = await asyncio.wait_for(outbound.get(), timeout=timeout)
        if event.get("type") == "websocket.close":
            raise RuntimeError(f"Live Voice socket closed: {event.get('reason') or event.get('code')}")
        if event.get("type") != "websocket.send":
            raise RuntimeError(f"Unexpected Voice websocket event: {event.get('type')}")
        if event.get("bytes") is not None:
            return "audio", event["bytes"]
        try:
            return "json", json.loads(event.get("text") or "{}")
        except ValueError as error:
            raise RuntimeError("Live Voice sent invalid JSON") from error

    async def write_json(value: dict) -> None:
        await inbound.put({"type": "websocket.receive", "text": json.dumps(value, ensure_ascii=False)})

    try:
        await inbound.put({"type": "websocket.connect"})
        accepted = await asyncio.wait_for(outbound.get(), timeout=5)
        if accepted.get("type") != "websocket.accept":
            raise RuntimeError(f"Live Voice WebSocket handshake failed: {accepted}")
        await write_json({
            "type": "session.start", "model": "gpt-live", "request_id": request_id,
            "instructions": "保持当前 ChatGPT 语音通话不中断，用语音回答用户的问题或上传的附件。",
        })
        start_deadline = asyncio.get_running_loop().time() + 55
        while True:
            remain = start_deadline - asyncio.get_running_loop().time()
            if remain <= 0:
                raise RuntimeError("Timed out waiting for real ChatGPT Voice session.ready")
            kind, data = await read(timeout=remain)
            if kind != "json":
                continue
            if data.get("type") == "error":
                raise RuntimeError(f"Voice call startup: {data.get('code')}: {data.get('message')}")
            if data.get("type") == "session.ready":
                break
        # Send exactly the uploaded real PCM16 16kHz mono samples, if available.
        # No synthetic silence is ever passed off as verified microphone speech.
        for index in range(0, len(pcm16), 3200):
            await inbound.put({"type": "websocket.receive", "bytes": pcm16[index:index + 3200]})
            await asyncio.sleep(.01)
        item_id = "voice_call_probe_" + request_id[-22:]
        if attachments:
            await write_json({
                "type": "conversation.item.create",
                "item": {
                    "id": item_id, "role": "user",
                    "content": [{"type": "input_text", "text": text}] + [
                        {"type": "input_image" if str(a.get("mime_type") or "").startswith("image/")
                            else "input_file", "file_id": a["id"]}
                        for a in attachments
                    ],
                },
            })
        else:
            await write_json({"type": "input_text", "item_id": item_id, "text": text})

        acknowledged = False
        first_audio = False
        audio_bytes = 0
        audio_frames = 0
        media_sent = False
        text_sent = False
        transcript = ""
        response_done = False
        deadline = asyncio.get_running_loop().time() + 105
        while asyncio.get_running_loop().time() < deadline:
            kind, data = await read(timeout=max(.5, deadline - asyncio.get_running_loop().time()))
            if kind == "audio":
                # Greeting or old activity before the actual request was
                # delivered is not evidence of this test's voice response.
                if acknowledged and data:
                    first_audio = True
                    audio_bytes += len(data)
                    audio_frames += 1
                continue
            event = str(data.get("type") or "")
            if event == "error":
                raise RuntimeError(f"Voice call input/stream: {data.get('code')}: {data.get('message')}")
            if event == "input_media.sent" and data.get("item_id") == item_id:
                media_sent = acknowledged = True
            if event == "input_text.sent" and data.get("item_id") == item_id:
                text_sent = acknowledged = True
            if event == "response.done" and acknowledged:
                response_done = True
                transcript = str(data.get("text") or "")[:400]
                if first_audio:
                    break
            if event == "session.closed":
                raise RuntimeError("ChatGPT Voice call ended before delivering the reply")
        if not acknowledged:
            raise RuntimeError("ChatGPT did not confirm the Voice call text/image/file turn")
        if audio_bytes < 512 or not response_done:
            raise RuntimeError(
                f"ChatGPT Voice did not deliver a completed nonempty PCM answer "
                f"(audio_bytes={audio_bytes}, audio_frames={audio_frames}, done={response_done})"
            )
        return {
            "audio_bytes": audio_bytes, "audio_frames": audio_frames,
            "transcript": transcript, "media_sent": media_sent, "text_sent": text_sent,
            "attachments_count": len(attachments), "input_pcm_bytes": len(pcm16),
            "transport": "chatgpt-web-voice-asgi-websocket-pcm",
            "reverse_proxy_not_tested": True,
        }
    finally:
        await inbound.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(worker, timeout=3)
        except (asyncio.TimeoutError, Exception):
            worker.cancel()
            try:
                await worker
            except (asyncio.CancelledError, Exception):
                pass
