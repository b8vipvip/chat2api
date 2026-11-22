#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/chat2api}"
BRANCH="${BRANCH:-main}"
EXPECTED_REPO="${EXPECTED_REPO:-https://github.com/b8vipvip/chat2api.git}"
GITHUB_HTTPS_URL="https://github.com/b8vipvip/chat2api.git"
GITHUB_SSH_URL="git@github.com:b8vipvip/chat2api.git"
DATA_DIR="${APP_DIR}/data"
REQUEST_FILE="${DATA_DIR}/admin-update-request.json"
STATUS_FILE="${DATA_DIR}/admin-update-status.json"
LOG_FILE="${DATA_DIR}/admin-update.log"
DEPLOYMENT_FILE="${DATA_DIR}/deployment.json"
LOCK_FILE="${DATA_DIR}/.admin-update.lock"
GITHUB_KNOWN_HOSTS_FILE="/run/chat2api-github-known-hosts"
CURRENT_STAGE="starting"
CURRENT_PERCENT="2"
CURRENT_MESSAGE="正在启动更新任务"
REQUEST_ID=""
USE_BUILD_CACHE="true"
SMART_UPDATE_MODE="verified-image"
SMART_HOTFIX_ATTEMPTED="false"
FROM_COMMIT=""
TARGET_COMMIT=""
ROLLBACK_COMMIT=""
ROLLBACK_SUCCEEDED="false"
CODE_SWITCHED="false"
LOCAL_COMPOSE_OVERRIDE="false"
LOCAL_COMPOSE_BACKUP_FILE="${DATA_DIR}/.admin-update-docker-compose.backup"
LOCAL_COMPOSE_MERGED_FILE="${DATA_DIR}/.admin-update-docker-compose.merged"
ENV_BACKUP=""
FETCH_TRANSPORT=""
FETCH_ELAPSED_MS=""
FETCH_ATTEMPTS="0"
COMPOSE_SERVICE="chat2api"
GITHUB_SSH_CONNECT_SECONDS="${CHAT2API_UPDATE_GITHUB_SSH_CONNECT_SECONDS:-8}"
GITHUB_SSH_FETCH_SECONDS="${CHAT2API_UPDATE_GITHUB_SSH_FETCH_SECONDS:-25}"
GITHUB_HTTPS_FETCH_SECONDS="${CHAT2API_UPDATE_GITHUB_HTTPS_FETCH_SECONDS:-35}"
GITHUB_HTTPS_LOW_SPEED_SECONDS="${CHAT2API_UPDATE_GITHUB_HTTPS_LOW_SPEED_SECONDS:-12}"
CONTAINER_STOP_SECONDS="${CHAT2API_UPDATE_CONTAINER_STOP_SECONDS:-20}"
CONTAINER_STOP_COMMAND_SECONDS="${CHAT2API_UPDATE_CONTAINER_STOP_COMMAND_SECONDS:-35}"
CONTAINER_REMOVE_COMMAND_SECONDS="${CHAT2API_UPDATE_CONTAINER_REMOVE_COMMAND_SECONDS:-25}"
COMPOSE_UP_COMMAND_SECONDS="${CHAT2API_UPDATE_COMPOSE_UP_COMMAND_SECONDS:-75}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "chat2api server updater must run as root" >&2
  exit 1
fi

python3 "$APP_DIR/scripts/chat2api_update_storage.py" ensure-dir "$DATA_DIR"
python3 "$APP_DIR/scripts/chat2api_update_storage.py" prepare-lock "$LOCK_FILE"
python3 "$APP_DIR/scripts/chat2api_update_storage.py" truncate-log "$LOG_FILE"
# The log sink validates the opened descriptor (regular file, one hard link,
# no symlink following) before appending and mirrors output to the journal.
exec > >(python3 "$APP_DIR/scripts/chat2api_update_storage.py" tee-log "$LOG_FILE") 2>&1

log() {
  printf '%s [chat2api-update] %s\n' "$(date -Is)" "$*"
}

read_request_id() {
  python3 "$APP_DIR/scripts/chat2api_update_storage.py" read-request-id "$REQUEST_FILE"
}

