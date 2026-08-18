# Unattended Linux worker autostart

This deployment mode is for a Linux Chrome Bridge worker that has already been paired and manually signed into ChatGPT once with a persistent Chrome profile.

It intentionally does **not** depend on an XRDP login session after setup.

## What starts at boot

The installer creates three systemd services:

- `chat2api-xray.service`: runs the currently working v2rayN/Xray proxy configuration directly as the worker user;
- `chat2api-xvfb.service`: provides a persistent virtual X11 display (`:99` by default);
- `chat2api-chrome.service`: starts normal Google Chrome with the existing persistent worker profile and routes ChatGPT traffic through `127.0.0.1:10808`.

The v2rayN GUI is not required in production. The installer captures the **currently running** Xray configuration from the process that owns the configured local proxy port. This keeps the already-tested VLESS/WS/TLS node exactly as it is at installation time without storing node credentials in the repository.

## Preconditions

Before running the installer:

1. Log in once through XFCE/XRDP as the dedicated worker user (default: `chat2api`).
2. Start v2rayN, select and verify the intended node, and confirm Xray is listening on `127.0.0.1:10808`.
3. Start Chrome with the dedicated profile, load the `chrome_extension` directory, pair it, manually sign into ChatGPT, and run Page Smoke successfully.
4. Keep the working Xray core running so the installer can capture its executable, working directory and `config.json`.

## Install

Run from the repository as root:

```bash
cd /opt/chat2api
chmod +x scripts/install_linux_worker_autostart.sh
./scripts/install_linux_worker_autostart.sh
```

Defaults:

```text
WORKER_USER=chat2api
PROFILE_DIR=/home/chat2api/.config/chat2api-chrome-worker-01
PROXY_PORT=10808
DISPLAY_NUM=99
CHATGPT_URL=https://chatgpt.com/
BYPASS_LIST=localhost;127.0.0.1;chat2api.mv3.cn
```

The installer stops only the dedicated Chrome profile and the Xray process that currently owns the selected proxy port. It does not terminate unrelated Chrome processes used by other software on the host.

## Verify

```bash
systemctl status chat2api-xray chat2api-xvfb chat2api-chrome --no-pager
ss -lntp | grep ':10808'
pgrep -af 'google-chrome.*chat2api-chrome-worker-01'
```

The server admin console should show the paired extension returning online after Chrome starts.

## Logs

```bash
journalctl -u chat2api-xray -n 100 --no-pager
journalctl -u chat2api-xvfb -n 100 --no-pager
journalctl -u chat2api-chrome -n 100 --no-pager
```

## Reboot test

After all three services are healthy:

```bash
reboot
```

After the server returns:

```bash
systemctl is-active chat2api-xray chat2api-xvfb chat2api-chrome
ss -lntp | grep ':10808'
```

Confirm in `/admin` that the extension reconnects automatically and that ChatGPT login readiness returns to `ready`.

## Changing the proxy node later

Because production systemd runs a captured Xray config, changing a node in the v2rayN GUI does not automatically rewrite the captured production config.

For maintenance:

1. stop `chat2api-xray` and `chat2api-chrome`;
2. start v2rayN in the maintenance XFCE session;
3. select and test the new node until `127.0.0.1:10808` works;
4. rerun `scripts/install_linux_worker_autostart.sh` to recapture the working config;
5. verify all three systemd services again.

Do not commit the captured Xray config to Git. It may contain private proxy credentials.
