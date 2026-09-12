from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_v127_single_device_controller_retires_per_slot_agents_and_installers():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    bootstrap = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    assert 'SERVER_RUNTIME_VERSION = "0.22.83"' in runtime
    assert '"linux_worker_device_controller_agent_v127": True' in runtime
    assert '"linux_worker_shared_device_pairing_v127": True' in runtime
    assert '"linux_worker_shared_device_proxy_v127": True' in runtime
    assert '"linux_worker_profile_only_isolation_v127": True' in runtime
    assert '"linux_worker_same_host_slots_v1": False' in runtime
    assert 'linux_worker_device_controller.py' in bootstrap
    assert 'linux_worker_slot_agent.py' not in docker
    assert 'linux_worker_slot_install.sh' not in docker
    assert 'linux_worker_slot_install_reported.sh' not in docker


def test_v127_worker_add_is_direct_and_shares_device_pairing_and_proxy():
    authority = (ROOT / "app" / "linux_worker_device_authority_v124_patch.py").read_text(encoding="utf-8")
    ui = (ROOT / "app" / "admin_linux_device_authority_v124.js").read_text(encoding="utf-8")
    assert '"provision_worker"' in authority
    assert 'shared_device_pairing' in authority
    assert 'shared_device_proxy' in authority
    assert 'profile_only_isolation' in authority
    add = authority.split('async def add_linux_device_worker',1)[1].split('@app.delete',1)[0]
    assert 'pairing_id' not in add
    assert 'proxy_id' not in add
    assert 'create_install(' not in add
    assert 'provision_worker' in add
    assert 'linux_worker_slot_install' not in authority
    assert 'slot_installations' not in authority
    assert 'id="linuxAddWorkerV124"' not in ui
    assert 'Worker ${r.worker_slot} 已由设备总控 Agent 创建' in ui


def test_v127_device_and_worker_ui_contract():
    ui = (ROOT / "app" / "admin_linux_device_authority_v124.js").read_text(encoding="utf-8")
    header = ui.split('id="linuxDeviceTableV124"',1)[1].split('</thead>',1)[0]
    assert '<th>状态</th>' in header
    assert '<th>ChatGPT</th>' not in header
    assert 'deviceInstallStatus' in ui
    manager = ui.split('function renderManager()',1)[1].split('function renamePairingHeader',1)[0]
    assert '<th>代理</th>' not in manager
    assert '<th>ChatGPT</th>' in manager
    assert 'data-worker-action="initialize"' in manager
    assert 'data-login-worker=' in manager
    assert 'data-diagnostics=' in manager


def test_controller_and_root_helper_parse():
    py = subprocess.run(['python','-m','py_compile',str(ROOT/'scripts/linux_worker_device_controller.py')],capture_output=True,text=True,check=False)
    assert py.returncode == 0, py.stderr
    sh = subprocess.run(['bash','-n',str(ROOT/'scripts/linux_worker_device_controller_helper.sh')],capture_output=True,text=True,check=False)
    assert sh.returncode == 0, sh.stderr
