"""W-04 recovery contract: owner-scoped snapshots and no duplicate dispatch."""
import asyncio
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from app.user_commerce import USER_SESSION_COOKIE

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_recovery_api import install_web_chat_recovery_api
from app.web_chat_cancellation_v1 import (
    WebChatAdmissionCancelled, WebChatCancellationSignal,
    await_worker_admission_or_cancel,
)


ROOT = Path(__file__).resolve().parents[1]
RECOVERY_JS = ROOT / "app" / "chat_stream_recovery_v1.js"



@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for the Web Chat stream harness")
def test_in_band_stream_failure_settles_without_recovery_polling():
    harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/chat_client_v1.js', 'utf8');
const conversation = {messages: []};
const notices = [];
let polls = 0;
const node = {classList: {add() {}, remove() {}}, focus() {}};
const body = new ReadableStream({
  start(controller) {
    const stream = 'data: {"error":{"type":"browser_error","message":"Worker failed"}}\r\n\r\ndata: [DONE]\r\n\r\n';
    controller.enqueue(new TextEncoder().encode(stream));
    controller.close();
  },
});
const context = {
  state: {apiToken: 'token', user: {user_id: 'user-a'}, selectedModel: 'model', pendingImages: [], pendingImagesConversationId: '', pendingFiles: [], pendingFilesConversationId: ''},
  currentConversation: () => conversation,
  window: {Chat2APIStreamRecoveryV1: {createRequestId: () => 'req_webchat_error'}},
  apiJson: async () => ({user: {user_id: 'user-a'}}),
  fetch: async () => ({ok: true, body}),
  // The test extracts sendMessage independently of the native sidecar module.
  streamNativeContent: () => Promise.resolve(),
  makeRecoveryController: () => null,
  selectedKeyId: () => 'key-a',
  selectedKeyHasScope: () => true,
  uploadPendingImages: async () => {},
  uploadPendingFiles: async () => {},
  renderPendingImages() {},
  renderPendingFiles() {},
  setConnection() {},
  recoverPendingMessage: async () => { polls++; return {status: 'completed'}; },
  id: () => String(conversation.messages.length),
  toast: (message) => notices.push(message),
  $: () => node,
  autoTitle() {}, saveHistory() {}, resizeComposer() {}, renderAll() {}, renderMessages() {},
  AbortController, TextDecoder,
};
const readFrame = source.slice(source.indexOf('  class StreamResponseError'), source.indexOf('  function recoveryStatusLabel'));
const send = source.slice(source.indexOf('  async function sendMessage'), source.indexOf('  async function selectApiKey'));
const sendMessage = vm.runInNewContext(readFrame + send + '\nsendMessage', context);
(async () => {
  await sendMessage('question');
  assert.deepEqual(notices, ['请求失败：Worker failed']);
  assert.deepEqual(conversation.messages.map(message => message.role), ['user']);
  assert.equal(conversation.messages.some(message => message.role === 'assistant'), false);
  assert.equal(polls, 0);
  assert.equal(body.locked, false);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(["node", "-e", harness], cwd=ROOT, check=True, timeout=20)



def make_app(data_dir: Path):
    app = create_app(Settings(CHAT2API_API_KEY="w04-test-key", CHAT2API_DATA_DIR=str(data_dir)))
    install_user_console_v104_patch(app)
    install_web_chat_recovery_api(app)
    install_web_chat_recovery_api(app)
    return app


def register(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/user/register",
        json={"email": email, "password": "secure-password-123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]["user_id"]


def remember(app, request_id: str, user_key: str, **changes):
    app.state.telemetry._remember({
        "request_id": request_id,
        "api_key_id": user_key,
        "status": "running",
        "updated_at": "2026-11-12T08:00:00+00:00",
        **changes,
    })


def test_sse_event_replay_is_owner_scoped_and_honors_last_event_id(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_eventreplay123"
    with TestClient(app) as alice, TestClient(app) as bob:
        alice_id = register(alice, "replay-alice@example.org")
        bob_id = register(bob, "replay-bob@example.org")
        app.state.user_accounts.attach_key(alice_id, "key_replay_alice")
        app.state.user_accounts.attach_key(bob_id, "key_replay_bob")
        remember(
            app, request_id, "key_replay_alice", status="completed",
            response_text="complete", response_text_available=True,
        )
        delivery = asyncio.run(app.state.web_chat_event_journal.append_delivery(
            request_id, "key_replay_alice", "x" * 5000,
        ))
        first_id, second_id = delivery["chunk_event_ids"]
        terminal_id = asyncio.run(app.state.web_chat_event_journal.append_delivery_terminal(
            request_id, "key_replay_alice", delivery["text_chars"],
        ))

        replay = alice.get(
            f"/api/user/chat/requests/{request_id}/events",
            headers={"Last-Event-ID": str(first_id)},
        )
        assert replay.status_code == 200, replay.text
        assert replay.headers["content-type"].startswith("text/event-stream")
        assert replay.headers["cache-control"] == "private, no-store"
        assert f"id: {second_id}\nevent: delivery.delta" in replay.text
        assert f"id: {terminal_id}\nevent: delivery.completed" in replay.text
        assert f"id: {first_id}\nevent: delivery.delta" not in replay.text

        denied = bob.get(f"/api/user/chat/requests/{request_id}/events")
        assert denied.status_code == 404
        assert denied.headers["cache-control"] == "private, no-store"

        invalid_cursor = alice.get(
            f"/api/user/chat/requests/{request_id}/events",
            headers={"Last-Event-ID": "not-a-number"},
        )
        assert invalid_cursor.status_code == 400


def test_recovering_sse_replay_surfaces_unverified_worker_progress_before_terminal(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_recoveryprogress123"
    with TestClient(app) as client:
        user_id = register(client, "recovery-progress@example.org")
        app.state.user_accounts.attach_key(user_id, "key_recovery_progress")
        remember(
            app, request_id, "key_recovery_progress",
            status="recovering", web_chat_mode=True,
            response_text="", response_text_available=False,
        )
        asyncio.run(app.state.web_chat_event_journal.append_worker_event(
            request_id, "key_recovery_progress",
            {"type": "chat.delta", "delta": "provisional worker answer"},
        ))
        asyncio.run(app.state.web_chat_event_journal.append_terminal(
            request_id, "key_recovery_progress", "stalled", "worker lease expired",
        ))
        replay = client.get(f"/api/user/chat/requests/{request_id}/events")
        assert replay.status_code == 200
        assert "event: recovery.partial.snapshot" in replay.text
        assert '"partial_text":"provisional worker answer"' in replay.text
        assert '"partial_text_unverified":true' in replay.text
        assert "event: delivery.stalled" in replay.text


def test_completed_snapshot_is_returned_only_to_key_owner_and_never_cached(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_12345678"
    with TestClient(app) as alice, TestClient(app) as bob:
        alice_id = register(alice, "alice@example.org")
        bob_id = register(bob, "bob@example.org")
        app.state.user_accounts.attach_key(alice_id, "key_alice")
        app.state.user_accounts.attach_key(bob_id, "key_bob")
        remember(
            app, request_id, "key_alice", status="completed",
            response_text="Final assistant answer",
            response_text_available=True,
        )

        response = alice.get(f"/api/user/chat/requests/{request_id}/recovery")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
        assert response.json()["terminal"] is True
        assert response.json()["response_text"] == "Final assistant answer"
        assert response.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in response.headers["vary"].split(",")}

        denied = bob.get(f"/api/user/chat/requests/{request_id}/recovery")
        assert denied.status_code == 404
        assert denied.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in denied.headers["vary"].split(",")}



def test_cancel_signal_is_owner_scoped_idempotent_and_not_a_terminal_owner(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_cancel123"
    sent = []

    async def fake_send(client_id, payload):
        sent.append((client_id, dict(payload)))

    with TestClient(app) as alice, TestClient(app) as bob:
        alice_id = register(alice, "cancel-alice@example.org")
        bob_id = register(bob, "cancel-bob@example.org")
        app.state.user_accounts.attach_key(alice_id, "key_cancel_alice")
        app.state.user_accounts.attach_key(bob_id, "key_cancel_bob")
        remember(app, request_id, "key_cancel_alice")
        state = SimpleNamespace(
            client_id="ext_worker123",
            terminal_event_type=None,
            diagnostics={},
        )
        app.state.broker.requests[request_id] = state
        app.state.registry.send = fake_send
        path = f"/api/user/chat/requests/{request_id}/cancel"

        missing_origin = alice.post(path)
        assert missing_origin.status_code == 403
        assert missing_origin.headers["cache-control"] == "private, no-store"

        denied = bob.post(path, headers={"Origin": "http://testserver"})
        assert denied.status_code == 404
        assert denied.headers["cache-control"] == "private, no-store"
        assert sent == []

        cancelled = alice.post(path, headers={"Origin": "http://testserver"})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["cancel_requested"] is True
        assert cancelled.json()["terminal"] is False
        assert cancelled.json()["active"] is True
        assert cancelled.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in cancelled.headers["vary"].split(",")}
        assert sent == [("ext_worker123", {"type": "chat.cancel", "request_id": request_id})]
        assert state.terminal_event_type is None

        repeated = alice.post(path, headers={"Origin": "http://testserver"})
        assert repeated.status_code == 200
        assert repeated.json()["already_requested"] is True
        assert len(sent) == 1
        assert state.terminal_event_type is None


def test_terminal_request_cancel_is_idempotent_and_never_signals_worker(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_done1234"
    with TestClient(app) as client:
        user_id = register(client, "cancel-terminal@example.org")
        app.state.user_accounts.attach_key(user_id, "key_cancel_terminal")
        remember(
            app, request_id, "key_cancel_terminal", status="completed",
            response_text="final", response_text_available=True,
        )
        sent = []

        async def fake_send(client_id, payload):
            sent.append((client_id, payload))

        app.state.registry.send = fake_send
        response = client.post(
            f"/api/user/chat/requests/{request_id}/cancel",
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 200
        assert response.json()["terminal"] is True
        assert response.json()["cancel_requested"] is False
        assert sent == []


def test_queued_cancel_signals_admission_and_waits_for_terminal_projection(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_queuedcancel1"
    with TestClient(app) as client:
        user_id = register(client, "cancel-queued@example.org")
        app.state.user_accounts.attach_key(user_id, "key_cancel_queued")
        remember(app, request_id, "key_cancel_queued", status="queued")
        signal = WebChatCancellationSignal()
        signal.finalized.set()
        app.state.web_chat_cancel_events[request_id] = signal

        response = client.post(
            f"/api/user/chat/requests/{request_id}/cancel",
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["queued"] is True
        assert response.json()["cancel_requested"] is True
        assert response.json()["terminal"] is False
        assert signal.requested.is_set()
        assert app.state.broker.requests.get(request_id) is None

        repeated = client.post(
            f"/api/user/chat/requests/{request_id}/cancel",
            headers={"Origin": "http://testserver"},
        )
        assert repeated.status_code == 200
        assert repeated.json()["already_requested"] is True
        assert signal.requested.is_set()


def test_worker_admission_wait_is_interruptible_without_redispatch() -> None:
    async def scenario() -> None:
        signal = WebChatCancellationSignal()
        started = asyncio.Event()

        async def wait_for_worker() -> str:
            started.set()
            await asyncio.Event().wait()
            return "worker-never-selected"

        task = asyncio.create_task(await_worker_admission_or_cancel(wait_for_worker, signal))
        await started.wait()
        signal.requested.set()
        try:
            await task
        except WebChatAdmissionCancelled:
            pass
        else:
            raise AssertionError("queued cancellation must interrupt Worker admission")

        async def ready_worker() -> str:
            return "worker-a"

        result = await await_worker_admission_or_cancel(ready_worker, WebChatCancellationSignal())
        assert result == "worker-a"

    asyncio.run(scenario())


def test_cancel_delivery_failure_does_not_leave_false_idempotency_marker(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_cancelfail"
    with TestClient(app) as client:
        user_id = register(client, "cancel-failure@example.org")
        app.state.user_accounts.attach_key(user_id, "key_cancel_failure")
        remember(app, request_id, "key_cancel_failure")
        state = SimpleNamespace(
            client_id="ext_worker123",
            terminal_event_type=None,
            diagnostics={},
        )
        app.state.broker.requests[request_id] = state

        async def fail_send(client_id, payload):
            raise RuntimeError("worker offline")

        app.state.registry.send = fail_send
        response = client.post(
            f"/api/user/chat/requests/{request_id}/cancel",
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 409
        assert response.headers["cache-control"] == "private, no-store"
        assert "web_chat_cancel_requested" not in state.diagnostics

def test_running_request_is_pollable_but_stale_running_state_is_not_replayed(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_abcdefgh"
    with TestClient(app) as client:
        user_id = register(client, "running@example.org")
        app.state.user_accounts.attach_key(user_id, "key_running")
        remember(app, request_id, "key_running")
        app.state.broker.requests[request_id] = SimpleNamespace(terminal_event_type=None)
        active = client.get(f"/api/user/chat/requests/{request_id}/recovery")
        assert active.status_code == 200
        assert active.json()["recoverable"] is True
        assert active.json()["active"] is True
        assert active.json()["phase"] == "generating"
        assert active.json()["response_text"] == ""
        assert active.json()["response_text_available"] is False
        del app.state.broker.requests[request_id]

        stale = client.get(f"/api/user/chat/requests/{request_id}/recovery")
        assert stale.status_code == 200
        assert stale.json()["status"] == "unknown"
        assert stale.json()["recoverable"] is False
        assert stale.json()["response_text"] == ""


def test_recovering_mode1_request_waits_for_worker_terminal_outbox_without_redispatch(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_recovering123"
    with TestClient(app) as client:
        user_id = register(client, "recovering@example.org")
        app.state.user_accounts.attach_key(user_id, "key_recovering")
        remember(
            app, request_id, "key_recovering",
            status="recovering", web_chat_mode=True,
            client_id="ext_worker123",
            response_text="", response_text_available=False,
            response_text_provisional="",
            diagnostics={"web_chat_request_recovery":"awaiting-worker-terminal-replay"},
        )
        # Simulate Worker deltas durably journaled before the Broker process
        # restarted, while Telemetry never received its provisional snapshot.
        app.state.web_chat_event_journal._append_records(
            request_id, "key_recovering",
            [("worker.delta", {"delta": "journal partial"})],
        )
        response = client.get(f"/api/user/chat/requests/{request_id}/recovery")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "recovering"
        assert payload["terminal"] is False
        assert payload["recoverable"] is True
        assert payload["active"] is True
        assert payload["phase"] == "reconnecting"
        assert payload["partial_text"] == "journal partial"
        assert payload["partial_text_available"] is True
        assert payload["partial_text_unverified"] is True
        assert payload["response_text"] == ""
        assert payload["response_text_available"] is False


def test_queued_admission_and_failed_partial_text_are_not_misreported(tmp_path):
    app = make_app(tmp_path)
    queued_id, failed_id = "req_queued123", "req_failed123"
    with TestClient(app) as client:
        user_id = register(client, "states@example.org")
        app.state.user_accounts.attach_key(user_id, "key_states")
        remember(app, queued_id, "key_states", status="queued")
        queued = client.get(f"/api/user/chat/requests/{queued_id}/recovery").json()
        assert queued["recoverable"] is True and queued["phase"] == "admission"
        assert queued["response_text"] == ""
        assert queued["response_text_available"] is False

        remember(
            app, failed_id, "key_states", status="stalled",
            response_text="",
            response_text_available=False,
            response_text_provisional="unverified partial output",
        )
        failed = client.get(f"/api/user/chat/requests/{failed_id}/recovery").json()
        assert failed["terminal"] is True
        assert failed["status"] == "stalled"
        assert failed["response_text"] == ""
        assert failed["response_text_available"] is False
        assert failed["partial_text"] == "unverified partial output"
        assert failed["partial_text_available"] is True



def test_recovery_respects_bounded_telemetry_retention_window(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_oldrecord1"
    with TestClient(app) as client:
        user_id = register(client, "retention@example.org")
        app.state.user_accounts.attach_key(user_id, "key_retention")
        remember(
            app, request_id, "key_retention", status="completed",
            response_text="old final answer", response_text_available=True,
        )
        for index in range(500):
            remember(
                app, f"req_new{index:08d}", "key_retention",
                status="completed", response_text=f"answer {index}",
                response_text_available=True,
            )
        assert client.get(
            f"/api/user/chat/requests/{request_id}/recovery"
        ).status_code == 404

def test_unknown_and_malformed_ids_are_not_disclosed(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        register(client, "unknown@example.org")
        unknown = client.get("/api/user/chat/requests/req_12345678/recovery")
        malformed = client.get("/api/user/chat/requests/not-a-request/recovery")
        assert unknown.status_code == 404
        assert malformed.status_code == 404
        assert unknown.headers["cache-control"] == "private, no-store"
        assert malformed.headers["cache-control"] == "private, no-store"
    with TestClient(app) as anonymous:
        unauthorized = anonymous.get("/api/user/chat/requests/req_12345678/recovery")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["cache-control"] == "private, no-store"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for the recovery state-machine harness")
def test_client_polls_same_request_with_get_and_never_redispatches():
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const src = fs.readFileSync(process.argv[1], "utf8");
const window = {};
const calls = [];
const updates = [];
const replies = [
  {status:200, ok:true, json:async()=>({
    request_id:"req_12345678", status:"running", terminal:false,
    recoverable:true, active:true, phase:"reconnecting", retry_after_ms:250,
    partial_text_available:true, partial_text_unverified:true,
    partial_text:"durable partial"
  })},
  {status:200, ok:true, json:async()=>({
    request_id:"req_12345678", status:"completed", terminal:true,
    recoverable:false, active:false, response_text_available:true,
    response_text:"final answer"
  })}
];
const fetcher = async (url, options) => {
  calls.push({url, method:options.method, credentials:options.credentials});
  return replies.shift();
};
vm.runInNewContext(src, {
  window, AbortController, Date, Promise, Math,
  setTimeout:fn=>{fn();return 1;}, clearTimeout:()=>{}
});
const api = window.Chat2APIStreamRecoveryV1;
assert(api && api.isRequestId("req_12345678"));
assert(!api.isRequestId("bad"));
assert(/^req_[A-Za-z0-9_-]{8,120}$/.test(api.createRequestId({randomUUID:()=>"12345678-1234-1234-1234-123456789012"})));
(async()=>{
  const result = await api.create({
    fetcher, onUpdate:value=>updates.push(value), pollIntervalMs:250
  }).resume({request_id:"req_12345678", assistant_message_id:"message_123456"});
  assert.strictEqual(result.status, "completed");
  assert.strictEqual(result.response_text, "final answer");
  assert.strictEqual(calls.length, 2);
  assert(calls.every(call=>call.method==="GET"));
  assert(calls.every(call=>call.credentials==="same-origin"));
  assert(calls.every(call=>call.url==="/api/user/chat/requests/req_12345678/recovery"));
  assert(updates.some(value=>value.status==="running" && value.partial_text==="durable partial" && value.partial_text_unverified===true));
  assert(updates.some(value=>value.status==="completed"));
  const replayWindow = {};
  vm.runInNewContext(src, {
    window:replayWindow, AbortController, Date, Promise, Math, TextDecoder,
    setTimeout:()=>1, clearTimeout:()=>{},
  });
  const replayApi = replayWindow.Chat2APIStreamRecoveryV1;
  const replayCalls = [], replayEvents = [];
  const replayFrames = [
    "id: 7\nevent: cursor\ndata: {}\n\n",
    "id: 8\nevent: delivery.delta\ndata: {\"delta\":\"replayed answer\"}\n\n",
    "id: 9\nevent: delivery.completed\ndata: {\"text_chars\":15}\n\n",
  ].join("");
  const replayBytes = new TextEncoder().encode(replayFrames);
  let replayRead = false;
  const replayController = replayApi.create({
    fetcher:async(url, options)=>{
      replayCalls.push({url,method:options.method,headers:options.headers});
      return {
        status:200,ok:true,
        body:{getReader:()=>({
          read:async()=> replayRead ? {done:true} : (replayRead=true,{done:false,value:replayBytes}),
          cancel:async()=>{},releaseLock(){},
        })},
      };
    },
    onEvent:value=>replayEvents.push(value),
  });
  const replayResult = await replayController.replay({
    request_id:"req_12345678",assistant_message_id:"message_123456"
  }, 6);
  assert.strictEqual(replayResult.status,"completed");
  assert.strictEqual(replayResult.terminal,true);
  assert.strictEqual(replayEvents.length,3);
  assert.strictEqual(replayEvents[1].id,8);
  assert.strictEqual(replayEvents[1].data.delta,"replayed answer");
  assert.strictEqual(replayCalls.length,1);
  assert.strictEqual(replayCalls[0].method,"GET");
  assert.strictEqual(replayCalls[0].url,
    "/api/user/chat/requests/req_12345678/events?after=6");
  assert.strictEqual(replayCalls[0].headers["Last-Event-ID"],"6");

  const inconsistent = await api.create({
    fetcher:async()=>({status:200,ok:true,json:async()=>({
      request_id:"req_12345678", status:"completed", terminal:false,
      recoverable:false, active:false, response_text_available:true,
      response_text:"must not be accepted"
    })})
  }).resume({request_id:"req_12345678", assistant_message_id:"message_123456"});
  assert.strictEqual(inconsistent.status, "invalid_response");

  const cancelCalls = [];
  const cancelController = api.create({
    fetcher:async(url, options)=>{
      cancelCalls.push({url,method:options.method,credentials:options.credentials});
      return {status:200,ok:true,json:async()=>({
        request_id:"req_12345678", status:"cancellation_requested",
        terminal:false, active:true, cancel_requested:true, queued:true
      })};
    }
  });
  const cancelResult = await cancelController.cancel({
    request_id:"req_12345678", assistant_message_id:"message_123456"
  });
  assert.strictEqual(cancelResult.status, "cancellation_requested");
  assert.strictEqual(cancelResult.cancel_requested, true);
  assert.strictEqual(cancelResult.queued, true);
  assert.strictEqual(cancelCalls.length, 1);
  assert.strictEqual(cancelCalls[0].method, "POST");
  assert.strictEqual(cancelCalls[0].credentials, "same-origin");
  assert.strictEqual(cancelCalls[0].url, "/api/user/chat/requests/req_12345678/cancel");
  assert(!cancelCalls.some(call=>call.url.includes("/v1/chat/completions")));

  const unavailable = await api.create({
    fetcher:async()=>({status:409,ok:false})
  }).cancel({request_id:"req_12345678",assistant_message_id:"message_123456"});
  assert.strictEqual(unavailable.status, "cancel_unavailable");

  const finalizing = await api.create({
    fetcher:async()=>({status:200,ok:true,json:async()=>({
      request_id:"req_12345678", status:"finalizing",
      terminal:false, active:true, cancel_requested:false,
      already_terminal:true
    })})
  }).cancel({request_id:"req_12345678",assistant_message_id:"message_123456"});
  assert.strictEqual(finalizing.status, "finalizing");
  assert.strictEqual(finalizing.terminal, false);
  console.log("recovery-harness-passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", harness, str(RECOVERY_JS)],
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "recovery-harness-passed" in result.stdout


def test_long_lived_replay_revalidates_user_session_and_key_ownership(monkeypatch, tmp_path):
    app = make_app(tmp_path)
    request_id = "req_reauthreplay123"
    with TestClient(app) as client:
        user_id = register(client, "replay-reauth@example.org")
        app.state.user_accounts.attach_key(user_id, "key_replay_reauth")
        remember(app, request_id, "key_replay_reauth", web_chat_mode=True)
        app.state.broker.requests[request_id] = SimpleNamespace(terminal_event_type=None)
        cookie = client.cookies.get(USER_SESSION_COOKIE)
        original_user = app.state.user_accounts.authenticate(cookie)
        calls = []

        def revoke_after_initial_auth(value):
            calls.append(value)
            return original_user if len(calls) == 1 else None

        monkeypatch.setattr(app.state.user_accounts, "authenticate", revoke_after_initial_auth)

        class RequestStub:
            def __init__(self, application, session_cookie):
                self.app = application
                self.cookies = {USER_SESSION_COOKIE: session_cookie}

            async def is_disconnected(self):
                return False

        route = next(
            item for item in app.routes
            if getattr(item, "path", "") == "/api/user/chat/requests/{request_id}/events"
        )
        response = asyncio.run(route.endpoint(
            request_id, RequestStub(app, cookie), Response(), after=0, last_event_id=None,
        ))

        async def no_wait(_seconds):
            return None

        monkeypatch.setattr("app.web_chat_recovery_api.asyncio.sleep", no_wait)

        async def consume():
            async for _ in response.body_iterator:
                pass

        asyncio.run(asyncio.wait_for(consume(), timeout=2))
        assert len(calls) >= 2



def test_recovery_prefers_longest_unverified_partial_across_worker_and_delivery_journals(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_longpartial123"
    with TestClient(app) as client:
        user_id = register(client, "long-partial@example.org")
        app.state.user_accounts.attach_key(user_id, "key_long_partial")
        remember(
            app, request_id, "key_long_partial",
            status="recovering", web_chat_mode=True,
            response_text_provisional="short provisional",
        )
        asyncio.run(app.state.web_chat_event_journal.append_delivery(
            request_id, "key_long_partial", "short delivered",
        ))
        asyncio.run(app.state.web_chat_event_journal.append_worker_event(
            request_id, "key_long_partial",
            {"type": "chat.delta", "delta": "worker partial response is longer"},
        ))

        recovery = client.get(
            f"/api/user/chat/requests/{request_id}/recovery"
        )
        assert recovery.status_code == 200
        assert recovery.json()["partial_text"] == "worker partial response is longer"
        assert recovery.json()["partial_text_unverified"] is True

        asyncio.run(app.state.web_chat_event_journal.append_terminal(
            request_id, "key_long_partial", "stalled", "worker lease expired",
        ))
        replay = client.get(f"/api/user/chat/requests/{request_id}/events")
        assert replay.status_code == 200
        assert '"partial_text":"worker partial response is longer"' in replay.text
        assert "event: recovery.partial.snapshot" in replay.text
        assert "event: delivery.stalled" in replay.text



@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for the Web Chat renderer harness")
def test_recovery_partial_snapshot_never_regresses_displayed_client_text():
    harness = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/chat_client_v1.js', 'utf8');
const start = source.indexOf('  function appendDelta(');
const end = source.indexOf('  function recoveryStatusLabel(', start);
assert.ok(start >= 0 && end > start);
const context = vm.createContext({MAX_MESSAGE_CHARS: 200000});
const api = vm.runInContext(source.slice(start, end) + '\n({consumeSseFrame})', context);
const assistant = {content:'longer worker progress', lastEventId:8};
const frame = text => [
  'id: 9',
  'event: recovery.partial.snapshot',
  'data: ' + JSON.stringify({partial_text:text,partial_text_unverified:true}),
].join('\n');
api.consumeSseFrame(frame('short'), assistant);
assert.equal(assistant.content, 'longer worker progress');
api.consumeSseFrame(frame('longer worker progress plus new text'), assistant);
assert.equal(assistant.content, 'longer worker progress plus new text');
""";
    result = subprocess.run(
        ["node", "-e", harness], cwd=ROOT,
        check=False, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_recovering_sse_replays_citations_from_canonical_worker_terminal(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_terminalcitation123"
    with TestClient(app) as client:
        user_id = register(client, "terminal-citation@example.org")
        app.state.user_accounts.attach_key(user_id, "key_terminal_citation")
        remember(
            app, request_id, "key_terminal_citation",
            status="recovering", web_chat_mode=True,
            response_text="", response_text_available=False,
        )
        asyncio.run(app.state.web_chat_event_journal.append_worker_event(
            request_id, "key_terminal_citation",
            {
                "type": "chat.completed",
                "message_id": "assistant-final",
                "text": "The API is documented here.",
                "metadata": {
                    "annotations": [{
                        "type": "url_citation",
                        "start_index": 0,
                        "end_index": 26,
                        "url_citation": {
                            "url": "https://docs.example.org/api",
                            "title": "API reference",
                            "ref_id": "turn0search0",
                        },
                    }],
                },
            },
        ))
        asyncio.run(app.state.web_chat_event_journal.append_terminal(
            request_id, "key_terminal_citation", "stalled", "worker lease expired",
        ))
        replay = client.get(f"/api/user/chat/requests/{request_id}/events")
        assert replay.status_code == 200, replay.text
        assert "event: recovery.partial.snapshot" in replay.text
        assert '"id":"turn0search0"' in replay.text
        assert '"message_id":"assistant-final"' in replay.text
        assert "event: chat.references" not in replay.text
        assert "event: delivery.stalled" in replay.text


def test_completed_sse_replay_surfaces_terminal_citations_without_recovery_snapshot(tmp_path):
    app = make_app(tmp_path)
    request_id = "req_completedcitation123"
    with TestClient(app) as client:
        user_id = register(client, "completed-citation@example.org")
        app.state.user_accounts.attach_key(user_id, "key_completed_citation")
        remember(
            app, request_id, "key_completed_citation",
            status="completed", web_chat_mode=True,
            response_text="The API is documented here.", response_text_available=True,
        )
        asyncio.run(app.state.web_chat_event_journal.append_worker_event(
            request_id, "key_completed_citation",
            {
                "type": "chat.completed",
                "message_id": "assistant-final",
                "text": "The API is documented here.",
                "metadata": {
                    "annotations": [{
                        "type": "url_citation",
                        "start_index": 0,
                        "end_index": 26,
                        "url_citation": {
                            "url": "https://docs.example.org/api",
                            "title": "API reference",
                            "ref_id": "turn0search0",
                        },
                    }],
                },
            },
        ))
        delivery = asyncio.run(app.state.web_chat_event_journal.append_delivery(
            request_id, "key_completed_citation", "The API is documented here.",
        ))
        asyncio.run(app.state.web_chat_event_journal.append_delivery_terminal(
            request_id, "key_completed_citation", delivery["text_chars"],
        ))
        replay = client.get(f"/api/user/chat/requests/{request_id}/events")
        assert replay.status_code == 200, replay.text
        assert "event: chat.references" in replay.text
        assert '"id":"turn0search0"' in replay.text
        assert "event: delivery.completed" in replay.text
        assert "event: recovery.partial.snapshot" not in replay.text
