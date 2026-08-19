from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _runtime_version(source: str) -> tuple[int, int, int]:
    match = re.search(r'SERVER_RUNTIME_VERSION = "(\d+)\.(\d+)\.(\d+)"', source)
    assert match
    return tuple(map(int, match.groups()))


def test_worker_uses_chrome_for_testing_to_keep_unpacked_extension_autoload_supported():
    bootstrap = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts" / "linux_worker_chrome_launcher.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "linux_worker_chrome_launcher.sh" in dockerfile
    assert "linux_worker_chrome_launcher.sh" in bootstrap
    assert "ExecStart=/bin/bash ${WORKER_DIR}/scripts/linux_worker_chrome_launcher.sh" in bootstrap
    assert "ExecStart=/usr/bin/google-chrome" not in bootstrap
    assert "last-known-good-versions-with-downloads.json" in launcher
    assert '.channels.Stable.version' in launcher
    assert 'select(.platform == "linux64")' in launcher
    assert "https://storage.googleapis.com/chrome-for-testing-public/" in launcher
    assert '--load-extension="$EXTENSION_DIR"' in launcher
    assert '--user-data-dir="$PROFILE_DIR"' in launcher
    assert "systemctl restart chat2api-chrome.service" in bootstrap
    assert "systemctl restart chat2api-worker-agent.service" in bootstrap


def test_remote_login_watchdog_and_reload_follow_the_dedicated_worker_profile_not_branded_binary_name():
    remote = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    watchdog = (ROOT / "scripts" / "linux_worker_watchdog.sh").read_text(encoding="utf-8")
    reload = (ROOT / "scripts" / "linux_extension_autoreload.sh").read_text(encoding="utf-8")

    assert '"/home/chat2api/.cache/chat2api-chrome-for-testing/chrome"' in remote
    assert '"--class", ".*[Cc]hrome.*"' in remote
    for source in (watchdog, reload):
        assert 'grep -F -- "--user-data-dir=${PROFILE_DIR}"' in source
        assert "google-chrome.*user-data-dir" not in source


def test_linux_worker_extension_binding_remains_automatic_and_has_no_manual_pairing_code():
    agent = (ROOT / "scripts" / "linux_worker_agent.py").read_text(encoding="utf-8")
    binding = (ROOT / "app" / "linux_worker_bridge_binding.py").read_text(encoding="utf-8")
    extension = (ROOT / "chrome_extension" / "background_worker_binding_v30.js").read_text(encoding="utf-8")

    assert "/api/workers/extension-binding-ticket" in agent
    assert "inject_worker_binding" in agent
    assert "/api/extensions/worker-bind" in binding
    assert "pairing_id=None" in binding
    assert 'pairingCode: ""' in extension
    assert "X-Pairing-Code" not in extension


def test_shell_launchers_are_syntax_valid():
    for relative in ("scripts/bootstrap_linux_worker.sh", "scripts/linux_worker_chrome_launcher.sh"):
        result = subprocess.run(["bash", "-n", str(ROOT / relative)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


def test_runtime_marks_extension_autoload_release_without_changing_bridge_protocol_version():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    assert _runtime_version(runtime) >= (0, 22, 16)
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
