#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/chat2api}"
WORKER_USER="${WORKER_USER:-chat2api}"
PROFILE_DIR="${PROFILE_DIR:-/home/${WORKER_USER}/.config/chat2api-chrome-worker-01}"
EXTENSION_DIR="${EXTENSION_DIR:-${REPO_DIR}/chrome_extension}"
CHROME_UNIT="${CHROME_UNIT:-chat2api-chrome.service}"
STATE_DIR="${STATE_DIR:-/var/lib/chat2api-worker}"
SERVER_URL="${CHAT2API_SERVER_URL:-}"
if [[ -n "${CHAT2API_EXTENSION_CENTRAL_SYNC+x}" ]]; then
  CENTRAL_SYNC_ENABLED="${CHAT2API_EXTENSION_CENTRAL_SYNC}"
elif [[ "${REPO_DIR}" == "/opt/chat2api-worker" && -n "${SERVER_URL}" ]]; then
  CENTRAL_SYNC_ENABLED=1
else
  CENTRAL_SYNC_ENABLED=0
fi
APPLIED_FILE="${STATE_DIR}/extension-applied.sha256"
FAILED_FILE="${STATE_DIR}/extension-failed.sha256"
CENTRAL_BUNDLE_FILE="${STATE_DIR}/extension-central-bundle.sha256"
STATE_FILE="${STATE_DIR}/extension-state.env"
LOCK_FILE="${STATE_DIR}/extension-autoreload.lock"
CENTRAL_EXTENSION_CHANGED=0

log() {
  local level="$1"
  shift
  local message="$*"
  printf '%s [%s] %s\n' "$(date -Is)" "${level}" "${message}"
  logger -t chat2api-linux-extension-autoreload -- "[${level}] ${message}" 2>/dev/null || true
}

chrome_process_ready() {
  ps -u "${WORKER_USER}" -o args= 2>/dev/null \
    | grep -F -- "--user-data-dir=${PROFILE_DIR}" \
    | grep -E '[Cc]hrome' >/dev/null 2>&1
}

