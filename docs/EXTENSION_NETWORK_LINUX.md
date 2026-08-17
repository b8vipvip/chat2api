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
10. Only after that should the browser be configured to start automatically with the Linux desktop/session.

### Why manual first login is recommended

The browser bridge should not store ChatGPT credentials or attempt to automate CAPTCHA/2FA. The reliable boundary is the Chrome profile's own authenticated session. Once the user has logged in manually, every normal ChatGPT tab/window created by the extension uses the same profile cookies and session state.

## First configuration recommendation

Keep the existing local popup pairing flow as the source of truth for both Windows and Linux:

- `serverUrl`: chat2api server URL;
- `pairingCode`: one-time/administrator-managed pairing credential;
- `extensionName`: human-readable worker name;
- generated `deviceId`, returned `clientId` and client token remain in `chrome.storage.local` for that browser profile.

For multi-worker Linux hosts, use one persistent browser profile per worker so each profile owns a stable extension identity and ChatGPT login session. Avoid sharing one profile between simultaneously running Chrome instances.

Login readiness is diagnostic/prewarm gating only. It does not replace the final model/reasoning verification that remains authoritative immediately before a request is sent.
