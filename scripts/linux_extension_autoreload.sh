#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/chat2api}"
WORKER_USER="${WORKER_USER:-chat2api}"
PROFILE_DIR="${PROFILE_DIR:-/home/${WORKER_USER}/.config/chat2api-chrome-worker-01}"
EXTENSION_DIR="${EXTENSION_DIR:-${REPO_DIR}/chrome_extension}"
CHROME_UNIT="${CHROME_UNIT:-chat2api-chrome.service}"
STATE_DIR="${STATE_DIR:-/var/lib/chat2api-worker}"
APPLIED_FILE="${STATE_DIR}/extension-applied.sha256"
FAILED_FILE="${STATE_DIR}/extension-failed.sha256"
STATE_FILE="${STATE_DIR}/extension-state.env"

log() {
  local level="$1"
  shift
  local message="$*"
  printf '%s [%s] %s\n' "$(date -Is)" "${level}" "${message}"
  logger -t chat2api-linux-extension-autoreload -- "[${level}] ${message}" 2>/dev/null || true
}

chrome_process_ready() {
  pgrep -u "${WORKER_USER}" -f "google-chrome.*user-data-dir=${PROFILE_DIR}" >/dev/null 2>&1
}

wait_for_chrome() {
  local attempts="${1:-30}"
  local delay="${2:-1}"
  local i
  for ((i=0; i<attempts; i++)); do
    if systemctl is-active --quiet "${CHROME_UNIT}" && chrome_process_ready; then
      return 0
    fi
    sleep "${delay}"
  done
  return 1
}

extension_version() {
  python3 - "${EXTENSION_DIR}/manifest.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("unknown")
else:
    print(str(data.get("version") or "unknown"))
PY
}

extension_fingerprint() {
  local repo_real extension_real extension_rel git_lock
  repo_real="$(realpath "${REPO_DIR}")"
  extension_real="$(realpath "${EXTENSION_DIR}")"

  if [[ "${extension_real}" == "${repo_real}/"* ]] && git -C "${repo_real}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    extension_rel="${extension_real#${repo_real}/}"
    git_lock="$(git -C "${repo_real}" rev-parse --git-path index.lock 2>/dev/null || true)"
    if [[ -n "${git_lock}" && "${git_lock}" != /* ]]; then
      git_lock="${repo_real}/${git_lock}"
    fi
    if [[ -n "${git_lock}" && -e "${git_lock}" ]]; then
      return 2
    fi
    if [[ -n "$(git -C "${repo_real}" status --porcelain --untracked-files=all -- "${extension_rel}" 2>/dev/null)" ]]; then
      return 3
    fi
    git -C "${repo_real}" rev-parse "HEAD:${extension_rel}" 2>/dev/null
    return
  fi

  find "${extension_real}" -type f -print0 \
    | sort -z \
    | while IFS= read -r -d '' file; do
        printf '%s\0' "${file#${extension_real}/}"
        sha256sum "${file}"
      done \
    | sha256sum \
    | awk '{print $1}'
}

write_state() {
  local fingerprint="$1"
  local version="$2"
  local commit="unknown"
  if git -C "${REPO_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    commit="$(git -C "${REPO_DIR}" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
  fi
  cat >"${STATE_FILE}" <<EOF
EXTENSION_VERSION=${version}
EXTENSION_FINGERPRINT=${fingerprint}
GIT_COMMIT=${commit}
APPLIED_AT=$(date -Is)
EOF
}

if [[ "${EUID}" -ne 0 ]]; then
  log ERROR "run as root so the production Chrome unit can be restarted"
  exit 1
fi

if ! id "${WORKER_USER}" >/dev/null 2>&1; then
  log ERROR "worker user does not exist: ${WORKER_USER}"
  exit 1
fi

if [[ ! -d "${PROFILE_DIR}" ]]; then
  log ERROR "persistent Chrome profile is missing; refusing automatic reload: ${PROFILE_DIR}"
  exit 1
fi

if [[ ! -f "${EXTENSION_DIR}/manifest.json" ]]; then
  log ERROR "Chrome Bridge manifest is missing; refusing automatic reload: ${EXTENSION_DIR}/manifest.json"
  exit 1
fi

install -d -o root -g root -m 755 "${STATE_DIR}"

fingerprint=""
set +e
fingerprint="$(extension_fingerprint)"
rc=$?
set -e
case "${rc}" in
  0) ;;
  2)
    log INFO "git update is in progress; deferring extension reload"
    exit 0
    ;;
  3)
    log ERROR "Chrome Bridge source has local changes; refusing automatic reload until the worktree is clean"
    exit 1
    ;;
  *)
    log ERROR "could not determine Chrome Bridge source fingerprint (exit=${rc})"
    exit "${rc}"
    ;;
esac

if [[ -z "${fingerprint}" ]]; then
  log ERROR "could not determine Chrome Bridge source fingerprint"
  exit 1
fi
version="$(extension_version)"
applied="$(cat "${APPLIED_FILE}" 2>/dev/null || true)"
failed="$(cat "${FAILED_FILE}" 2>/dev/null || true)"

if [[ -z "${applied}" ]]; then
  printf '%s\n' "${fingerprint}" >"${APPLIED_FILE}"
  rm -f "${FAILED_FILE}"
  write_state "${fingerprint}" "${version}"
  log INFO "initialized Chrome Bridge baseline: version=${version} fingerprint=${fingerprint:0:12}"
  exit 0
fi

if [[ "${fingerprint}" == "${applied}" ]]; then
  exit 0
fi

if [[ "${fingerprint}" == "${failed}" ]]; then
  log ERROR "Chrome Bridge fingerprint ${fingerprint:0:12} previously failed to reload; suppressing repeated restart until source changes"
  exit 1
fi

log INFO "Chrome Bridge source update detected: version=${version} fingerprint=${fingerprint:0:12}; restarting ${CHROME_UNIT}"
if ! systemctl restart "${CHROME_UNIT}"; then
  printf '%s\n' "${fingerprint}" >"${FAILED_FILE}"
  log ERROR "failed to restart ${CHROME_UNIT}; this fingerprint will not be retried automatically"
  exit 1
fi

if ! wait_for_chrome 30 1; then
  printf '%s\n' "${fingerprint}" >"${FAILED_FILE}"
  log ERROR "${CHROME_UNIT} did not become healthy after extension update; this fingerprint will not be retried automatically"
  exit 1
fi

printf '%s\n' "${fingerprint}" >"${APPLIED_FILE}"
rm -f "${FAILED_FILE}"
write_state "${fingerprint}" "${version}"
log INFO "Chrome Bridge update applied: version=${version} fingerprint=${fingerprint:0:12} using persistent profile ${PROFILE_DIR}"
