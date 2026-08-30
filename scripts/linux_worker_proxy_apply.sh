#!/usr/bin/env bash
set -euo pipefail

# Fixed privileged paths: the unprivileged Worker agent is allowed to invoke
# this exact helper, but it must not be able to redirect the root helper toward
# arbitrary files or units through environment variables or command arguments.
XRAY_BIN="/usr/local/bin/xray"
XRAY_CONFIG="/etc/chat2api-worker/xray.json"
XRAY_UNIT="chat2api-xray.service"
CHROME_UNIT="chat2api-chrome.service"
PROXY_PORT="10808"
GENERATION_PROBE="/opt/chat2api-worker/scripts/linux_worker_generation_probe.sh"
WORKSPACE_PARENT="/etc/chat2api-worker"

RESULT_EMITTED=0
CURRENT_STAGE="startup"
work_dir=""

emit_error() {
  local error="$1" rolled_back="${2:-false}" exit_code="${3:-1}"
  RESULT_EMITTED=1
  printf '{"ok":false,"error":"%s","stage":"%s","rolled_back":%s,"exit_code":%s}\n' \
    "$error" "$CURRENT_STAGE" "$rolled_back" "$exit_code"
}

on_exit() {
  local rc=$?
  [[ -n "$work_dir" ]] && rm -rf "$work_dir"
  if [[ $rc -ne 0 && $RESULT_EMITTED -ne 1 ]]; then
    printf '{"ok":false,"error":"proxy_helper_failed","stage":"%s","rolled_back":false,"exit_code":%s}\n' \
      "$CURRENT_STAGE" "$rc"
  fi
}
trap on_exit EXIT

if [[ "${EUID}" -ne 0 ]]; then
  CURRENT_STAGE="privilege_check"
  emit_error "root_required" false 1
  exit 1
fi

umask 077
CURRENT_STAGE="temporary_workspace"
# The Worker Agent runs with ProtectSystem=strict and explicitly grants write
# access only to /etc/chat2api-worker. A sudo child stays inside that mount
# namespace, so /tmp may be read-only even though the helper itself is root.
# Keep the short-lived candidate/rollback files inside the already allowlisted
# Worker configuration directory and remove them via the EXIT trap above.
work_dir="$(mktemp -d "${WORKSPACE_PARENT}/.proxy-apply.XXXXXX")"
candidate="${work_dir}/xray.candidate.json"
backup="${work_dir}/xray.previous.json"

CURRENT_STAGE="read_candidate"
cat >"${candidate}"
if [[ ! -s "${candidate}" ]]; then
  emit_error "empty_config" false 2
  exit 2
fi

CURRENT_STAGE="json_validation"
if ! python3 -m json.tool "${candidate}" >/dev/null 2>&1; then
  emit_error "invalid_json" false 2
  exit 2
fi

CURRENT_STAGE="xray_config_test"
if [[ ! -x "${XRAY_BIN}" ]]; then
  emit_error "xray_binary_missing" false 3
  exit 3
fi
if ! "${XRAY_BIN}" run -test -c "${candidate}" >/dev/null 2>&1; then
  emit_error "xray_config_test_failed" false 3
  exit 3
fi

had_previous=0
CURRENT_STAGE="backup_previous_config"
if [[ -s "${XRAY_CONFIG}" ]]; then
  cp -p "${XRAY_CONFIG}" "${backup}"
  had_previous=1
fi

rollback() {
  local ok=1
  if [[ "${had_previous}" -eq 1 && -s "${backup}" ]]; then
    install -o root -g chat2api -m 640 "${backup}" "${XRAY_CONFIG}" || ok=0
    systemctl restart "${XRAY_UNIT}" >/dev/null 2>&1 || ok=0
    for _ in {1..20}; do
      if (echo >/dev/tcp/127.0.0.1/${PROXY_PORT}) >/dev/null 2>&1; then break; fi
      sleep 0.25
    done
    systemctl restart "${CHROME_UNIT}" >/dev/null 2>&1 || ok=0
  else
    ok=0
  fi
  [[ $ok -eq 1 ]]
}

CURRENT_STAGE="install_candidate"
install -o root -g chat2api -m 640 "${candidate}" "${XRAY_CONFIG}"

CURRENT_STAGE="restart_xray"
if ! systemctl restart "${XRAY_UNIT}" >/dev/null 2>&1; then
  if rollback; then
    emit_error "xray_restart_failed" true 4
  else
    emit_error "xray_restart_failed_rollback_failed" false 4
  fi
  exit 4
fi

CURRENT_STAGE="wait_socks_listener"
listener_ready=0
for _ in {1..40}; do
  if (echo >/dev/tcp/127.0.0.1/${PROXY_PORT}) >/dev/null 2>&1; then
    listener_ready=1
    break
  fi
  sleep 0.25
done
if [[ "${listener_ready}" -ne 1 ]]; then
  if rollback; then
    emit_error "proxy_listener_not_ready" true 5
  else
    emit_error "proxy_listener_not_ready_rollback_failed" false 5
  fi
  exit 5
fi

# Do not accept a node merely because chatgpt.com renders. A real generation
# also depends on OpenAI/ChatGPT backend transports. The shared probe performs
# TLS/HTTP handshakes to the landing page plus bzr.openai.com and ws.chatgpt.com
# through the exact local SOCKS path Chrome uses.
CURRENT_STAGE="generation_backend_connectivity_test"
if [[ ! -x "${GENERATION_PROBE}" ]]; then
  if rollback; then
    emit_error "generation_probe_missing" true 6
  else
    emit_error "generation_probe_missing_rollback_failed" false 6
  fi
  exit 6
fi
probe_output=""
if ! probe_output="$(CHAT2API_PROXY_PORT="${PROXY_PORT}" "${GENERATION_PROBE}" 2>&1)"; then
  printf '%s\n' "${probe_output}" >&2
  if rollback; then
    emit_error "generation_backend_connectivity_test_failed" true 6
  else
    emit_error "generation_backend_connectivity_test_failed_rollback_failed" false 6
  fi
  exit 6
fi
http_code="$(printf '%s\n' "${probe_output}" | sed -n 's/^probe=chatgpt_home .*http_status=\([^ ]*\).*/\1/p' | tail -n1)"
http_code="${http_code:-000}"

# Ensure the browser is alive after the dependency restart and uses the newly
# validated local SOCKS listener. This preserves the persistent Chrome profile.
CURRENT_STAGE="restart_chrome"
systemctl restart "${CHROME_UNIT}" >/dev/null 2>&1 || true

CURRENT_STAGE="complete"
RESULT_EMITTED=1
printf '{"ok":true,"http_status":"%s","generation_backend_ready":true,"rolled_back":false,"stage":"complete"}\n' "${http_code}"
