from __future__ import annotations

import sys
import time
from typing import Any

from . import client as base

VERSION = "0.3.0"


class SecureBootstrapHandler(base.BootstrapHandler):
    server_version = "chat2api-local-bridge/0.3.0"

    def _allowed_origin(self) -> str:
        origin = self.headers.get("Origin", "").strip()
        if not origin or origin.startswith("chrome-extension://"):
            return origin
        return ""

    def _reject_web_origin(self) -> bool:
        origin = self.headers.get("Origin", "").strip()
        if origin and not origin.startswith("chrome-extension://"):
            self.send_response(403)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return True
        return False

    def do_OPTIONS(self) -> None:  # noqa: N802
        if self._reject_web_origin():
            return
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self._reject_web_origin():
            return
        super().do_GET()

    def _cors_headers(self) -> None:
        origin = self._allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")


class DesktopClient(base.DesktopClient):
    def __init__(self, config: base.ClientConfig) -> None:
        original_handler = base.BootstrapHandler
        base.BootstrapHandler = SecureBootstrapHandler
        try:
            super().__init__(config)
        finally:
            base.BootstrapHandler = original_handler

    def run(self, launch_now: bool = False) -> None:
        self.start_bridge()
        agent_id = ""
        first_launch = launch_now

        while True:
            try:
                if not agent_id:
                    agent_id = self.api.register()
                    print(f"Desktop agent connected: {agent_id}")
                    if first_launch:
                        self.launch_chrome()
                        first_launch = False

                command = self.api.wait_command(agent_id)
                if command.get("type") == "launch_browser":
                    print(f"Received browser wake request: {command.get('reason')}")
                    self.launch_chrome()
            except KeyboardInterrupt:
                print("Stopping desktop client.")
                return
            except Exception as error:
                print(f"Desktop client warning: {error}", file=sys.stderr)
                agent_id = ""
                time.sleep(3)


def run_client(args: Any) -> int:
    path = base.config_path_from_args(args)
    config = base.ClientConfig.load(path)
    client = DesktopClient(config)
    client.run(launch_now=args.launch_now)
    return 0


def main() -> int:
    parser = base.build_parser()
    args = parser.parse_args()
    try:
        if args.command == "run":
            return run_client(args)
        return int(args.func(args))
    except Exception as error:
        print(f"chat2api desktop client error: {error}", file=sys.stderr)
        return 1
