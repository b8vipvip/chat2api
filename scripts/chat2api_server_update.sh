#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/chat2api}"
BRANCH="${BRANCH:-main}"
EXPECTED_REPO="${EXPECTED_REPO:-https://github.com/b8vipvip/chat2api.git}"
DATA_DIR="${APP_DIR}/data"
REQUEST_FILE="${DATA_DIR}/admin-update-request.json"
STATUS_FILE="${DATA_DIR}/admin-update-status.json"
LOG_FILE="${DATA_DIR}/admin-update.log"
DEPLOYMENT_FILE="${DATA_DIR}/deployment.json"
LOCK_FILE="/run/lock/chat2api-admin-update.lock"
CURRENT_STAGE="starting"
CURRENT_PERCENT="2"
CURRENT_MESSAGE="正在启动更新任务"
REQUEST_ID=""
USE_BUILD_CACHE="true"
FROM_COMMIT=""
TARGET_COMMIT=""
ROLLBACK_COMMIT=""
ROLLBACK_SUCCEEDED="false"
CODE_SWITCHED="false"
ENV_BACKUP=""
COMPOSE_SERVICE="chat2api"
CONTAINER_STOP_SECONDS="${CHAT2API_UPDATE_CONTAINER_STOP_SECONDS:-20}"
CONTAINER_STOP_COMMAND_SECONDS="${CHAT2API_UPDATE_CONTAINER_STOP_COMMAND_SECONDS:-35}"
CONTAINER_REMOVE_COMMAND_SECONDS="${CHAT2API_UPDATE_CONTAINER_REMOVE_COMMAND_SECONDS:-25}"
COMPOSE_UP_COMMAND_SECONDS="${CHAT2API_UPDATE_COMPOSE_UP_COMMAND_SECONDS:-75}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "chat2api server updater must run as root" >&2
  exit 1
fi

mkdir -p "$DATA_DIR" /run/lock
chmod 755 "$DATA_DIR"
: > "$LOG_FILE"
chmod 640 "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

log() {
  printf '%s [chat2api-update] %s\n' "$(date -Is)" "$*"
}

read_request_id() {
  python3 - "$REQUEST_FILE" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    payload = {}
print(str(payload.get("request_id") or ""))
PY
}

read_use_build_cache() {
  python3 - "$REQUEST_FILE" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    payload = {}
print("false" if payload.get("use_build_cache") is False else "true")
PY
}

write_status() {
  local status="$1" stage="$2" percent="$3" message="$4"
  CURRENT_STAGE="$stage"
  CURRENT_PERCENT="$percent"
  CURRENT_MESSAGE="$message"
  python3 - "$STATUS_FILE" "$status" "$stage" "$percent" "$message" "$REQUEST_ID" "$FROM_COMMIT" "$TARGET_COMMIT" "$ROLLBACK_COMMIT" "$ROLLBACK_SUCCEEDED" <<'PY'
from __future__ import annotations
import json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
status, stage, percent, message = sys.argv[2:6]
request_id, from_commit, target_commit, rollback_commit, rollback_succeeded = sys.argv[6:11]
now = datetime.now(timezone.utc).isoformat()
try:
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    old = {}
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
}
path.parent.mkdir(parents=True, exist_ok=True)
fd, tmp = tempfile.mkstemp(prefix=".admin-update-status.", dir=str(path.parent), text=True)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp, path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
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

