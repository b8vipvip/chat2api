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
- 保留 HTTPS fallback 的 `git http.version=HTTP/1.1` 设置；
- 安装并启用 `chat2api-admin-update.path`；
- 安装 `chat2api-admin-update.service`；
- 将当前 Git 提交写入 `data/deployment.json`；
- 将更新助手状态写入 `data/admin-updater-installed.json`。

完成后，进入 chat2api 控制台 → **版本更新** 即可检查 GitHub main 并执行更新。

## GitHub 拉取链路

从 Server Runtime `v0.22.30` 起，宿主机更新助手不再把一次 HTTPS 连接长时间阻塞当作唯一拉取路径，而是按下面顺序自动容灾：

1. `SSH-443`：使用 `git@github.com` 的现有 SSH 身份，但把实际目标强制到 GitHub 官方 `ssh.github.com:443`；
2. `SSH-22`：标准 `git@github.com:...` SSH；
3. `HTTPS`：`https://github.com/...`，强制 HTTP/1.1，并启用低速超时；
4. 三条链路都失败时再进行第二轮，随后才判定 GitHub 拉取失败。

SSH 与 HTTPS 的单次拉取都有外层 `timeout`，SSH 还使用 `BatchMode=yes`、连接超时和 keepalive，因此 systemd 更新任务不会因为密码提示、失联 socket 或单个 TLS 连接无限等待。更新日志会记录每次尝试的 `transport`、耗时和返回码；成功后 32% 状态会显示最终通道、耗时和累计尝试次数。

更新助手使用 GitHub 官方发布的 Ed25519 host key 固定验证 `github.com` / `ssh.github.com`，不会使用 `StrictHostKeyChecking=no` 或静默接受未知 host key。SSH 私钥仍由宿主机 root 的正常 OpenSSH 配置/默认 identity 提供；不会把 SSH 私钥复制或挂载进 chat2api Web 容器。若主机没有可用 SSH key，SSH 两条链路会快速失败并自动退回 HTTPS。

可选环境变量：

```text
CHAT2API_UPDATE_GITHUB_SSH_CONNECT_SECONDS=8
CHAT2API_UPDATE_GITHUB_SSH_FETCH_SECONDS=25
CHAT2API_UPDATE_GITHUB_HTTPS_FETCH_SECONDS=35
CHAT2API_UPDATE_GITHUB_HTTPS_LOW_SPEED_SECONDS=12
```

这些值默认已经针对“快速失败、自动换路”设置，一般无需修改。

## Docker 构建下载源与超时保护

生产镜像的 Python 依赖下载默认按 **HTTPS 清华 PyPI 镜像 → HTTPS 阿里云 PyPI 镜像 → 官方 https://pypi.org/simple** 顺序尝试。每个来源的 `pip install` 最多持续 900 秒，内部 pip 网络请求超时 25 秒、重试 2 次；全失败才拒绝部署。更新后端采用带有 `chat2api-pip` 命名空间的 BuildKit pip 缓存，**不**执行 `docker builder prune`，不影响同机 MV3C 的 npm 缓存。国内镜像下载源走 TLS，但 PyPI wheel 目前尚未做 SHA256 锁定校验，后续可针对构建引入 hashes lockfile。

如服务器某个镜像不可达，可在 `/opt/chat2api/.env` 中配置（只允许 HTTPS `/simple` 根地址）：

```dotenv
CHAT2API_PIP_PRIMARY_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
CHAT2API_PIP_SECONDARY_INDEX=https://mirrors.aliyun.com/pypi/simple
```

这个选项只影响 Chat2API 的镜像构建，不修改 Docker 守护进程的 registry mirrors、Docker Hub 或其他项目，也不保证修复服务器到各个镜像站的实际网络连接。修改构建参数可能使已有依赖层失效一次。GitHub Actions 的美国构建 Runner 使用官方 PyPI 验证生产镜像，其成功不代表境内镜像站在实际宿主机必然可达。

更新的 systemd `TimeoutStartSec` 从 30 分钟调整为 **75 分钟**，给三个有界安装源及故障回滚保留空间。**更新后的宿主机需要重新执行** `sudo bash /opt/chat2api/scripts/install_chat2api_server_updater.sh` 以刷新本机 systemd unit；只更新容器中的代码不会改变已安装的超时。systemd 在异常结束时通过 `ExecStopPost` 将遗留的 `running/queued` 状态变为 `failed`，但不声称回滚已经成功。

## 更新流程

网页只会写入 `data/admin-update-request.json`。宿主机 `systemd.path` 检测到文件后启动固定脚本 `scripts/chat2api_server_update.sh`。

脚本固定执行以下步骤：

1. 获取主机更新锁，防止并发更新；
2. 校验 origin、Git、Docker Compose；
3. 如果 tracked 工作区有未提交修改则拒绝更新；
4. 备份 `.env`；
5. 使用 `SSH-443 → SSH-22 → HTTPS` 有界自动容灾拉取 GitHub main；
6. 切换到远端 main；
7. 先执行 `docker compose build`，构建成功后才切换容器；
8. 有界停止并删除旧 service container（不使用 `-v`），再从已构建镜像执行 `docker compose up -d --no-deps --no-build chat2api`；
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
