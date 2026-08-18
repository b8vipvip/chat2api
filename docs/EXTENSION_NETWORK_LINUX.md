# Extension startup network gate and Linux support

## Startup behavior

Chrome Bridge 0.7.8 keeps the existing browser-start connection behavior: the Manifest V3 service worker calls `connectSocket()` when it is loaded and also listens to `chrome.runtime.onStartup`.

After the server accepts the authenticated extension WebSocket, proactive prewarming now requires both a supported network location and a currently validated ChatGPT login session:

1. The saved `client_id` and client token attempt to connect to the configured chat2api server.
2. Server-side `connection_enabled` remains authoritative. A disabled extension cannot authenticate its WebSocket.
3. Once `socketState` becomes `connected`, `background_network_v26.js` performs a cached IP-country lookup from the browser process.
4. `CN` is classified as `china-mainland` and proactive warm-window creation is blocked.
5. Any valid non-`CN` country code is classified as `external` and passes the network half of the proactive prewarm gate.
6. `background_login_v27.js` then requires a fresh `ready` login-readiness result with a visible ChatGPT composer before dedicated warm windows are allowed.
7. If no ChatGPT page exists, the extension creates one unfocused startup-readiness window. If the existing Chrome profile is already signed in, the visible composer confirms the session and that temporary readiness window is retired before the dedicated warm pool takes over.
8. If that page redirects to an OpenAI authentication flow or exposes explicit login controls, the state becomes `login_required`. The popup action `打开 ChatGPT 登录窗口` focuses/reuses that existing window instead of creating a duplicate.
9. Offline state, `CN`, country-lookup failure, or unconfirmed ChatGPT login state fail closed for **proactive** prewarming only.
10. Request-time routing is intentionally unchanged. If an API request arrives without a warm slot, the existing request path may still create the ChatGPT window on demand.

The country lookup is cached for 30 minutes after a valid result and for 2 minutes after an error/offline result. Concurrent callers share one in-flight lookup. The extension persists only the country code and probe status; it does not persist the public IP returned by the lookup provider.

Login readiness is intentionally separate from account-plan detection and uses these states:

- `ready`: a usable visible ChatGPT composer is present;
- `login_required`: authentication redirect/path or explicit visible login controls are present and no composer is ready;
- `checking`: the startup/login readiness page is still being evaluated;
- `unknown`: there is not enough passive evidence yet.

The login detector is passive. It does not click page controls, type credentials, dispatch keyboard events, install a mutation observer, or automate CAPTCHA/2FA.

Popup status exposes the current platform, network state and ChatGPT login readiness. A user-visible login window is never treated as an expendable background readiness probe.

## Linux Chrome support

There is no separate Linux extension build. The same Manifest V3 package is used on Windows, Linux and macOS. `background_platform_v26.js` calls `chrome.runtime.getPlatformInfo()` and reports/stores the detected OS and CPU architecture. Linux is an explicitly supported desktop platform.

The extension does not depend on Win32 APIs, Windows paths or a native Windows helper for the normal browser bridge path.

## Recommended Linux deployment model

Use a dedicated persistent Chrome/Chromium profile for each chat2api worker. Do not use temporary/incognito profiles for production workers because ChatGPT login state and extension local storage must survive browser restarts.

Recommended first setup:

1. Start normal desktop Chrome/Chromium with a dedicated profile, for example a profile named `chat2api-worker-01`.
2. Load the repository's `chrome_extension` directory as an unpacked extension.
3. Open the extension popup and configure server URL, pairing code and a descriptive extension name such as `linux-worker-01`.
4. Pair once. The extension stores its device/client identity in that Chrome profile and automatically reconnects on later browser starts.
5. If popup reports `ChatGPT：需要登录` or the state is still unknown, click `打开 ChatGPT 登录窗口`.
6. Complete OpenAI sign-in manually in that visible normal window, including email verification, CAPTCHA or 2FA if requested.
7. Wait for popup to report `ChatGPT：已登录，可用 · Composer 已确认`.
8. Run the popup Page Smoke Test once.
9. Restart Chrome once and confirm that the same profile remains logged in, the extension returns to `connected`, login readiness returns to `ready`, and external-network prewarming resumes automatically.
10. Only after that should the worker be switched to the unattended systemd path with `scripts/install_linux_worker_autostart.sh`.

### Why manual first login is recommended

The browser bridge should not store ChatGPT credentials or attempt to automate CAPTCHA/2FA. The reliable boundary is the Chrome profile's own authenticated session. Once the user has logged in manually, every normal ChatGPT tab/window created by the extension uses the same profile cookies and session state.

## Unattended systemd worker

`scripts/install_linux_worker_autostart.sh` captures the currently working Xray configuration and installs five pieces of host automation:

