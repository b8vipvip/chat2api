#!/usr/bin/env bash
set -u

PROXY_PORT="${CHAT2API_PROXY_PORT:-${PROXY_PORT:-10808}}"
PROXY_URL="socks5h://127.0.0.1:${PROXY_PORT}"
CONNECT_TIMEOUT_SECONDS="${CHAT2API_GENERATION_PROBE_CONNECT_TIMEOUT_SECONDS:-5}"
MAX_TIME_SECONDS="${CHAT2API_GENERATION_PROBE_MAX_TIME_SECONDS:-8}"
NETWORK_URL="${CHAT2API_NETWORK_PROBE_URL:-https://ipwho.is/}"

# A configured Xray listener is only the local half of proxy health. Report four
# distinct facts for the console: configured is derived from Worker state, while
# this probe measures general Internet reachability, real ChatGPT generation
# routes, and an end-to-end proxy latency sample.
#
# The ChatGPT probes are intentionally credential-free: 401/403/405 are healthy
# transport outcomes because they prove DNS + SOCKS + TLS + HTTP routing reached
# the real endpoint. Only curl transport failure / HTTP 000 is unhealthy.
#
# Do NOT use bzr.openai.com as a text-generation health gate. It is browser
# telemetry/measurement traffic and can fail independently. ws.chatgpt.com is
# likewise not a hard gate for ordinary text generation.
PROBES=(
  "network_access|GET|${NETWORK_URL}"
  "chatgpt_home|GET|https://chatgpt.com/"
  "conversation_route|POST|https://chatgpt.com/backend-api/f/conversation"
  "sentinel_route|POST|https://chatgpt.com/backend-api/sentinel/chat-requirements"
)

overall_ready=1
network_ready=0
network_total_s="0"
chatgpt_home_total_s="0"
for spec in "${PROBES[@]}"; do
  name="${spec%%|*}"
  rest="${spec#*|}"
  method="${rest%%|*}"
  url="${rest#*|}"
  output=""
  rc=0
  curl_args=(
    --proxy "${PROXY_URL}"
    --connect-timeout "${CONNECT_TIMEOUT_SECONDS}"
    --max-time "${MAX_TIME_SECONDS}"
    --silent --show-error
    --output /dev/null
    --write-out '%{http_code}|%{time_connect}|%{time_appconnect}|%{time_total}'
  )
  if [[ "${method}" == "POST" ]]; then
    curl_args+=(--request POST --header 'Content-Type: application/json' --header 'Accept: text/event-stream' --data '{}')
  fi
  if output="$(curl "${curl_args[@]}" "${url}" 2>/dev/null)"; then
    rc=0
  else
    rc=$?
  fi

  IFS='|' read -r http_code connect_s tls_s total_s <<<"${output:-000|0|0|0}"
  http_code="${http_code:-000}"
  connect_s="${connect_s:-0}"
  tls_s="${tls_s:-0}"
  total_s="${total_s:-0}"
  ok=false
  if [[ "${rc}" -eq 0 && "${http_code}" != "000" ]]; then
    ok=true
  fi

  printf 'probe=%s method=%s ok=%s http_status=%s curl_exit=%s connect_s=%s tls_s=%s total_s=%s\n' \
    "${name}" "${method}" "${ok}" "${http_code}" "${rc}" "${connect_s}" "${tls_s}" "${total_s}"

  if [[ "${name}" == "network_access" ]]; then
    network_total_s="${total_s}"
    [[ "${ok}" == "true" ]] && network_ready=1
  else
    [[ "${name}" == "chatgpt_home" ]] && chatgpt_home_total_s="${total_s}"
    [[ "${ok}" != "true" ]] && overall_ready=0
  fi
done

latency_source_s="${network_total_s}"
if [[ "${network_ready}" -ne 1 ]]; then
  latency_source_s="${chatgpt_home_total_s}"
fi
latency_ms="$(awk -v value="${latency_source_s:-0}" 'BEGIN { n=value+0; if (n <= 0) print 0; else printf "%d", (n*1000)+0.5 }' 2>/dev/null || printf '0')"
checked_at_epoch="$(date +%s)"

printf 'proxy_network_ready=%s\n' "$([[ "${network_ready}" -eq 1 ]] && printf true || printf false)"
printf 'proxy_chatgpt_ready=%s\n' "$([[ "${overall_ready}" -eq 1 ]] && printf true || printf false)"
printf 'proxy_latency_ms=%s\n' "${latency_ms:-0}"
printf 'proxy_checked_at_epoch=%s\n' "${checked_at_epoch}"

if [[ "${overall_ready}" -eq 1 ]]; then
  printf 'generation_backend_ready=true\n'
  exit 0
fi
printf 'generation_backend_ready=false\n'
exit 1