read_use_build_cache() {
  python3 "$APP_DIR/scripts/chat2api_update_storage.py" read-build-cache "$REQUEST_FILE"
}

write_status() {
  local status="$1" stage="$2" percent="$3" message="$4"
  CURRENT_STAGE="$stage"
  CURRENT_PERCENT="$percent"
  CURRENT_MESSAGE="$message"
  python3 - "$STATUS_FILE" "$status" "$stage" "$percent" "$message" "$REQUEST_ID" "$FROM_COMMIT" "$TARGET_COMMIT" "$ROLLBACK_COMMIT" "$ROLLBACK_SUCCEEDED" "$FETCH_TRANSPORT" "$FETCH_ELAPSED_MS" "$FETCH_ATTEMPTS" <<'PY'
from __future__ import annotations
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.environ.get("APP_DIR", "/opt/chat2api")) / "scripts"))
from chat2api_update_storage import read_json, write_json_atomic

path = Path(sys.argv[1])
status, stage, percent, message = sys.argv[2:6]
request_id, from_commit, target_commit, rollback_commit, rollback_succeeded = sys.argv[6:11]
fetch_transport, fetch_elapsed_ms, fetch_attempts = sys.argv[11:14]
now = datetime.now(timezone.utc).isoformat()
old = read_json(path)
started = str(old.get("started_at") or now)
if status == "running" and stage == "starting":
    started = now
payload = {
    "status": status,
    "stage": stage,
    "percent": max(0, min(int(percent), 100)),
    "message": message,
    "request_id": request_id or str(old.get("request_id") or ""),
    "started_at": started,
    "updated_at": now,
    "completed_at": now if status in {"succeeded", "failed"} else "",
    "from_commit": from_commit,
    "target_commit": target_commit,
    "deployed_commit": target_commit if status == "succeeded" else "",
    "rollback_commit": rollback_commit,
    "rollback_succeeded": rollback_succeeded.lower() == "true",
    "fetch_transport": fetch_transport or str(old.get("fetch_transport") or ""),
    "fetch_elapsed_ms": int(fetch_elapsed_ms) if fetch_elapsed_ms.isdigit() else int(old.get("fetch_elapsed_ms") or 0),
    "fetch_attempts": int(fetch_attempts) if fetch_attempts.isdigit() else int(old.get("fetch_attempts") or 0),
}
write_json_atomic(path, payload)
PY
  log "status=${status} stage=${stage} percent=${percent} message=${message}"
}

restore_env() {
  if [[ -n "$ENV_BACKUP" && -f "$ENV_BACKUP" ]]; then
    cp -a "$ENV_BACKUP" "$APP_DIR/.env"
  fi
}

health_port() {
  local port=""
  if [[ -f "$APP_DIR/.env" ]]; then
    port="$(grep -E '^CHAT2API_PORT=' "$APP_DIR/.env" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r' | tr -d '"' | tr -d "'" || true)"
  fi
  if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
    port="8765"
  fi
  printf '%s' "$port"
}

wait_health() {
  local port="$1"
  local attempts="${2:-60}"
  for ((i=1; i<=attempts; i++)); do
    if curl -fsS --connect-timeout 2 --max-time 5 "http://127.0.0.1:${port}/version" >/tmp/chat2api-update-version.json 2>/dev/null; then
      python3 -m json.tool /tmp/chat2api-update-version.json || true
      rm -f /tmp/chat2api-update-version.json
      return 0
    fi
    sleep 1
  done
  rm -f /tmp/chat2api-update-version.json
  return 1
}

prepare_github_known_hosts() {
  # Pin GitHub's published Ed25519 host key instead of accepting an unknown
  # host interactively. The same published fingerprint is used by GitHub's
  # ssh.github.com endpoint on port 443.
  cat >"$GITHUB_KNOWN_HOSTS_FILE" <<'EOF'
github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
ssh.github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
EOF
  chmod 600 "$GITHUB_KNOWN_HOSTS_FILE"
}

fetch_refspec() {
  printf '+refs/heads/%s:refs/remotes/origin/%s' "$BRANCH" "$BRANCH"
}

