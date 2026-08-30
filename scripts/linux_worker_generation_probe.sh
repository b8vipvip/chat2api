#!/usr/bin/env bash
set -u

PROXY_PORT="${CHAT2API_PROXY_PORT:-${PROXY_PORT:-10808}}"
PROXY_URL="socks5h://127.0.0.1:${PROXY_PORT}"
CONNECT_TIMEOUT_SECONDS="${CHAT2API_GENERATION_PROBE_CONNECT_TIMEOUT_SECONDS:-5}"
MAX_TIME_SECONDS="${CHAT2API_GENERATION_PROBE_MAX_TIME_SECONDS:-8}"

# A configured Xray listener and a reachable ChatGPT landing page are necessary
# but not sufficient for text generation. Probe the same chatgpt.com control
# paths that the SPA uses before/while sending a text turn. These probes are
# intentionally credential-free: 401/403/405 are acceptable because they prove
# DNS + SOCKS + TLS + HTTP routing reached the real ChatGPT endpoint. Only a
# transport failure / HTTP 000 is considered unhealthy.
#
# Do NOT use bzr.openai.com as a text-generation health gate. It is used by
# OpenAI browser telemetry/measurement traffic and can fail independently of the
# conversation SSE path. ws.chatgpt.com is likewise not required for normal text
# generation (it is relevant to realtime/voice surfaces).
PROBES=(
  "chatgpt_home|GET|https://chatgpt.com/"
  "conversation_route|POST|https://chatgpt.com/backend-api/f/conversation"
  "sentinel_route|POST|https://chatgpt.com/backend-api/sentinel/chat-requirements"
)

overall_ready=1
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
  if [[ "${rc}" -eq 0 && "${http_code}" != "000" ]]; then
    printf 'probe=%s method=%s ok=true http_status=%s curl_exit=%s connect_s=%s tls_s=%s total_s=%s\n' \
      "${name}" "${method}" "${http_code}" "${rc}" "${connect_s}" "${tls_s}" "${total_s}"
  else
    overall_ready=0
    printf 'probe=%s method=%s ok=false http_status=%s curl_exit=%s connect_s=%s tls_s=%s total_s=%s\n' \
      "${name}" "${method}" "${http_code}" "${rc}" "${connect_s}" "${tls_s}" "${total_s}"
  fi
done

if [[ "${overall_ready}" -eq 1 ]]; then
  printf 'generation_backend_ready=true\n'
  exit 0
fi
printf 'generation_backend_ready=false\n'
exit 1