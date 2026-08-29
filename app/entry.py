from .main import app
from .voice_patch import install_voice_patch
from .live_voice_patch import install_live_voice_patch
from .v7_patch import install_v7_patch
from .v8_patch import install_v8_patch
from .v9_patch import install_v9_patch
from .v10_patch import install_v10_patch
from .v11_patch import install_v11_patch
from .v12_patch import install_v12_patch
from .v13_patch import install_v13_patch
from .v14_patch import install_v14_patch
from .v15_patch import install_v15_patch
from .v16_patch import install_v16_patch
from .v17_patch import install_v17_patch
from .v17_crypto_patch import install_v17_crypto_patch
from .v17_route_migration_patch import install_v17_route_migration_patch
from .v17_finalize_patch import install_v17_finalize_patch
from .v17_1_patch import install_v17_1_patch
from .v18_patch import install_v18_patch
from .v19_patch import install_v19_patch
from .v20_patch import install_v20_patch
from .v20_1_patch import install_v20_1_patch
from .v20_2_patch import install_v20_2_patch
from .v20_3_patch import install_v20_3_patch
from .v21_patch import install_v21_patch
from .v21_routing_patch import install_v21_routing_patch
from .v21_1_patch import install_v21_1_patch
from .v21_2_patch import install_v21_2_patch
from .v21_3_patch import install_v21_3_patch
from .v21_4_model_contract_patch import install_v21_4_model_contract_patch
from .v21_5_patch import install_v21_5_patch
from .v21_6_patch import install_v21_6_patch
from .v21_13_patch import install_v21_13_patch
from .linux_worker_patch import install_linux_worker_patch
from .linux_worker_login_freshness_patch import install_linux_worker_login_freshness_patch
from .linux_worker_install_patch import install_linux_worker_install_patch
from .linux_worker_xray_patch import install_linux_worker_xray_patch
from .linux_worker_bridge_binding import install_linux_worker_bridge_binding_patch
from .linux_worker_ui_state_patch import install_linux_worker_ui_state_patch
from .linux_worker_proxy_catalog_patch import install_linux_worker_proxy_catalog_patch
from .linux_worker_pairing_patch import install_linux_worker_pairing_patch
from .linux_worker_proxy_name_patch import install_linux_worker_proxy_name_patch
from .linux_worker_table_stability_patch import install_linux_worker_table_stability_patch
from .linux_worker_install_ux_patch import install_linux_worker_install_ux_patch
from .linux_worker_repair_command_patch import install_linux_worker_repair_command_patch
from .linux_worker_diagnostics_patch import install_linux_worker_diagnostics_patch
from .linux_worker_initialize_patch import install_linux_worker_initialize_patch
from .linux_worker_upgrade_patch import install_linux_worker_upgrade_patch
from .linux_worker_enable_patch import install_linux_worker_enable_patch
from .linux_worker_console_polling_patch import install_linux_worker_console_polling_patch
from .stream_keepalive_patch import install_stream_keepalive_patch
from .request_stall_patch import install_request_stall_patch
from .request_recovery_patch import install_request_recovery_patch
from .worker_transport_recovery_patch import install_worker_transport_recovery_patch
from .request_device_identity_patch import install_request_device_identity_patch
from .playground_lifecycle_patch import install_playground_lifecycle_patch
from .playground_multimodal_defaults_patch import install_playground_multimodal_defaults_patch
from .playground_random_prompt_patch import install_playground_random_prompt_patch
from .runtime_contract import install_runtime_contract
from .runtime_logs_patch import install_runtime_logs_patch
from .extension_capacity_control_patch import install_extension_capacity_control_patch
from .mini_multimodal_quota_patch import install_mini_multimodal_quota_patch
from .server_update_patch import install_server_update_patch
from .server_worker_sync_patch import install_server_worker_sync_patch