run_fetch_transport() {
  local transport="$1"
  local start_ms end_ms elapsed_ms rc=0
  local refspec
  refspec="$(fetch_refspec)"
  FETCH_ATTEMPTS="$((FETCH_ATTEMPTS + 1))"
  start_ms="$(date +%s%3N)"
  log "git fetch attempt ${FETCH_ATTEMPTS} transport=${transport}"

  case "$transport" in
    ssh-443)
      GIT_TERMINAL_PROMPT=0 \
      GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=${GITHUB_SSH_CONNECT_SECONDS} -o ConnectionAttempts=1 -o ServerAliveInterval=5 -o ServerAliveCountMax=1 -o HostName=ssh.github.com -o Port=443 -o HostKeyAlias=ssh.github.com -o UserKnownHostsFile=${GITHUB_KNOWN_HOSTS_FILE} -o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=yes -o HostKeyAlgorithms=ssh-ed25519" \
      timeout --signal=TERM --kill-after=5s "${GITHUB_SSH_FETCH_SECONDS}s" \
        git -C "$APP_DIR" fetch --prune "$GITHUB_SSH_URL" "$refspec" || rc=$?
      ;;
    ssh-22)
      GIT_TERMINAL_PROMPT=0 \
      GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=${GITHUB_SSH_CONNECT_SECONDS} -o ConnectionAttempts=1 -o ServerAliveInterval=5 -o ServerAliveCountMax=1 -o HostKeyAlias=github.com -o UserKnownHostsFile=${GITHUB_KNOWN_HOSTS_FILE} -o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=yes -o HostKeyAlgorithms=ssh-ed25519" \
      timeout --signal=TERM --kill-after=5s "${GITHUB_SSH_FETCH_SECONDS}s" \
        git -C "$APP_DIR" fetch --prune "$GITHUB_SSH_URL" "$refspec" || rc=$?
      ;;
    https)
      GIT_TERMINAL_PROMPT=0 \
      timeout --signal=TERM --kill-after=5s "${GITHUB_HTTPS_FETCH_SECONDS}s" \
        git -c http.version=HTTP/1.1 \
            -c http.lowSpeedLimit=1024 \
            -c "http.lowSpeedTime=${GITHUB_HTTPS_LOW_SPEED_SECONDS}" \
            -C "$APP_DIR" fetch --prune "$GITHUB_HTTPS_URL" "$refspec" || rc=$?
      ;;
    *)
      log "unknown GitHub fetch transport: ${transport}"
      return 2
      ;;
  esac

  end_ms="$(date +%s%3N)"
  elapsed_ms="$((end_ms - start_ms))"
  if (( rc == 0 )); then
    FETCH_TRANSPORT="$transport"
    FETCH_ELAPSED_MS="$elapsed_ms"
    log "git fetch success transport=${transport} elapsed_ms=${elapsed_ms} attempt=${FETCH_ATTEMPTS}"
    return 0
  fi
  log "git fetch failed transport=${transport} elapsed_ms=${elapsed_ms} rc=${rc}; trying next transport"
  return "$rc"
}

fetch_main() {
  local cycle
  prepare_github_known_hosts
  for cycle in 1 2; do
    log "GitHub fetch transport cycle ${cycle}/2: SSH-443 -> SSH-22 -> HTTPS"
    if command -v ssh >/dev/null 2>&1; then
      if run_fetch_transport "ssh-443"; then return 0; fi
      if run_fetch_transport "ssh-22"; then return 0; fi
    else
      log "ssh client unavailable; skipping SSH transports"
    fi
    if run_fetch_transport "https"; then return 0; fi
    if (( cycle < 2 )); then sleep $((cycle * 2)); fi
  done
  return 1
}

normalize_legacy_updater_mode() {
  # v0.22.23's updater installer chmodded this tracked 100644 script to 0755.
  # That mode-only change made the updater reject its own worktree. Normalize
  # only when the file bytes are still exactly the committed bytes; genuine
  # local content edits remain protected by the dirty-worktree preflight.
  local rel="scripts/chat2api_server_update.sh"
  local current="${APP_DIR}/${rel}"
  [[ -f "$current" ]] || return 0
  if git -C "$APP_DIR" diff --quiet --ignore-submodules=all -- "$rel"; then
    return 0
  fi
  local expected
  expected="$(mktemp /tmp/chat2api-updater-head.XXXXXX)"
  if git -C "$APP_DIR" show "HEAD:${rel}" >"$expected" 2>/dev/null && cmp -s "$current" "$expected"; then
    chmod 0644 "$current"
    log "normalized legacy mode-only change on ${rel} back to 0644"
  fi
  rm -f "$expected"
}