- `chat2api-xray.service`: the captured Xray proxy on `127.0.0.1:10808`;
- `chat2api-xvfb.service`: the virtual X display used by the production browser;
- `chat2api-chrome.service`: Google Chrome using the persistent worker profile and the local SOCKS proxy;
- `chat2api-worker-watchdog.timer`: a periodic health check that runs `chat2api-worker-watchdog.service` roughly every two minutes;
- `chat2api-extension-autoreload.timer`: a source-version watcher that runs `chat2api-extension-autoreload.service` roughly every minute.

The installer is intentionally idempotent. Once systemd Xray already runs from `${WORKER_CONFIG_DIR}/xray-config.json`, a later installer run recognizes that the live source config and captured destination are the same file and does not call `install(1)` on the same path. This allows later automation units to be installed during routine production updates.

The watchdog is deliberately conservative. It checks:

1. the persistent Chrome profile still exists and is owned by the expected worker user;
2. the unpacked Chrome Bridge source still contains `manifest.json`;
3. Xray is active and the configured local proxy port is actually listening;
4. Xvfb is active;
5. the dedicated Chrome service is active and a Chrome process using the expected `user-data-dir` exists;
6. ChatGPT can be reached through the worker SOCKS path;
7. the configured chat2api `/healthz` endpoint is reachable.

A definitively dead local Xray, missing proxy listener, dead Xvfb, or dead Chrome worker can be restarted once automatically. The watchdog does **not** repeatedly restart healthy local processes merely because an upstream proxy node, DNS path, ChatGPT, or the chat2api server is temporarily unavailable. Those conditions are logged as degraded failures for operator visibility.

The watchdog also refuses to create, re-own, or replace a missing/mis-owned production Chrome profile. Automatically creating a fresh profile could silently lose the paired extension identity and the saved ChatGPT session, so that condition requires manual repair.

ChatGPT `login_required` is intentionally not repaired by the host watchdog. The extension already reports login readiness through its normal metadata path; expired login, CAPTCHA and 2FA remain a visible/manual account operation.

## Automatic unpacked-extension update

The production worker loads `chrome_extension/` as an unpacked extension from the repository path. Chrome does not reliably reload an already-running unpacked extension just because files on disk changed, so Linux production uses an explicit source watcher.

`chat2api-extension-autoreload.service` records the currently deployed extension source fingerprint and the `version` from `chrome_extension/manifest.json` under:

```text
/var/lib/chat2api-worker/extension-state.env
```

When the repository has been updated and the Git tree for `chrome_extension/` changes, the watcher:

1. waits if a Git index update is still in progress;
2. refuses automatic reload if `chrome_extension/` has local/uncommitted changes;
3. reads the new manifest version and Git tree fingerprint;
4. restarts only `chat2api-chrome.service`;
5. waits for the production Chrome process using the expected persistent `user-data-dir` to return;
6. records the new version, fingerprint, Git commit and applied time.

A server-only repository update does not change the extension tree and therefore does not restart Chrome.

If one extension fingerprint fails to restart Chrome successfully, that exact fingerprint is recorded as failed and is **not retried every minute**. A later source change produces a new fingerprint and becomes eligible for one fresh attempt. This avoids an automatic restart storm caused by one bad extension revision.

The watcher does not run `git pull` by itself. Repository deployment remains an explicit operator/deployment action; once the working tree is updated, extension activation is automatic. This keeps server deployment, Docker rebuilds and browser reloads as separate safety domains.

The formal Chrome Bridge release version remains `chrome_extension/manifest.json`. The host never rewrites the manifest locally, so the repository stays clean for future `git pull`. If a future extension release updates the manifest version in Git, the watcher reloads that version automatically and records it in `extension-state.env`.

Useful commands after installation:

```bash
systemctl status \
  chat2api-xray \
  chat2api-xvfb \
  chat2api-chrome \
  chat2api-worker-watchdog.timer \
  chat2api-extension-autoreload.timer \
  --no-pager

systemctl start chat2api-worker-watchdog.service
systemctl start chat2api-extension-autoreload.service

journalctl -u chat2api-worker-watchdog -n 100 --no-pager
journalctl -u chat2api-extension-autoreload -n 100 --no-pager

systemctl list-timers \
  chat2api-worker-watchdog.timer \
  chat2api-extension-autoreload.timer \
  --no-pager

cat /var/lib/chat2api-worker/extension-state.env
```

Do not run a second Chrome instance against the same `user-data-dir` at the same time.

## First configuration recommendation

Keep the existing local popup pairing flow as the source of truth for both Windows and Linux:

- `serverUrl`: chat2api server URL;
- `pairingCode`: one-time/administrator-managed pairing credential;
- `extensionName`: human-readable worker name;
- generated `deviceId`, returned `clientId` and client token remain in `chrome.storage.local` for that browser profile.

For multi-worker Linux hosts, use one persistent browser profile per worker so each profile owns a stable extension identity and ChatGPT login session. Avoid sharing one profile between simultaneously running Chrome instances.

Login readiness is diagnostic/prewarm gating only. It does not replace the final model/reasoning verification that remains authoritative immediately before a request is sent.