fetch_main() {
  local attempt
  for attempt in 1 2 3 4 5 6; do
    log "git fetch attempt ${attempt}/6 (HTTP/1.1)"
    if GIT_TERMINAL_PROMPT=0 git -c http.version=HTTP/1.1 -c http.lowSpeedLimit=0 -C "$APP_DIR" fetch --prune origin "$BRANCH"; then
      return 0
    fi
    sleep $((attempt * 2))
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
    DOCKER_BUILDKIT=1 docker compose build --no-cache
  else
    log "building Docker image with existing BuildKit/layer cache"
    DOCKER_BUILDKIT=1 docker compose build
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
      docker stop --time "$CONTAINER_STOP_SECONDS" $ids; then
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
  [[ -n "$FROM_COMMIT" ]] || return 0
  ROLLBACK_COMMIT="$FROM_COMMIT"
  write_status "running" "rollback" "92" "新版本未通过验证，正在自动回滚到 ${FROM_COMMIT:0:12}"
  log "rolling back source to $FROM_COMMIT"
  set +e
  git -C "$APP_DIR" reset --hard "$FROM_COMMIT"
  local git_rc=$?
  restore_env
  if (( git_rc == 0 )); then
    # Recovery prioritizes speed/availability; always reuse safe local cache.
    (cd "$APP_DIR" && DOCKER_BUILDKIT=1 docker compose build)
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

cleanup() {
  [[ -n "$ENV_BACKUP" ]] && rm -f "$ENV_BACKUP" || true
  rm -f "$REQUEST_FILE" || true
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

exec 9>"$LOCK_FILE"
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
if [[ "$remote_url" != "$EXPECTED_REPO" && "$remote_url" != "git@github.com:b8vipvip/chat2api.git" ]]; then
  log "unexpected origin remote: $remote_url"
  exit 3
fi

normalize_legacy_updater_mode
tracked_dirty="$(git -C "$APP_DIR" status --porcelain --untracked-files=no)"
if [[ -n "$tracked_dirty" ]]; then
  log "tracked working tree is dirty; refusing destructive update"
  printf '%s\n' "$tracked_dirty"
  exit 4
fi

FROM_COMMIT="$(git -C "$APP_DIR" rev-parse HEAD)"
write_status "running" "backup" "14" "正在备份 .env 并记录当前提交 ${FROM_COMMIT:0:12}"
if [[ -f "$APP_DIR/.env" ]]; then
  ENV_BACKUP="$(mktemp /tmp/chat2api-env.XXXXXX)"
  cp -a "$APP_DIR/.env" "$ENV_BACKUP"
fi

# Persist HTTP/1.1 locally because some Ubuntu/GnuTLS paths intermittently fail
# with 'The TLS connection was non-properly terminated' under HTTP/2.
git -C "$APP_DIR" config http.version HTTP/1.1

write_status "running" "fetch" "24" "正在从 GitHub 拉取 origin/main（带重试）"
if ! fetch_main; then
  log "git fetch failed after retries"
  exit 5
fi
TARGET_COMMIT="$(git -C "$APP_DIR" rev-parse "origin/${BRANCH}")"
write_status "running" "fetch" "32" "GitHub main=${TARGET_COMMIT:0:12}"

if [[ "$TARGET_COMMIT" == "$FROM_COMMIT" ]]; then
  write_status "succeeded" "completed" "100" "当前已经是 GitHub main 最新提交 ${TARGET_COMMIT:0:12}"
  python3 - "$DEPLOYMENT_FILE" "$TARGET_COMMIT" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
path = Path(sys.argv[1]); sha = sys.argv[2]
payload = {"commit": sha, "branch": "main", "repository": "b8vipvip/chat2api", "updated_at": datetime.now(timezone.utc).isoformat()}
fd, tmp = tempfile.mkstemp(prefix=".deployment.", dir=str(path.parent), text=True)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
os.replace(tmp, path)
PY
  trap - EXIT
  cleanup
  exit 0
fi

write_status "running" "checkout" "40" "正在切换到 GitHub main ${TARGET_COMMIT:0:12}"
git -C "$APP_DIR" checkout "$BRANCH"
git -C "$APP_DIR" reset --hard "$TARGET_COMMIT"
CODE_SWITCHED="true"
restore_env

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
import json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
path = Path(sys.argv[1]); sha = sys.argv[2]; previous = sys.argv[3]
payload = {
    "commit": sha,
    "previous_commit": previous,
    "branch": "main",
    "repository": "b8vipvip/chat2api",
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
fd, tmp = tempfile.mkstemp(prefix=".deployment.", dir=str(path.parent), text=True)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
os.replace(tmp, path)
PY

write_status "succeeded" "completed" "100" "服务端更新完成：${FROM_COMMIT:0:12} → ${TARGET_COMMIT:0:12}"
trap - EXIT
cleanup
log "update completed successfully"