build_new_image() {
  cd "$APP_DIR"
  docker compose config -q
  if [[ "$USE_BUILD_CACHE" == "false" ]]; then
    log "building Docker image without cache by administrator request"
    DOCKER_BUILDKIT=1 docker compose build --no-cache --build-arg "CHAT2API_BUILD_COMMIT=$TARGET_COMMIT"
  else
    log "building Docker image with existing BuildKit/layer cache"
    DOCKER_BUILDKIT=1 docker compose build --build-arg "CHAT2API_BUILD_COMMIT=$TARGET_COMMIT"
  fi
}

service_container_ids() {
  # Query Docker directly instead of asking Compose to inspect/reconcile the
  # project. This remains usable even when a Compose recreate operation itself
  # is the component that is wedged.
  timeout --signal=TERM --kill-after=5s 15s \
    docker ps -aq \
      --filter "label=com.docker.compose.project.working_dir=${APP_DIR}" \
      --filter "label=com.docker.compose.service=${COMPOSE_SERVICE}" 2>/dev/null || true
}

replace_chat2api_container() {
  local phase="${1:-deploy}"
  local ids=""
  cd "$APP_DIR"

  ids="$(service_container_ids)"
  if [[ -n "$ids" ]]; then
    log "${phase}: stopping existing ${COMPOSE_SERVICE} container(s): $(tr '\n' ' ' <<<"$ids")"
    if ! timeout --signal=TERM --kill-after=5s "${CONTAINER_STOP_COMMAND_SECONDS}s" \
      docker stop --timeout "$CONTAINER_STOP_SECONDS" $ids; then
      log "${phase}: graceful stop timed out/failed; forcing old ${COMPOSE_SERVICE} container(s) down"
      timeout --signal=KILL 15s docker kill $ids >/dev/null 2>&1 || true
    fi

    # Never pass -v: /app/data is a host bind mount and update recovery must not
    # remove any persistent data. Removing only the old service container also
    # bypasses Compose v5 recreate-state hangs seen on some hosts.
    if ! timeout --signal=TERM --kill-after=5s "${CONTAINER_REMOVE_COMMAND_SECONDS}s" \
      docker rm -f $ids; then
      log "${phase}: failed to remove old ${COMPOSE_SERVICE} container(s) within bounded timeout"
      return 1
    fi
  fi

  log "${phase}: creating ${COMPOSE_SERVICE} from the already-built image"
  if timeout --signal=TERM --kill-after=10s "${COMPOSE_UP_COMMAND_SECONDS}s" \
    docker compose up -d --no-deps --no-build "$COMPOSE_SERVICE"; then
    return 0
  fi

  # A Compose CLI can time out after the engine has already started the new
  # container. Accept that case only if the actual HTTP service is healthy.
  log "${phase}: docker compose up did not finish in time; checking service health before failing"
  if wait_health "$(health_port)" 12; then
    log "${phase}: service is healthy despite Compose CLI timeout; continuing"
    return 0
  fi
  return 1
}

rollback() {
  [[ "$CODE_SWITCHED" == "true" ]] || return 0
  if [[ "$SMART_HOTFIX_ATTEMPTED" == "true" ]]; then
    # The shared hotfix helper already owns file restoration. Never rebuild
    # a different image as a second rollback controller.
    git -C "$APP_DIR" reset --hard "$FROM_COMMIT" || true
    restore_env
    restore_local_compose_backup || true
    log "hotfix helper attempted container rollback; inspect its evidence before retry"
    return 0
  fi
  [[ -n "$FROM_COMMIT" ]] || return 0
  ROLLBACK_COMMIT="$FROM_COMMIT"
  write_status "running" "rollback" "92" "新版本未通过验证，正在自动回滚到 ${FROM_COMMIT:0:12}"
  log "rolling back source to $FROM_COMMIT"
  set +e
  git -C "$APP_DIR" reset --hard "$FROM_COMMIT"
  local git_rc=$?
  restore_env
  if (( git_rc == 0 )); then
    restore_local_compose_backup || true
    # Recovery prioritizes speed/availability; always reuse safe local cache.
    (cd "$APP_DIR" && DOCKER_BUILDKIT=1 docker compose build --build-arg "CHAT2API_BUILD_COMMIT=$FROM_COMMIT")
    local build_rc=$?
    if (( build_rc == 0 )); then
      replace_chat2api_container "rollback"
      local up_rc=$?
      if (( up_rc == 0 )) && wait_health "$(health_port)" 45; then
        ROLLBACK_SUCCEEDED="true"
        log "automatic rollback succeeded"
      fi
    fi
  fi
  set -e
}

