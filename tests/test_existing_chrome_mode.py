import json
from pathlib import Path

from desktop_client import client as desktop


def test_legacy_dedicated_profile_fields_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "client.json"
    path.write_text(
        json.dumps(
            {
                "server_url": "https://example.test",
                "api_key": "key",
                "extension_dir": "old-extension",
                "profile_dir": "old-profile",
            }
        ),
        encoding="utf-8",
    )
    config = desktop.ClientConfig.load(path)
    assert config.server_url == "https://example.test"
    assert not hasattr(config, "extension_dir")
    assert not hasattr(config, "profile_dir")


def test_launch_uses_existing_chrome_without_dedicated_flags(monkeypatch, tmp_path: Path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"")
    captured: list[list[str]] = []

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(desktop.subprocess, "Popen", lambda command: captured.append(command) or Process())

    instance = desktop.DesktopClient.__new__(desktop.DesktopClient)
    instance.config = desktop.ClientConfig(api_key="key", chrome_path=str(chrome))
    instance.prepare_bootstrap = lambda: {
        "launch_url": "https://chatgpt.com/?chat2api_launch=test-token",
        "expires_in_seconds": 120,
    }

    desktop.DesktopClient.launch_chrome(instance)

    command = captured[0]
    assert command[0] == str(chrome)
    assert "--new-window" in command
    assert any("chat2api_launch=test-token" in value for value in command)
    assert not any(value.startswith("--user-data-dir") for value in command)
    assert not any(value.startswith("--load-extension") for value in command)
    assert not any(value.startswith("--disable-extensions-except") for value in command)


def test_bootstrap_contains_one_time_launch_token() -> None:
    class Api:
        def bootstrap(self):
            return {
                "server_url": "https://example.test",
                "pairing_code": "pair",
                "expires_in_seconds": 120,
            }

    instance = desktop.DesktopClient.__new__(desktop.DesktopClient)
    instance.api = Api()
    instance.bootstrap_state = desktop.BootstrapState()
    payload = desktop.DesktopClient.prepare_bootstrap(instance)

    assert payload["chrome_mode"] == "existing-profile"
    assert payload["auto_bind"] is False
    assert payload["launch_token"]
    assert payload["launch_token"] in payload["launch_url"]
