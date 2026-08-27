#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/chat2api}"
DATA_DIR="${APP_DIR}/data"
UPDATER_SCRIPT="${APP_DIR}/scripts/chat2api_server_update.sh"
PATH_UNIT="/etc/systemd/system/chat2api-admin-update.path"
SERVICE_UNIT="/etc/systemd/system/chat2api-admin-update.service"
MARKER_FILE="${DATA_DIR}/admin-updater-installed.json"
DEPLOYMENT_FILE="${DATA_DIR}/deployment.json"

if [[ "${EUID}" -ne 0 ]]; then
  echo "请使用 root 执行：sudo bash ${APP_DIR}/scripts/install_chat2api_server_updater.sh" >&2
  exit 1
fi

[[ -d "${APP_DIR}/.git" ]] || { echo "未找到 Git 仓库：${APP_DIR}" >&2; exit 2; }
[[ -f "${APP_DIR}/docker-compose.yml" ]] || { echo "未找到 ${APP_DIR}/docker-compose.yml" >&2; exit 2; }
[[ -f "${UPDATER_SCRIPT}" ]] || { echo "未找到更新脚本：${UPDATER_SCRIPT}" >&2; exit 2; }
command -v systemctl >/dev/null
command -v docker >/dev/null
docker compose version >/dev/null

origin="$(git -C "${APP_DIR}" remote get-url origin 2>/dev/null || true)"
case "$origin" in
  https://github.com/b8vipvip/chat2api.git|git@github.com:b8vipvip/chat2api.git) ;;
  *) echo "origin 不是预期的 chat2api 仓库：${origin}" >&2; exit 3 ;;
esac

install -d -m 0755 "$DATA_DIR"
# The service invokes this tracked file through /bin/bash, so it does not need
# an executable bit. Keep the mode aligned with Git (100644); the old installer
# used chmod 755 here, which made the worktree look dirty and caused the updater
# itself to fail its destructive-update preflight with exit code 4.
chmod 0644 "$UPDATER_SCRIPT"
# Ubuntu + GnuTLS environments occasionally terminate GitHub HTTP/2 sessions
# early. Keep this repository on HTTP/1.1; the updater also retries every fetch.
git -C "$APP_DIR" config http.version HTTP/1.1

cat > "$SERVICE_UNIT" <<EOF
[Unit]
Description=chat2api admin-triggered GitHub update
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=${APP_DIR}
Environment=APP_DIR=${APP_DIR}
Environment=BRANCH=main
ExecStart=/bin/bash ${UPDATER_SCRIPT}
TimeoutStartSec=30min
Nice=5

[Install]
WantedBy=multi-user.target
EOF

cat > "$PATH_UNIT" <<EOF
[Unit]
Description=Watch for chat2api admin update requests

[Path]
PathExists=${DATA_DIR}/admin-update-request.json
Unit=chat2api-admin-update.service
MakeDirectory=true

[Install]
WantedBy=multi-user.target
EOF

chmod 0644 "$SERVICE_UNIT" "$PATH_UNIT"
# Never allow a stale request left by a previous partial installation to fire
# while systemd units are being replaced.
rm -f "${DATA_DIR}/admin-update-request.json"
systemctl daemon-reload
systemctl enable --now chat2api-admin-update.path

current_sha="$(git -C "$APP_DIR" rev-parse HEAD)"
python3 - "$MARKER_FILE" "$DEPLOYMENT_FILE" "$APP_DIR" "$current_sha" <<'PY'
from __future__ import annotations
import json, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

marker = Path(sys.argv[1])
deployment = Path(sys.argv[2])
app_dir = sys.argv[3]
sha = sys.argv[4]
now = datetime.now(timezone.utc).isoformat()

def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

write(marker, {
    "installed": True,
    "mode": "systemd-path",
    "app_dir": app_dir,
    "path_unit": "chat2api-admin-update.path",
    "service_unit": "chat2api-admin-update.service",
    "installed_at": now,
})
write(deployment, {
    "commit": sha,
    "branch": "main",
    "repository": "b8vipvip/chat2api",
    "updated_at": now,
})
PY

systemctl restart chat2api-admin-update.path

echo "chat2api 主机更新助手已安装。"
echo "监听单元：chat2api-admin-update.path"
echo "执行单元：chat2api-admin-update.service"
echo "部署目录：${APP_DIR}"
echo "当前提交：${current_sha}"
echo "现在可以在 chat2api 控制台 → 版本更新 中直接执行 GitHub main 自动更新。"
