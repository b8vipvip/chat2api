from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

VERSION = "0.3.0"
DEFAULT_PORT = 8791


def default_data_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "chat2api"


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def candidate_chrome_paths() -> list[Path]:
    values: list[Path] = []
    if os.name == "nt":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if root:
                values.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        values.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            path = shutil_which(name)
            if path:
                values.append(Path(path))
    return values


def find_chrome(configured: str = "") -> Path:
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(f"Configured Chrome executable does not exist: {path}")
    for path in candidate_chrome_paths():
        if path.exists():
            return path
    raise FileNotFoundError("Google Chrome was not found. Set chrome_path in the desktop client config.")


@dataclass
class ClientConfig:
    server_url: str = "https://chat2api.mv3.cn"
    api_key: str = ""
    agent_name: str = platform.node() or "Desktop"
    chrome_path: str = ""
    local_bridge_port: int = DEFAULT_PORT

    @classmethod
    def load(cls, path: Path) -> "ClientConfig":
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        # Old v0.2 files may contain extension_dir/profile_dir. They are
        # deliberately ignored because v0.3 only drives the user's existing
        # Chrome profile and an already-installed extension.
        return cls(**{key: value for key, value in payload.items() if key in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


class ApiClient:
    def __init__(self, config: ClientConfig) -> None:
        self.config = config

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 35) -> Any:
        url = self.config.server_url.rstrip("/") + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"chat2api-desktop/{VERSION}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Server returned HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Cannot reach chat2api server: {error.reason}") from error

    def register(self) -> str:
        result = self.request(
            "POST",
            "/api/desktop/register",
            {
                "name": self.config.agent_name,
                "platform": platform.platform(),
                "version": VERSION,
                "metadata": {"python": platform.python_version(), "chrome_mode": "existing-profile"},
            },
        )
        return str(result["agent_id"])

    def bootstrap(self) -> dict[str, Any]:
        return dict(self.request("GET", "/api/desktop/bootstrap"))

    def wait_command(self, agent_id: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(agent_id, safe="")
        return dict(self.request("GET", f"/api/desktop/commands/{encoded}?timeout=25", timeout=35))


class BootstrapState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.payload: dict[str, Any] | None = None
        self.expires_at = 0.0

    def activate(self, payload: dict[str, Any]) -> None:
        with self.lock:
            expires = max(30, int(payload.get("expires_in_seconds") or 120))
            self.payload = dict(payload)
            self.expires_at = time.time() + expires

    def current(self) -> dict[str, Any] | None:
        with self.lock:
            if not self.payload or time.time() >= self.expires_at:
                return None
            return dict(self.payload)


class BootstrapHandler(BaseHTTPRequestHandler):
    server_version = "chat2api-local-bridge/0.3"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if urllib.parse.urlparse(self.path).path != "/bootstrap":
            self.send_error(404)
            return
        payload = self.server.bootstrap_state.current()  # type: ignore[attr-defined]
        if payload is None:
            self.send_response(404)
            self._cors_headers()
            self.end_headers()
            return
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def log_message(self, _format: str, *_args: object) -> None:
        return


class LocalBridgeServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, port: int, bootstrap_state: BootstrapState) -> None:
        super().__init__(("127.0.0.1", port), BootstrapHandler)
        self.bootstrap_state = bootstrap_state


class DesktopClient:
    def __init__(self, config: ClientConfig) -> None:
        if not config.api_key:
            raise ValueError("api_key is empty. Run the configure command first.")
        self.config = config
        self.api = ApiClient(config)
        self.bootstrap_state = BootstrapState()
        self.chrome_process: subprocess.Popen[bytes] | None = None
        self.bridge = LocalBridgeServer(config.local_bridge_port, self.bootstrap_state)
        self.bridge_thread = threading.Thread(
            target=self.bridge.serve_forever,
            name="chat2api-local-bridge",
            daemon=True,
        )

    def start_bridge(self) -> None:
        self.bridge_thread.start()
        print(f"Local extension bootstrap bridge: http://127.0.0.1:{self.config.local_bridge_port}/bootstrap")

    def prepare_bootstrap(self) -> dict[str, Any]:
        payload = self.api.bootstrap()
        launch_token = secrets.token_urlsafe(18)
        payload.update(
            {
                "auto_bind": False,
                "chrome_mode": "existing-profile",
                "launch_token": launch_token,
                "launch_url": (
                    "https://chatgpt.com/?chat2api_launch="
                    + urllib.parse.quote(launch_token, safe="")
                ),
            }
        )
        self.bootstrap_state.activate(payload)
        return payload

    def launch_chrome(self) -> None:
        payload = self.prepare_bootstrap()
        chrome = find_chrome(self.config.chrome_path)
        launch_url = str(payload["launch_url"])
        command = [str(chrome), "--new-window", launch_url]
        print("Opening the existing Chrome profile and a new ChatGPT automation window.")
        self.chrome_process = subprocess.Popen(command)
        print(f"Bootstrap is active for {payload.get('expires_in_seconds', 120)} seconds.")
        print("The chat2api extension must already be installed in this Chrome profile.")
        print("ChatGPT sign-in is managed manually by the user; chat2api never stores the ChatGPT password.")

    def run(self, launch_now: bool = False) -> None:
        self.start_bridge()
        agent_id = self.api.register()
        print(f"Desktop agent connected: {agent_id}")
        if launch_now:
            self.launch_chrome()

        while True:
            try:
                command = self.api.wait_command(agent_id)
                if command.get("type") == "launch_browser":
                    print(f"Received browser wake request: {command.get('reason')}")
                    self.launch_chrome()
            except KeyboardInterrupt:
                print("Stopping desktop client.")
                return
            except Exception as error:
                print(f"Desktop client warning: {error}", file=sys.stderr)
                time.sleep(3)


def config_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.config).expanduser().resolve() if args.config else default_data_dir() / "client.json"


def configure(args: argparse.Namespace) -> int:
    path = config_path_from_args(args)
    config = ClientConfig.load(path)
    if args.server_url:
        config.server_url = args.server_url.rstrip("/")
    if args.api_key:
        config.api_key = args.api_key
    if args.agent_name:
        config.agent_name = args.agent_name
    if args.chrome_path:
        config.chrome_path = args.chrome_path
    config.save(path)
    print(f"Saved desktop client configuration: {path}")
    print("Existing Chrome mode is enabled. Install the chat2api extension in Chrome once and sign in to ChatGPT manually.")
    print("The API key is stored locally; protect this file like a password.")
    return 0


def run_client(args: argparse.Namespace) -> int:
    path = config_path_from_args(args)
    config = ClientConfig.load(path)
    client = DesktopClient(config)
    client.run(launch_now=args.launch_now)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="chat2api existing-Chrome desktop launcher")
    parser.add_argument("--config", help="Path to client.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser("configure", help="Save server and existing Chrome settings")
    configure_parser.add_argument("--server-url")
    configure_parser.add_argument("--api-key")
    configure_parser.add_argument("--agent-name")
    configure_parser.add_argument("--chrome-path")
    configure_parser.set_defaults(func=configure)

    run_parser = subparsers.add_parser("run", help="Wait for server wake requests")
    run_parser.add_argument(
        "--launch-now",
        action="store_true",
        help="Open a new ChatGPT window in the existing Chrome profile immediately",
    )
    run_parser.set_defaults(func=run_client)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as error:
        print(f"chat2api desktop client error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