restore_local_compose_backup() {
  if [[ -f "$LOCAL_COMPOSE_BACKUP_FILE" ]]; then
    cp -a "$LOCAL_COMPOSE_BACKUP_FILE" "$APP_DIR/docker-compose.yml"
    log "restored preserved local docker-compose.yml"
  fi
}

prepare_local_compose_override() {
  local paths=()
  mapfile -t paths < <({
    git -C "$APP_DIR" diff --name-only
    git -C "$APP_DIR" diff --cached --name-only
  } | sort -u)
  local path=""
  for path in "${paths[@]}"; do
    [[ -z "$path" ]] && continue
    if [[ "$path" != "docker-compose.yml" ]]; then
      return 1
    fi
  done
  [[ -f "$APP_DIR/docker-compose.yml" ]] || return 1
  cp -a "$APP_DIR/docker-compose.yml" "$LOCAL_COMPOSE_BACKUP_FILE"
  LOCAL_COMPOSE_OVERRIDE="true"
  log "detected local docker-compose.yml override; it will be three-way merged into the target release"
  return 0
}

merge_local_compose_override() {
  [[ "$LOCAL_COMPOSE_OVERRIDE" == "true" ]] || return 0
  local base_file="${DATA_DIR}/.admin-update-docker-compose.base"
  local remote_file="${DATA_DIR}/.admin-update-docker-compose.remote"
  git -C "$APP_DIR" show "${FROM_COMMIT}:docker-compose.yml" >"$base_file"
  git -C "$APP_DIR" show "${TARGET_COMMIT}:docker-compose.yml" >"$remote_file"
  local merge_rc=0
  set +e
  git -C "$APP_DIR" merge-file -p -q \
    -L "local docker-compose.yml" \
    -L "base ${FROM_COMMIT:0:12}" \
    -L "remote ${TARGET_COMMIT:0:12}" \
    "$LOCAL_COMPOSE_BACKUP_FILE" "$base_file" "$remote_file" \
    >"$LOCAL_COMPOSE_MERGED_FILE"
  merge_rc=$?
  set -e
  rm -f "$base_file" "$remote_file"
  if (( merge_rc != 0 )); then
    log "local docker-compose.yml cannot be merged cleanly with target ${TARGET_COMMIT:0:12}; update remains non-destructive"
    return 1
  fi
  return 0
}

cleanup() {
  [[ -n "$ENV_BACKUP" ]] && rm -f "$ENV_BACKUP" || true
  rm -f "$REQUEST_FILE" || true
  rm -f "$GITHUB_KNOWN_HOSTS_FILE" || true
  rm -f "$LOCAL_COMPOSE_MERGED_FILE" "${DATA_DIR}/.admin-update-docker-compose.base" "${DATA_DIR}/.admin-update-docker-compose.remote" || true
  rm -f "$LOCAL_COMPOSE_BACKUP_FILE" || true
}

on_exit() {
  local rc=$?
  trap - EXIT
  if (( rc != 0 )); then
    rollback || true
    write_status "failed" "$CURRENT_STAGE" "$CURRENT_PERCENT" "${CURRENT_MESSAGE}失败（退出码 ${rc}）"
    if [[ "$ROLLBACK_SUCCEEDED" == "true" ]]; then
      write_status "failed" "rollback" "100" "更新失败，但已自动回滚并恢复旧版本服务"
    fi
  fi
  cleanup
  exit "$rc"
}
trap on_exit EXIT