install_voice_patch(app)
install_live_voice_patch(app)
install_v7_patch(app)
install_v8_patch(app)
install_v9_patch(app)
install_v10_patch(app)
install_v11_patch(app)
install_v12_patch(app)
install_v13_patch(app)
install_v14_patch(app)
install_v15_patch(app)
install_v16_patch(app)
install_v17_patch(app)
install_v17_crypto_patch(app)
install_v17_route_migration_patch(app)
install_v17_finalize_patch(app)
install_v17_1_patch(app)
install_v18_patch(app)
install_v19_patch(app)
install_v20_patch(app)
install_v20_1_patch(app)
install_v20_2_patch(app)
install_v20_3_patch(app)
install_v21_patch(app)
install_v21_routing_patch(app)
install_v21_1_patch(app)
install_v21_2_patch(app)
install_v21_3_patch(app)
install_v21_4_model_contract_patch(app)
install_v21_5_patch(app)
install_v21_6_patch(app)
install_v21_13_patch(app)
install_linux_worker_patch(app)
install_linux_worker_login_freshness_patch(app)
install_linux_worker_install_patch(app)
install_linux_worker_xray_patch(app)
install_linux_worker_bridge_binding_patch(app)
install_linux_worker_ui_state_patch(app)
install_linux_worker_proxy_catalog_patch(app)
install_stream_keepalive_patch(app)
install_request_stall_patch(app)
install_request_recovery_patch(app)
# Preserve active browser requests through brief Worker WebSocket reconnects.
# This sits after request-stall/recovery so the synthetic disconnect filter owns
# the final terminal decision while existing timeout/watchdog limits stay intact.
install_worker_transport_recovery_patch(app)
install_playground_lifecycle_patch(app)
# The console promises that an empty attachment chooser generates a default
# sample. Make vision/file tests dispatch real multimodal requests instead of
# being silently skipped when the administrator leaves the chooser empty.
install_playground_multimodal_defaults_patch(app)
# Every dispatched Playground case gets a fresh prompt variant and opaque marker.
# Install after multimodal defaults so generated vision/file samples flow through
# the same randomized chat boundary as administrator-supplied attachments.
install_playground_random_prompt_patch(app)

# Install the runtime contract after the historical patch stack so /version
# describes the production app rather than the legacy base layer in app.main.
install_runtime_contract(app)

# Capture server runtime logs before the final control-plane patches are installed
# so their diagnostics and exception traces are available from the admin console.
install_runtime_logs_patch(app)

# Live extension capacity controls add admin/Bridge control endpoints without
# owning the runtime version. They are installed after the runtime contract so
# the version surface remains stable while the control plane can evolve.
install_extension_capacity_control_patch(app)

# Keep gpt-5.5-mini vision/file routing enabled for Free accounts until the
# browser reports an actual ChatGPT quota reset time. This is installed after
# the historical routing stack so it decorates the final resolver/catalog.
install_mini_multimodal_quota_patch(app)

# These final Worker patches do not own the runtime version. They are installed
# last so the presentation assets can refine the legacy Worker console without
# changing the established Worker/Bridge transport contracts.
install_linux_worker_pairing_patch(app)
install_linux_worker_proxy_name_patch(app)
install_linux_worker_table_stability_patch(app)
install_linux_worker_install_ux_patch(app)
install_linux_worker_repair_command_patch(app)
install_linux_worker_diagnostics_patch(app)
# Initialization is deliberately installed after diagnostics: its bootstrap
# transformation extends the final helper/sudo rules and its admin button can
# use the stable diagnostics-tagged Worker rows.
install_linux_worker_initialize_patch(app)
# Online upgrade is installed last in the bounded remote-command Worker stack so
# it can extend the fully-patched bootstrap, including the v43 initialization
# helper and sudo rule.
install_linux_worker_upgrade_patch(app)
# The reversible routing toggle is presentation/routing-only and is deliberately
# installed after pairing. Pairing may keep the physical extension transport
# healthy, while this final boundary decides whether it is eligible for requests.
install_linux_worker_enable_patch(app)
# The Worker console polling guard is the final Worker presentation boundary.
# It serializes list refreshes, suppresses unchanged tbody rewrites, and lets the
# stable renderer consume the base page's shared snapshot instead of polling twice.
install_linux_worker_console_polling_patch(app)
# Request history resolves ext_* transport identities to the administrator's
# human device-code name and injects the canonical Worker/设备码 terminology layer.
install_request_device_identity_patch(app)

# Docker deployments cannot safely update their own host by exposing the Docker
# socket to the web container. The server update patch writes a bounded request
# into the persisted data volume; a one-time host systemd.path helper executes
# the fixed transactional updater outside the container.
install_server_update_patch(app)
# After the host updater has finished its health check, reconcile every Linux
# Worker with the newly deployed server. Worker-impacting GitHub diffs force a
# refresh even when the semantic bundle version did not change; server-only
# updates leave already-current Workers untouched. Offline Workers stay pending
# and continue automatically when they reconnect.
install_server_worker_sync_patch(app)
