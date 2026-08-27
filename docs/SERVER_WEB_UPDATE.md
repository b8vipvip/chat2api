# 服务端网页自动更新

chat2api 的服务端运行在 Docker 容器内。控制台的“版本更新”功能采用宿主机 `systemd.path` 监听持久化 `data/` 目录中的固定请求文件，而不是把 `/var/run/docker.sock` 或宿主机 root 权限直接挂进 Web 容器。

## 一次性启用

服务器已经更新到包含本功能的版本后，在宿主机执行一次：

```bash
cd /opt/chat2api
sudo bash scripts/install_chat2api_server_updater.sh
```

安装器会：

- 校验 `/opt/chat2api`、Git origin 和 Docker Compose；
- 为当前仓库固定 `git http.version=HTTP/1.1`，降低 Ubuntu/GnuTLS 与 GitHub 连接被提前终止的概率；
- 安装并启用 `chat2api-admin-update.path`；
- 安装 `chat2api-admin-update.service`；
- 将当前 Git 提交写入 `data/deployment.json`；
- 将更新助手状态写入 `data/admin-updater-installed.json`。

完成后，进入 chat2api 控制台 → **版本更新** 即可检查 GitHub main 并执行更新。

## 更新流程

网页只会写入 `data/admin-update-request.json`。宿主机 `systemd.path` 检测到文件后启动固定脚本 `scripts/chat2api_server_update.sh`。

脚本固定执行以下步骤：

1. 获取主机更新锁，防止并发更新；
2. 校验 origin、Git、Docker Compose；
3. 如果 tracked 工作区有未提交修改则拒绝更新；
4. 备份 `.env`；
5. 使用 HTTP/1.1 + 重试拉取 `origin/main`；
6. 切换到远端 main；
7. 先执行 `docker compose build`，构建成功后才切换容器；
8. 执行 `docker compose up -d --remove-orphans`；
9. 轮询本机 `/version` 健康检查；
10. 成功后写入 `data/deployment.json`；
11. 如果切换后健康检查失败，自动 reset 回更新前提交、重新构建并恢复旧容器。

更新状态保存到 `data/admin-update-status.json`，日志保存到 `data/admin-update.log`，所以服务重启期间页面重新连接后仍能恢复进度。

## 手工查看状态

```bash
systemctl status chat2api-admin-update.path --no-pager
systemctl status chat2api-admin-update.service --no-pager
journalctl -u chat2api-admin-update.service -n 200 --no-pager
cat /opt/chat2api/data/admin-update-status.json
```

## 禁用网页更新

```bash
sudo systemctl disable --now chat2api-admin-update.path
```

重新启用：

```bash
sudo systemctl enable --now chat2api-admin-update.path
```
