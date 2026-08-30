#!/usr/bin/env bash
set -u

PROXY_PORT="${CHAT2API_PROXY_PORT:-${PROXY_PORT:-10808}}"
PROXY_URL="socks5h://127.0.0.1:${PROXY_PORT}"
CONNECT_TIMEOUT_SECONDS="${CHAT2API_GENERATION_PROBE_CONNECT_TIMEOUT_SECONDS:-5}"
MAX_TIME_SECONDS="${CHAT2API_GENERATION_PROBE_MAX_TIME_SECONDS:-8}"

# Landing-page reachability is not enough to prove that a Worker can complete a
# ChatGPT generation. Production failures showed chatgpt.com remaining healthy
# while the browser's generation transports to bzr.openai.com / ws.chatgpt.com
# were closed. Keep this probe fixed-scope and credential-free so it is safe to
# run from the unprivileged Agent, the root watchdog, and the proxy transaction.
PROBES=(
  "chatgpt_home|https://chatgpt.com/"
  "generation_bzr|https://bzr.openai.com/"
  "generation_ws|https://ws.chatgpt.com/"
)

overall_ready=1
for spec in "${PROBES[@]}"; do
  name="${spec%%|*}"
  url="${spec#*|}"
  output=""
  rc=0
  if output="$(curl \
      --proxy "${PROXY_URL}" \
      --connect-timeout "${CONNECT_TIMEOUT_SECONDS}" \
      --max-time "${MAX_TIME_SECONDS}" \
      --silent --show-error \
      --output /dev/null \
      --write-out '%{http_code}|%{time_connect}|%{time_appconnect}|%{time_total}' \
      "${url}" 2>/dev/null)"; then
    rc=0
  else
    rc=$?
  fi

  IFS='|' read -r http_code connect_s tls_s total_s <<<"${output:-000|0|0|0}"
  http_code="${http_code:-000}"
  connect_s="${connect_s:-0}"
  tls_s="${tls_s:-0}"
  total_s="${total_s:-0}"
  if [[ "${rc}" -eq 0 && "${http_code}" != "000" ]]; then
    printf 'probe=%s ok=true http_status=%s curl_exit=%s connect_s=%s tls_s=%s total_s=%s\n' \
      "${name}" "${http_code}" "${rc}" "${connect_s}" "${tls_s}" "${total_s}"
  else
    overall_ready=0
    printf 'probe=%s ok=false http_status=%s curl_exit=%s connect_s=%s tls_s=%s total_s=%s\n' \
      "${name}" "${http_code}" "${rc}" "${connect_s}" "${tls_s}" "${total_s}"
  fi
done

if [[ "${overall_ready}" -eq 1 ]]; then
  printf 'generation_backend_ready=true\n'
  exit 0
fi
printf 'generation_backend_ready=false\n'
exit 1
