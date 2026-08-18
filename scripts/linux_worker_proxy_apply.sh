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
TEST_URL="https://chatgpt.com/"

if [[ "${EUID}" -ne 0 ]]; then
  printf '%s\n' '{"ok":false,"error":"root_required"}'
  exit 1
fi

umask 077
work_dir="$(mktemp -d /tmp/chat2api-proxy-apply.XXXXXX)"
candidate="${work_dir}/xray.candidate.json"
backup="${work_dir}/xray.previous.json"
cleanup() { rm -rf "${work_dir}"; }
trap cleanup EXIT

cat >"${candidate}"

if [[ ! -s "${candidate}" ]]; then
  printf '%s\n' '{"ok":false,"error":"empty_config"}'
  exit 2
fi

if ! python3 -m json.tool "${candidate}" >/dev/null 2>&1; then
  printf '%s\n' '{"ok":false,"error":"invalid_json"}'
  exit 2
fi

if ! "${XRAY_BIN}" run -test -c "${candidate}" >/dev/null 2>&1; then
  printf '%s\n' '{"ok":false,"error":"xray_config_test_failed"}'
  exit 3
fi

had_previous=0
if [[ -s "${XRAY_CONFIG}" ]]; then
  cp -p "${XRAY_CONFIG}" "${backup}"
  had_previous=1
fi

rollback() {
  if [[ "${had_previous}" -eq 1 && -s "${backup}" ]]; then
    install -o root -g chat2api -m 640 "${backup}" "${XRAY_CONFIG}"
    systemctl restart "${XRAY_UNIT}" >/dev/null 2>&1 || true
    for _ in {1..20}; do
      if (echo >/dev/tcp/127.0.0.1/${PROXY_PORT}) >/dev/null 2>&1; then break; fi
      sleep 0.25
    done
    systemctl restart "${CHROME_UNIT}" >/dev/null 2>&1 || true
  fi
}

install -o root -g chat2api -m 640 "${candidate}" "${XRAY_CONFIG}"

if ! systemctl restart "${XRAY_UNIT}" >/dev/null 2>&1; then
  rollback
  printf '%s\n' '{"ok":false,"error":"xray_restart_failed","rolled_back":true}'
  exit 4
fi

listener_ready=0
for _ in {1..40}; do
  if (echo >/dev/tcp/127.0.0.1/${PROXY_PORT}) >/dev/null 2>&1; then
    listener_ready=1
    break
  fi
  sleep 0.25
done
if [[ "${listener_ready}" -ne 1 ]]; then
  rollback
  printf '%s\n' '{"ok":false,"error":"proxy_listener_not_ready","rolled_back":true}'
  exit 5
fi

http_code="$(curl --proxy "socks5h://127.0.0.1:${PROXY_PORT}" --connect-timeout 10 --max-time 20 -sS -o /dev/null -w '%{http_code}' "${TEST_URL}" 2>/dev/null || true)"
if [[ -z "${http_code}" || "${http_code}" == "000" ]]; then
  rollback
  printf '%s\n' '{"ok":false,"error":"proxy_connectivity_test_failed","rolled_back":true}'
  exit 6
fi

# Ensure the browser is alive after the dependency restart and uses the newly
# validated local SOCKS listener. This preserves the persistent Chrome profile.
systemctl restart "${CHROME_UNIT}" >/dev/null 2>&1 || true

printf '{"ok":true,"http_status":"%s","rolled_back":false}\n' "${http_code}"