exec 9<>"$LOCK_FILE"
if ! flock -n 9; then
  REQUEST_ID="$(read_request_id)"
  write_status "failed" "starting" "1" "已有更新任务持有主机更新锁"
  exit 75
fi

REQUEST_ID="$(read_request_id)"
USE_BUILD_CACHE="$(read_use_build_cache)"
rm -f "$REQUEST_FILE"
write_status "running" "starting" "2" "主机更新助手已接管任务"

write_status "running" "preflight" "8" "正在检查 Git、Docker Compose 与工作区"
[[ -d "$APP_DIR/.git" ]] || { log "missing git repository at $APP_DIR"; exit 2; }
command -v git >/dev/null
command -v docker >/dev/null
command -v curl >/dev/null
command -v timeout >/dev/null
docker compose version >/dev/null

remote_url="$(git -C "$APP_DIR" remote get-url origin 2>/dev/null || true)"
case "$remote_url" in
  "$EXPECTED_REPO"|"$GITHUB_HTTPS_URL"|"$GITHUB_SSH_URL"|"ssh://git@ssh.github.com:443/b8vipvip/chat2api.git") ;;
  *) log "unexpected origin remote: $remote_url"; exit 3 ;;
esac

FROM_COMMIT="$(git -C "$APP_DIR" rev-parse HEAD)"
normalize_legacy_updater_mode
tracked_dirty="$(git -C "$APP_DIR" status --porcelain --untracked-files=no)"
if [[ -n "$tracked_dirty" ]]; then
  if ! prepare_local_compose_override; then
    log "tracked working tree is dirty outside the supported docker-compose.yml local override; refusing destructive update"
    printf '%s\n' "$tracked_dirty"
    exit 4
  fi
fi
write_status "running" "backup" "14" "正在备份 .env 并记录当前提交 ${FROM_COMMIT:0:12}"
if [[ -f "$APP_DIR/.env" ]]; then
  ENV_BACKUP="$(mktemp /tmp/chat2api-env.XXXXXX)"
  cp -a "$APP_DIR/.env" "$ENV_BACKUP"
fi

# Keep HTTPS fallback on HTTP/1.1 because some Ubuntu/GnuTLS paths
# intermittently terminate GitHub HTTP/2 sessions early.
git -C "$APP_DIR" config http.version HTTP/1.1

write_status "running" "fetch" "24" "正在从 GitHub 拉取 main：SSH-443 → SSH-22 → HTTPS 自动容灾"
if ! fetch_main; then
  log "git fetch failed on all bounded transports"
  exit 5
fi
TARGET_COMMIT="$(git -C "$APP_DIR" rev-parse "origin/${BRANCH}")"
write_status "running" "fetch" "32" "GitHub main=${TARGET_COMMIT:0:12}；通道=${FETCH_TRANSPORT}；耗时=${FETCH_ELAPSED_MS}ms；尝试=${FETCH_ATTEMPTS}"
if [[ "$LOCAL_COMPOSE_OVERRIDE" == "true" ]]; then
  write_status "running" "fetch" "34" "检测到本地 docker-compose.yml 修改，正在与目标提交做安全三方合并"
  if ! merge_local_compose_override; then
    exit 4
  fi
fi

# Git checkout != Docker runtime. Verify the actual running source SHA first.
RUNTIME_SHA=""
RUNTIME_CONTAINER="$(docker compose -f "$APP_DIR/docker-compose.yml" ps -q chat2api || true)"
if [[ -n "$RUNTIME_CONTAINER" ]]; then
  RUNTIME_SHA="$(docker exec "$RUNTIME_CONTAINER" cat /app/.gptauto-runtime-sha 2>/dev/null || true)"
  if [[ ! "$RUNTIME_SHA" =~ ^[a-f0-9]{40}$ ]]; then
    RUNTIME_SHA="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$RUNTIME_CONTAINER" 2>/dev/null |
      sed -n 's/^CHAT2API_BUILD_COMMIT=//p' | head -1 || true)"
  fi
fi
if [[ "$TARGET_COMMIT" == "$FROM_COMMIT" && "$RUNTIME_SHA" != "$TARGET_COMMIT" ]]; then
  log "Git already current but running container is unverified/old: ${RUNTIME_SHA:-unknown}; rebuilding"
