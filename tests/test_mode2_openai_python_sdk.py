"""Real Python OpenAI SDK -> Chat2API ASGI app interoperability.

The fake registry replaces only the external browser/Worker transport, not the
FastAPI auth, serialization, request admission, Broker or SSE adapter. This
suite is executed with the real openai package by mode2-sdk-contract.yml.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.responses_v108_patch import install_responses_v108_patch

openai = pytest.importorskip("openai")

MASTER = "mode2-real-sdk-regression-admin"
EXPECTED_WORKER = "ext_mode2_sdk_fake"


@pytest.fixture
def sdk_app(tmp_path: Path):
    app = create_app(Settings(
        CHAT2API_API_KEY=MASTER,
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_responses_v108_patch(app)
    commands: list[dict] = []
    app.state.registry.resolve_client = lambda requested: EXPECTED_WORKER

    async def fake_worker_send(client_id: str, payload: dict):
        if payload.get("type") == "chat.request":
            assert client_id == EXPECTED_WORKER
            commands.append(payload)
            published = await app.state.broker.publish(payload["request_id"], {
                "type": "chat.completed",
                "text": "mode2-sdk-fixture-answer",
            })
            assert published

    app.state.registry.send = fake_worker_send
    return app, commands


@pytest.fixture
def sdk_transport(sdk_app):
    app, commands = sdk_app
    with TestClient(app) as asgi:
        def forward(request: httpx.Request) -> httpx.Response:
            # MockTransport keeps the stock OpenAI client's request serializer,
            # headers, stream parser and exception types intact. Only the final
            # socket hop is adapted into the real ASGI application.
            path = request.url.raw_path.decode("ascii")
            response = asgi.request(
                request.method, path, headers=dict(request.headers),
                content=request.read(),
            )
            return httpx.Response(
                response.status_code, headers=dict(response.headers),
                content=response.content, request=request,
            )

        transport = httpx.Client(transport=httpx.MockTransport(forward))
        sdk = openai.OpenAI(
            api_key=MASTER,
            base_url="http://testserver/v1",
            max_retries=0,
            http_client=transport,
        )
        try:
            yield sdk, commands
        finally:
            sdk.close()


def test_python_sdk_nonstream_chat_preserves_multiturn_history(sdk_transport) -> None:
    sdk, commands = sdk_transport
    result = sdk.chat.completions.create(
        model="gpt-5.6-sol",
        messages=[
            {"role": "system", "content": "Reply in Chinese."},
            {"role": "user", "content": "First turn"},
            {"role": "assistant", "content": "Acknowledged"},
            {"role": "user", "content": "Second turn"},
        ],
    )
    assert result.object == "chat.completion"
    assert result.choices[0].message.content == "mode2-sdk-fixture-answer"
    assert result.choices[0].finish_reason == "stop"
    assert result.usage.total_tokens >= 1
    assert len(commands) == 1
    prompt = commands[0]["prompt"]
    assert prompt.index("[System]") < prompt.index("First turn") < prompt.index("Acknowledged") < prompt.index("Second turn")


def test_python_sdk_stream_chunks_done_and_requested_model(sdk_transport) -> None:
    sdk, commands = sdk_transport
    stream = sdk.chat.completions.create(
        model="gpt-5.6-sol",
        stream=True,
        messages=[{"role": "user", "content": "Stream this reply."}],
    )
    chunks = list(stream)
    assert chunks
    assert chunks[0].choices[0].delta.role == "assistant"
    text = "".join(str(choice.delta.content or "") for chunk in chunks for choice in chunk.choices)
    assert text == "mode2-sdk-fixture-answer"
    assert any(choice.finish_reason == "stop" for chunk in chunks for choice in chunk.choices)
    assert {chunk.model for chunk in chunks} == {"gpt-5.6-sol"}
    assert len(commands) == 1


def test_python_sdk_models_and_responses_nonstream(sdk_transport) -> None:
    sdk, commands = sdk_transport
    models = sdk.models.list()
    assert models.object == "list"
    assert isinstance(models.data, list)

    response = sdk.responses.create(
        model="gpt-5.6-sol",
        input="Respond from the bridge",
        stream=False,
    )
    assert response.object == "response"
    assert response.status == "completed"
    assert "mode2-sdk-fixture-answer" in response.output_text
    assert commands[-1]["options"]["response_protocol"] == "responses-v108"


def test_python_sdk_standard_multipart_file_upload_is_not_compatibly_implemented(sdk_transport) -> None:
    sdk, commands = sdk_transport
    with pytest.raises(openai.APIStatusError) as error:
        sdk.files.create(
            file=("sample.txt", io.BytesIO(b"one two three")),
            purpose="assistants",
        )
    assert error.value.status_code in {400, 415, 422}
    assert commands == []
