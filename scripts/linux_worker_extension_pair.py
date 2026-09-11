#!/usr/bin/env python3
"""Pair one Linux Worker Chrome extension through its local CDP endpoint.

The raw pairing secret exists only in the root-owned worker identity file during
first install. After the extension has registered successfully, this helper
removes the secret from worker.json. It never logs or prints the secret.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlsplit, urlunsplit

import websockets


def server_from_worker(payload: dict) -> str:
    raw = str(payload.get("websocket_url") or "").strip()
    parsed = urlsplit(raw)
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunsplit((scheme, parsed.netloc, "", "", "")).rstrip("/")


def targets(cdp_url: str) -> list[dict]:
    with urlopen(cdp_url.rstrip("/") + "/json", timeout=3) as response:  # noqa: S310 - loopback-only CDP
        value = json.loads(response.read().decode("utf-8"))
    return value if isinstance(value, list) else []


async def evaluate(target_ws: str, expression: str) -> dict:
    async with websockets.connect(target_ws, open_timeout=5, close_timeout=2, max_size=2**20) as socket:
        await socket.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "awaitPromise": True, "returnByValue": True},
        }))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            message = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
            if message.get("id") != 1:
                continue
            if message.get("error"):
                raise RuntimeError(str(message["error"]))
            result = message.get("result", {}).get("result", {})
            return result.get("value") if isinstance(result.get("value"), dict) else {"ok": False, "error": "invalid_pair_result"}
    raise RuntimeError("pair_evaluation_timeout")


def remove_secret(path: Path, payload: dict) -> None:
    payload = dict(payload)
    payload.pop("extension_pairing_code", None)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o640)
        os.chown(temporary, 0, path.stat().st_gid)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


async def main_async(config: Path, cdp_url: str) -> int:
    payload = json.loads(config.read_text(encoding="utf-8"))
    secret = str(payload.get("extension_pairing_code") or "").strip()
    if not secret:
        print(json.dumps({"ok": True, "skipped": "pairing_secret_absent"}))
        return 0
    server = server_from_worker(payload)
    name = str(payload.get("extension_name") or "Linux Worker").strip()[:120] or "Linux Worker"
    deadline = time.monotonic() + 60
    last_error = "extension_service_worker_not_found"
    while time.monotonic() < deadline:
        try:
            candidates = [item for item in targets(cdp_url) if str(item.get("type") or "") in {"service_worker", "background_page"} and str(item.get("url") or "").startswith("chrome-extension://") and item.get("webSocketDebuggerUrl")]
            for item in candidates:
                expression = (
                    "(async()=>{try{const r=await pair({serverUrl:" + json.dumps(server) + ",pairingCode:" + json.dumps(secret) + ",extensionName:" + json.dumps(name) + ",force:true,autoBind:true});return {ok:true,client_id:r&&r.client_id||''};}catch(e){return {ok:false,error:String(e&&e.message||e)}}})()"
                )
                result = await evaluate(str(item["webSocketDebuggerUrl"]), expression)
                if result.get("ok") is True:
                    remove_secret(config, payload)
                    print(json.dumps({"ok": True, "client_id": str(result.get("client_id") or "")[:120]}))
                    return 0
                last_error = str(result.get("error") or "pair_failed")[:240]
        except Exception as exc:  # bounded retry while Chrome/extension starts
            last_error = str(exc)[:240]
        await asyncio.sleep(1)
    print(json.dumps({"ok": False, "error": last_error}))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--cdp-url", required=True)
    args = parser.parse_args()
    return asyncio.run(main_async(Path(args.config), str(args.cdp_url).rstrip("/")))


if __name__ == "__main__":
    raise SystemExit(main())