fi
if [[ "$TARGET_COMMIT" == "$FROM_COMMIT" && "$RUNTIME_SHA" == "$TARGET_COMMIT" ]]; then
  write_status "succeeded" "completed" "100" "当前已经是 GitHub main 最新提交 ${TARGET_COMMIT:0:12}"
  python3 - "$DEPLOYMENT_FILE" "$TARGET_COMMIT" <<'PY'
import os, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(os.environ.get("APP_DIR", "/opt/chat2api")) / "scripts"))
from chat2api_update_storage import write_json_atomic
path = Path(sys.argv[1]); sha = sys.argv[2]
payload = {"commit": sha, "branch": "main", "repository": "b8vipvip/chat2api", "updated_at": datetime.now(timezone.utc).isoformat()}
write_json_atomic(path, payload)
PY
  trap - EXIT
  cleanup
  exit 0
fi

write_status "running" "checkout" "40" "正在切换到 GitHub main ${TARGET_COMMIT:0:12}"
git -C "$APP_DIR" checkout "$BRANCH"
git -C "$APP_DIR" reset --hard "$TARGET_COMMIT"
CODE_SWITCHED="true"
if [[ "$LOCAL_COMPOSE_OVERRIDE" == "true" ]]; then
  cp -a "$LOCAL_COMPOSE_MERGED_FILE" "$APP_DIR/docker-compose.yml"
  log "reapplied merged local docker-compose.yml after source checkout"
fi
restore_env

# GPTAuto is the only change classifier. The existing host updater retains
# exclusive Docker deployment ownership and its systemd flock.
SMART_PLAN_TOOL="$APP_DIR/.github/gptauto/smart_update.py"
SMART_APPLY_TOOL="$APP_DIR/.github/gptauto/runtime_hotfix.py"
if [[ -f "$SMART_PLAN_TOOL" ]]; then
  if smart_plan="$(python3 "$SMART_PLAN_TOOL" --repo chat2api       --worktree "$APP_DIR" --from-sha "$FROM_COMMIT" --target-sha "$TARGET_COMMIT")"; then
    SMART_UPDATE_MODE="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["mode"])' <<<"$smart_plan")"
    log "GPTAuto exact-SHA smart update: $SMART_UPDATE_MODE; $smart_plan"
  else
    log "GPTAuto smart plan invalid; full image verification is required"
    SMART_UPDATE_MODE="verified-image"
  fi
fi

if [[ "$SMART_UPDATE_MODE" == "checks-only" ]]; then
  write_status "running" "verify" "85" "智能判断：只修改文档/测试，跳过 Docker 构建和容器重启"
  python3 - "$DEPLOYMENT_FILE" "$TARGET_COMMIT" "$FROM_COMMIT" <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ.get("APP_DIR", "/opt/chat2api")) / "scripts"))
from chat2api_update_storage import write_json_atomic
p=Path(sys.argv[1]); record={"commit":sys.argv[2],"previous_commit":sys.argv[3],
"repository":"b8vipvip/chat2api","update_mode":"checks-only"}
write_json_atomic(p, record)
PY
  write_status "succeeded" "completed" "100" "GPTAuto 智能更新：无运行时代码变更，跳过构建"
  trap - EXIT
  cleanup
  exit 0
fi

# A live container can be hotpatched only when its *effective* source revision
# is proven. Unknown legacy images upgrade automatically via the existing
# verified Docker build, rather than failing an ordinary admin update.
if [[ "$SMART_UPDATE_MODE" == "runtime-hotfix" && -f "$SMART_APPLY_TOOL" ]]; then
  baseline_cid="$(docker compose -f "$APP_DIR/docker-compose.yml" ps -q chat2api || true)"
  if [[ -n "$baseline_cid" ]]; then
    baseline_json="$(python3 "$SMART_APPLY_TOOL" --baseline-only \
      --repo chat2api --worktree "$APP_DIR" \
      --from-sha "$FROM_COMMIT" --target-sha "$TARGET_COMMIT" \
      --container "$baseline_cid" \
      --state-file "$DATA_DIR/smart-update-runtime.json" \
      --health-url "http://127.0.0.1:$(health_port)/version")"
    SMART_UPDATE_MODE="$(python3 -c 'import json,sys;print(json.load(sys.stdin)["mode"])' <<<"$baseline_json")"
    log "GPTAuto running-container baseline selection: $SMART_UPDATE_MODE"
  else
    SMART_UPDATE_MODE="verified-image"
    log "No existing container to patch; use verified Docker image build"
  fi
