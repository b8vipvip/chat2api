from pathlib import Path

path = Path('/tmp/v126_transform.py')
script = path.read_text(encoding='utf-8')

# Current diagnostics script spells the variable with braces.
script = '\n'.join(
    line.replace('$WORKER_PYTHON', '${WORKER_PYTHON}')
    if 'diagnostics CDP argument' in line else line
    for line in script.splitlines()
) + '\n'

begin = script.index('# Production image contract tracks both changed surfaces.')
finish = script.index('# Current-version tests are release snapshots.', begin)
smoke = r'''# Production image contract tracks both changed surfaces.
path = '.github/workflows/production-image-smoke.yml'
text = read(path)
text = replace_once(
    text,
    '                  "scripts/linux_worker_slot_install_reported.sh",\n',
    '                  "scripts/linux_worker_slot_install_reported.sh",\n                  "scripts/linux_worker_initialize.sh",\n                  "scripts/linux_worker_diagnostics.sh",\n',
    'production bundle isolated helper requirements',
)
text = replace_once(
    text,
    '          assert manifest["version"] == "0.8.34"',
    '          assert manifest["version"] == "0.8.35"',
    'production bundle version',
)
text = replace_once(
    text,
    "          assert payload['server']['runtime_version'] == '0.22.81'",
    "          assert payload['server']['runtime_version'] == '0.22.82'",
    'production runtime version',
)
text = replace_once(
    text,
    "          assert payload['chrome_bridge']['bundle_version'] == '0.8.34'",
    "          assert payload['chrome_bridge']['bundle_version'] == '0.8.35'",
    'production boot bundle version',
)
source_anchor = '          authority = Path("/app/app/linux_worker_device_authority_v124_patch.py").read_text()\n'
source_extra = source_anchor + (
    '          runtime = Path("/app/app/runtime_contract.py").read_text()\n'
    '          device_ui = Path("/app/app/admin_linux_device_authority_v124.js").read_text()\n'
    '          initialize_helper = Path("/app/scripts/linux_worker_initialize.sh").read_text()\n'
    '          diagnostics_helper = Path("/app/scripts/linux_worker_diagnostics.sh").read_text()\n'
    '          slot_installer = Path("/app/scripts/linux_worker_slot_install.sh").read_text()\n'
)
text = replace_once(text, source_anchor, source_extra, 'production isolated source loads')
assert_anchor = '          assert "_repair_install_command_origin" in authority\n'
assert_extra = assert_anchor + (
    '          assert "\\\"linux_worker_isolated_management_v126\\\": True" in runtime\n'
    '          assert "\\\"linux_worker_slot_isolated_ops_v126\\\": True" in runtime\n'
    '          assert "Worker版本" in device_ui\n'
    '          assert "data-manage-device" in device_ui\n'
    '          assert "manageDeviceWorkersV124" not in device_ui\n'
    '          assert "managerDeviceV124" not in device_ui\n'
    '          assert "chat2api-worker-initialize-slot([0-9]+)" in initialize_helper\n'
    '          assert "chat2api-worker-diagnostics-slot([0-9]+)" in diagnostics_helper\n'
    '          assert "CHAT2API_INITIALIZE_HELPER=${INITIALIZE_HELPER}" in slot_installer\n'
    '          assert "CHAT2API_DIAGNOSTICS_HELPER=${DIAGNOSTICS_HELPER}" in slot_installer\n'
)
text = replace_once(text, assert_anchor, assert_extra, 'production isolated source assertions')
feature_anchor = "          assert payload['features']['linux_worker_public_https_origin_v125'] is True\n"
feature_extra = feature_anchor + (
    "          assert payload['features']['linux_worker_isolated_management_v126'] is True\n"
    "          assert payload['features']['linux_worker_slot_isolated_ops_v126'] is True\n"
)
text = replace_once(text, feature_anchor, feature_extra, 'production v126 feature assertions')
text = replace_once(
    text,
    "          assert '管理设备 Worker' in v124",
    "          assert '管理设备 Worker' not in v124\n          assert 'Worker版本' in v124\n          assert 'data-manage-device' in v124",
    'production v126 admin UI assertion',
)
write(path, text)

'''
script = script[:begin] + smoke + script[finish:]
path.write_text(script, encoding='utf-8')
