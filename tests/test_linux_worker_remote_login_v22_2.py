from pathlib import Path
import re

import pytest

from app.linux_worker_login_sessions import LOGIN_SESSION_IDLE_SECONDS, LoginSessionStore


ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


def _runtime_version(source: str) -> tuple[int, int, int]:
    match = re.search(r'SERVER_RUNTIME_VERSION = "(\d+)\.(\d+)\.(\d+)"', source)
    assert match
    return tuple(map(int, match.groups()))


def test_login_session_ticket_is_memory_only_worker_bound_single_session_and_expires():
    clock = Clock()
    store = LoginSessionStore(now=clock, idle_seconds=120)

    first = store.issue("wrk_one")
    assert first.startswith("lgn_")
    assert first not in repr(store._sessions)
    assert store.require("wrk_one", first).worker_id == "wrk_one"
    with pytest.raises(KeyError):
        store.require("wrk_two", first)

    second = store.issue("wrk_one")
    assert second != first
    with pytest.raises(KeyError):
        store.require("wrk_one", first)
    assert store.require("wrk_one", second).worker_id == "wrk_one"

    clock.value += 121
    with pytest.raises(KeyError):
        store.require("wrk_one", second)
    assert not store.has_worker_session("wrk_one")


def test_login_session_ignores_stale_ready_and_requires_fresh_login_transition():
    store = LoginSessionStore(idle_seconds=120)
    ticket = store.issue("wrk_login", baseline_login_checked_at_ms=1000)
    session = store.require("wrk_login", ticket, touch=False)

    assert session.observe_login(checked_at_ms=1000, state="ready", composer_ready=True) is False
    assert session.observe_login(checked_at_ms=1100, state="ready", composer_ready=True) is False
    assert session.saw_login_required is False
    assert session.observe_login(checked_at_ms=1200, state="login_required", composer_ready=False) is False
    assert session.saw_login_required is True
    assert session.observe_login(checked_at_ms=1200, state="ready", composer_ready=True) is False
    assert session.observe_login(checked_at_ms=1300, state="ready", composer_ready=False) is False
    assert session.observe_login(checked_at_ms=1400, state="ready", composer_ready=True) is True


def test_remote_login_helper_uses_xvfb_capture_and_xdotool_without_listener():
    source = (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")
    for token in (
        'DISPLAY = os.environ.get("CHAT2API_LOGIN_DISPLAY", ":99")',
        'SESSION_IDLE_SECONDS = int(os.environ.get("CHAT2API_LOGIN_SESSION_IDLE_SECONDS", "1200"))',
        'MAX_FRAME_BYTES = 1_500_000',
        '["xwd", "-display", DISPLAY, "-root", "-silent"]',
        '"convert"',
        '["xdotool", *args]',
        '["xdotool", "-"]',
        'input=script',
        'max(0, min(SOURCE_WIDTH - 1',
        'max(0, min(SOURCE_HEIGHT - 1',
    ):
        assert token in source
    lowered = source.lower()
    assert "http.server" not in lowered
    assert "import websockets" not in lowered
    assert ".listen(" not in lowered
    assert "5900" not in source
    assert "6080" not in source
    assert '_run_xdotool_stdin(["type", "--clearmodifiers", "--delay", "0", key])' in source
    assert '_run_xdotool(["type", "--clearmodifiers", "--delay", "0", key])' not in source


def test_bootstrap_installs_only_headless_capture_dependencies_not_desktop_or_remote_ports():
    source = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    assert "x11-apps xdotool imagemagick" in source
    assert "Environment=DISPLAY=:99" in source
    assert 'agent_version:"0.3.2"' in source
    lowered = source.lower()
    for forbidden in ("x11vnc", "novnc", "xrdp", "xfce", "gnome", "5900", "6080", "3389"):
        assert forbidden not in lowered


def test_remote_login_control_plane_requires_admin_plus_worker_bound_ticket():
    source = (ROOT / "app" / "linux_worker_patch.py").read_text(encoding="utf-8")
    freshness = (ROOT / "app" / "linux_worker_login_freshness_patch.py").read_text(encoding="utf-8")
    for token in (
        'LOGIN_TICKET_HEADER = "x-chat2api-login-ticket"',
        'LoginSessionStore()',
        '"/api/admin/linux-workers/{worker_id}/login-session"',
        '"/api/admin/linux-workers/{worker_id}/login-session/frame"',
        '"/api/admin/linux-workers/{worker_id}/login-session/input"',
        'require_login_ticket(worker_id, request)',
        '"open_login_session"',
        '"close_login_session"',
        '"login_session_frame"',
        '"login_session_input"',
        'len(frame) > 2_100_000',
    ):
        assert token in source
    assert 'session.observe_login(' in freshness
    assert 'baseline = _bridge_login_observation(worker)["checked_at_ms"]' in freshness
    assert 'send_worker_command(worker_id, "login_session_frame"' in freshness
    assert 'send_worker_command(worker_id, "close_login_session"' in freshness
    assert LOGIN_SESSION_IDLE_SECONDS == 20 * 60


def test_admin_remote_login_is_direct_browser_interaction_not_password_form():
    source = (ROOT / "app" / "admin_linux_workers.js").read_text(encoding="utf-8")
    for token in (
        "远程登录 ChatGPT",
        'id="linuxLoginFrame"',
        'id="linuxLoginKeyboardSink"',
        "X-Chat2API-Login-Ticket",
        "/login-session/frame",
        "/login-session/input",
        'data-login="${esc(row.worker_id)}"',
        'keyboardSink.addEventListener("keydown"',
        'keyboardSink.addEventListener("paste"',
        'remoteImage.addEventListener("wheel"',
        "chat2api 不保存这些输入内容",
        "登录状态已确认，正在自动关闭远程会话",
        "setTimeout(() => closeLoginDialog(), 650)",
    ):
        assert token in source
    lowered = source.lower()
    assert 'type="password"' not in lowered
    assert "ws://" not in lowered
    assert "wss://" not in lowered
    assert "novnc" not in lowered
    assert 'addEventListener("dblclick"' not in source
    assert 'const {headers = {}, ...rest} = options;' in source


def test_worker_agent_implements_low_latency_remote_login_without_privilege_escalation():
    source = (ROOT / "scripts" / "linux_worker_agent.py").read_text(encoding="utf-8")
    assert 'AGENT_VERSION = "0.3.4"' in source
    for command in ("open_login_session", "close_login_session", "login_session_frame", "login_session_input"):
        assert f'"{command}"' in source
    assert "capture_frame()" in source
    assert "send_input(args)" in source
    assert "HEARTBEAT_SECONDS = 15.0" in source
    assert "timeout = max(0.05, next_heartbeat - time.monotonic())" in source
    assert "await asyncio.sleep(15)" not in source
    assert "sudo" not in (ROOT / "scripts" / "linux_worker_remote_login.py").read_text(encoding="utf-8")


def test_remote_login_runtime_tracks_binding_upgrade():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert _runtime_version(runtime) >= (0, 22, 4)
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert "install_linux_worker_login_freshness_patch(app)" in entry
    assert entry.index("install_linux_worker_patch(app)") < entry.index("install_linux_worker_login_freshness_patch(app)")
