from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_device_controller_owns_one_socket_and_multiplexes_logical_workers():
    controller=(ROOT/'scripts/linux_worker_device_controller.py').read_text(encoding='utf-8')
    server=(ROOT/'app/linux_worker_patch.py').read_text(encoding='utf-8')
    assert 'async with websockets.connect(config["websocket_url"]' in controller
    assert '"type": "worker.heartbeat"' in controller
    assert 'target_worker_id' in controller
    assert 'controller_worker_id' in server
    assert 'payload = {"type": "command"' in server


def test_extra_workers_share_proxy_and_only_get_profile_chrome_resources():
    helper=(ROOT/'scripts/linux_worker_device_controller_helper.sh').read_text(encoding='utf-8')
    docker=(ROOT/'Dockerfile').read_text(encoding='utf-8')
    assert 'PROFILE_DIR="/home/chat2api/.config/chat2api-chrome-worker-' in helper
    assert "printf '%02d'" in helper
    assert 'PROXY_PORT' in helper
    assert '"10808"' in helper
    assert 'DISPLAY_NUM=$((98 + SLOT))' in helper
    assert 'CDP_PORT=$((9221 + SLOT))' in helper
    assert 'Requires=chat2api-xray.service' in helper
    assert 'chat2api-worker-agent-slot' not in helper
    assert 'chat2api-xray-slot' not in helper
    assert 'linux_worker_slot_agent.py' not in docker
    assert 'linux_worker_slot_install.sh' not in docker
    assert 'linux_worker_slot_chrome_launcher.sh' in docker


def test_controller_helper_syntax():
    result=subprocess.run(['bash','-n',str(ROOT/'scripts/linux_worker_device_controller_helper.sh')],capture_output=True,text=True,check=False)
    assert result.returncode==0,result.stderr