wait_for_chrome() {
  local attempts="${1:-60}"
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

chrome_main_pid() {
  ps -u "${WORKER_USER}" -o pid=,args= 2>/dev/null \
    | awk -v needle="--user-data-dir=${PROFILE_DIR}" 'index($0, needle) && !index($0, "--type=") { print $1; exit }'
}

chrome_started_after_extension_source() {
  local pid latest started_text started_epoch latest_epoch
  pid="$(chrome_main_pid)"
  [[ -n "${pid}" ]] || return 1
  latest="$(find "${EXTENSION_DIR}" -type f -printf '%T@\n' 2>/dev/null | sort -nr | head -n1 || true)"
  [[ -n "${latest}" ]] || return 1
  started_text="$(ps -o lstart= -p "${pid}" 2>/dev/null | sed 's/^ *//;s/ *$//' || true)"
  [[ -n "${started_text}" ]] || return 1
  started_epoch="$(date -d "${started_text}" +%s 2>/dev/null || true)"
  latest_epoch="${latest%%.*}"
  [[ "${started_epoch}" =~ ^[0-9]+$ && "${latest_epoch}" =~ ^[0-9]+$ ]] || return 1
  (( started_epoch >= latest_epoch ))
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

tree_fingerprint() {
  local root="$1"
  find "${root}" -type f -print0 \
    | sort -z \
    | while IFS= read -r -d '' file; do
        printf '%s\0' "${file#${root}/}"
        sha256sum "${file}"
      done \
    | sha256sum \
    | awk '{print $1}'
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

  tree_fingerprint "${extension_real}"
}

sync_central_extension() {
  [[ "${CENTRAL_SYNC_ENABLED}" == "1" ]] || return 0
  if [[ -z "${SERVER_URL}" ]]; then
    log WARN "central Bridge sync enabled but CHAT2API_SERVER_URL is empty"
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    log WARN "central Bridge sync skipped because curl/python3 is unavailable"
    return 0
  fi

  local meta bundle tmp incoming expected known incoming_fp current_fp new_dir backup_dir
  meta="$(mktemp)"
  bundle="$(mktemp)"
  tmp="$(mktemp -d)"
  cleanup_central_sync() { rm -f "${meta}" "${bundle}"; rm -rf "${tmp}"; }

  if ! curl -fsS --connect-timeout 5 --max-time 15 -o "${meta}" "${SERVER_URL%/}/bootstrap/linux-worker-bundle.json"; then
    log WARN "central Bridge manifest check failed; keeping the currently loaded local bundle"
    cleanup_central_sync
    return 0
  fi
  expected="$(python3 - "${meta}" <<'PY'
import json,sys
try:
    value=json.load(open(sys.argv[1],encoding='utf-8'))
    digest=str(value.get('sha256') or '').strip().lower()
    print(digest if len(digest)==64 and all(c in '0123456789abcdef' for c in digest) else '')
except Exception:
    print('')
PY
)"
  if [[ -z "${expected}" ]]; then
    log WARN "central Bridge manifest returned an invalid bundle digest"
    cleanup_central_sync
    return 0
  fi

  known="$(cat "${CENTRAL_BUNDLE_FILE}" 2>/dev/null || true)"
  if [[ "${known}" == "${expected}" ]]; then
    cleanup_central_sync
    return 0
  fi

  if ! curl -fSL --retry 2 --retry-delay 1 --retry-all-errors --connect-timeout 8 --max-time 120 \
      -o "${bundle}" "${SERVER_URL%/}/bootstrap/linux-worker-bundle.tar.gz"; then
    log WARN "central Worker Bundle download failed; local Bridge remains unchanged"
    cleanup_central_sync
    return 0
  fi
  if ! printf '%s  %s\n' "${expected}" "${bundle}" | sha256sum -c - >/dev/null 2>&1; then
    log WARN "central Worker Bundle digest verification failed; local Bridge remains unchanged"
    cleanup_central_sync
    return 0
  fi
  if ! tar -xzf "${bundle}" -C "${tmp}" chrome_extension >/dev/null 2>&1; then
    log WARN "central Worker Bundle does not contain a readable chrome_extension directory"
    cleanup_central_sync
    return 0
  fi

  incoming="${tmp}/chrome_extension"
  if [[ ! -f "${incoming}/manifest.json" || ! -f "${incoming}/background_entry.js" ]]; then
    log WARN "central Bridge payload failed structural validation"
    cleanup_central_sync
    return 0
  fi

  incoming_fp="$(tree_fingerprint "${incoming}")"
  current_fp="$(tree_fingerprint "${EXTENSION_DIR}")"
  if [[ -n "${incoming_fp}" && "${incoming_fp}" != "${current_fp}" ]]; then
    new_dir="${EXTENSION_DIR}.central-new"
    backup_dir="${EXTENSION_DIR}.central-previous"
    rm -rf "${new_dir}" "${backup_dir}"
    cp -a "${incoming}" "${new_dir}"
    chown -R root:root "${new_dir}"
    chmod -R a+rX "${new_dir}"
    mv "${EXTENSION_DIR}" "${backup_dir}"
    if mv "${new_dir}" "${EXTENSION_DIR}"; then
      rm -rf "${backup_dir}"
      CENTRAL_EXTENSION_CHANGED=1
      log INFO "synced a newer Chrome Bridge from the central Worker Bundle: $(extension_version)"
    else
      mv "${backup_dir}" "${EXTENSION_DIR}" || true
      rm -rf "${new_dir}"
      log ERROR "failed to atomically install the central Chrome Bridge; restored previous source"
      cleanup_central_sync
      return 1
    fi
  fi

  printf '%s\n' "${expected}" >"${CENTRAL_BUNDLE_FILE}"
  cleanup_central_sync
  return 0
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
CENTRAL_BUNDLE_SHA256=$(cat "${CENTRAL_BUNDLE_FILE}" 2>/dev/null || echo unknown)
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
if ! command -v flock >/dev/null 2>&1; then
  log ERROR "flock is required to serialize Chrome Bridge reload transactions"
  exit 1
fi
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log INFO "another Chrome Bridge reload transaction is already running; skipping this trigger"
  exit 0
fi

# Production Linux Workers infer central sync from their canonical bundle path
# and CHAT2API_SERVER_URL. Development checkouts remain local-only unless opted in.
sync_central_extension || true

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

if [[ -z "${applied}" && "${CENTRAL_EXTENSION_CHANGED}" != "1" ]]; then
  printf '%s\n' "${fingerprint}" >"${APPLIED_FILE}"
  rm -f "${FAILED_FILE}"
  write_state "${fingerprint}" "${version}"
  log INFO "initialized Chrome Bridge baseline: version=${version} fingerprint=${fingerprint:0:12}"
  exit 0
fi

if [[ "${fingerprint}" == "${applied}" ]]; then
  exit 0
fi

# The bootstrap upgrader replaces the Worker Bundle and then explicitly restarts
# Chrome from that new bundle. The old applied fingerprint is still on disk, so
# the minute timer used to restart the freshly started Chrome a second time.
# If this invocation itself downloaded a newer central Bridge, however, Chrome
# definitely predates those files and must restart even when timestamps are close.
if [[ "${CENTRAL_EXTENSION_CHANGED}" != "1" ]] && chrome_started_after_extension_source; then
  printf '%s\n' "${fingerprint}" >"${APPLIED_FILE}"
  rm -f "${FAILED_FILE}"
  write_state "${fingerprint}" "${version}"
  log INFO "Chrome already started after the current Bridge source; adopted version=${version} fingerprint=${fingerprint:0:12} without a second restart"
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

if ! wait_for_chrome 60 1; then
  printf '%s\n' "${fingerprint}" >"${FAILED_FILE}"
  log ERROR "${CHROME_UNIT} did not become healthy after extension update; this fingerprint will not be retried automatically"
  exit 1
fi

printf '%s\n' "${fingerprint}" >"${APPLIED_FILE}"
rm -f "${FAILED_FILE}"
write_state "${fingerprint}" "${version}"
log INFO "Chrome Bridge update applied: version=${version} fingerprint=${fingerprint:0:12} using persistent profile ${PROFILE_DIR}"