fi

if [[ "$SMART_UPDATE_MODE" == "runtime-hotfix" && -f "$SMART_APPLY_TOOL" ]]; then
  SMART_HOTFIX_ATTEMPTED="true"
  port="$(health_port)"
  write_status "running" "hotfix" "60" "GPTAuto 智能热更新：校验精确 CI、运行镜像和文件 SHA256"
  cid="$(docker compose -f "$APP_DIR/docker-compose.yml" ps -q chat2api)"
  [[ -n "$cid" ]] || { log "No running chat2api service for hotfix"; exit 9; }
  if ! python3 "$SMART_APPLY_TOOL"       --repo chat2api --worktree "$APP_DIR"       --from-sha "$FROM_COMMIT" --target-sha "$TARGET_COMMIT"       --container "$cid"       --state-file "$DATA_DIR/smart-update-runtime.json"       --health-url "http://127.0.0.1:${port}/version"; then
    log "GPTAuto guarded runtime update failed; no unverified image will be deployed"
    exit 9
  fi
  python3 - "$DEPLOYMENT_FILE" "$TARGET_COMMIT" "$FROM_COMMIT" <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ.get("APP_DIR", "/opt/chat2api")) / "scripts"))
from chat2api_update_storage import write_json_atomic
p=Path(sys.argv[1]); record={"commit":sys.argv[2],"previous_commit":sys.argv[3],
"repository":"b8vipvip/chat2api","update_mode":"runtime-hotfix"}
write_json_atomic(p, record)
PY
  write_status "succeeded" "completed" "100" "GPTAuto 智能热修复成功：精确 CI、文件校验和健康检查通过"
  trap - EXIT
  cleanup
  exit 0
fi

write_status "running" "strategy" "50" "智能判断：需要经验证的 Docker 镜像，按原正式构建路径执行"
if [[ "$USE_BUILD_CACHE" == "false" ]]; then
  write_status "running" "build" "55" "正在校验 Compose 并无缓存构建新 Docker 镜像"
else
  write_status "running" "build" "55" "正在校验 Compose 并使用现有 Docker 缓存构建新镜像"
fi
build_new_image

write_status "running" "deploy" "76" "新镜像构建成功，正在有界停止旧容器并启动新 chat2api 容器"
if ! replace_chat2api_container "deploy"; then
  CURRENT_STAGE="deploy"
  CURRENT_PERCENT="76"
  CURRENT_MESSAGE="新版本容器切换"
  log "bounded container replacement failed"
  exit 7
fi

port="$(health_port)"
write_status "running" "health" "88" "正在等待新服务健康检查 127.0.0.1:${port}/version"
if ! wait_health "$port" 60; then
  log "new deployment health check failed"
  docker compose -f "$APP_DIR/docker-compose.yml" ps || true
  docker compose -f "$APP_DIR/docker-compose.yml" logs --tail=160 chat2api || true
  CURRENT_STAGE="health"
  CURRENT_PERCENT="88"
  CURRENT_MESSAGE="新版本健康检查"
  exit 6
fi

python3 - "$DEPLOYMENT_FILE" "$TARGET_COMMIT" "$FROM_COMMIT" <<'PY'
import os, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(os.environ.get("APP_DIR", "/opt/chat2api")) / "scripts"))
from chat2api_update_storage import write_json_atomic
path = Path(sys.argv[1]); sha = sys.argv[2]; previous = sys.argv[3]
payload = {
    "commit": sha,
    "previous_commit": previous,
    "branch": "main",
    "repository": "b8vipvip/chat2api",
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
write_json_atomic(path, payload)
PY

write_status "succeeded" "completed" "100" "服务端更新完成：${FROM_COMMIT:0:12} → ${TARGET_COMMIT:0:12}"
trap - EXIT
cleanup
log "update completed successfully"
