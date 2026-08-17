# Extension startup network gate and Linux support

## Startup behavior

Chrome Bridge 0.7.7 keeps the existing browser-start connection behavior: the Manifest V3 service worker calls `connectSocket()` when it is loaded and also listens to `chrome.runtime.onStartup`.

After the server accepts the authenticated extension WebSocket, the extension checks the browser's current egress country before background prewarming:

1. The saved `client_id` and client token attempt to connect to the configured chat2api server.
2. Server-side `connection_enabled` remains authoritative. A disabled extension cannot authenticate its WebSocket.
3. Once `socketState` becomes `connected`, `background_network_v26.js` performs a cached IP-country lookup from the browser process.
4. `CN` is classified as `china-mainland` and proactive warm-window creation is blocked.
5. Any valid non-`CN` country code is classified as `external` and the warm pool may immediately prepare its ChatGPT windows.
6. Offline state, lookup timeout, malformed data or provider failure fail closed for **proactive** prewarming only.
7. Request-time routing is intentionally unchanged. If an API request arrives without a warm slot, the existing request path may still create the ChatGPT window on demand.

The country lookup is cached for 30 minutes after a valid result and for 2 minutes after an error/offline result. Concurrent callers share one in-flight lookup. The extension persists only the country code and probe status; it does not persist the public IP returned by the lookup provider.

Popup status exposes the current platform and one of these network states:

- external country: proactive prewarm allowed;
- China mainland (`CN`): proactive prewarm blocked;
- offline;
- lookup error;
- unknown / not checked yet.

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
5. Open `https://chatgpt.com/` in a **visible** normal window and complete OpenAI sign-in manually, including email verification, CAPTCHA or 2FA if requested.
6. Confirm that the ChatGPT composer is visible. Then run the popup Page Smoke Test.
7. Restart Chrome once and confirm that the same profile remains logged in and the popup returns to `connected` automatically.
8. Only after that should the browser be configured to start automatically with the Linux desktop/session.

### Why manual first login is recommended

The browser bridge should not store ChatGPT credentials or attempt to automate CAPTCHA/2FA. The reliable boundary is the Chrome profile's own authenticated session. Once the user has logged in manually, every normal ChatGPT tab/window created by the extension uses the same profile cookies and session state.

### Recommended login-state design for the next phase

Add an explicit browser login readiness state separate from account plan detection:

- `ready`: ChatGPT page is loaded and a usable composer is visible;
- `login_required`: ChatGPT/auth page is reachable but no authenticated composer is available and login UI/redirect evidence is present;
- `checking`: a startup/login probe is in progress;
- `unknown`: no reliable conclusion yet.

For a new Linux worker, the popup should offer a single `打开 ChatGPT 登录窗口` action when the state is `login_required` or `unknown`. That window should be focused and user-driven. After the composer becomes ready, normal background warm windows can remain unfocused.

This login readiness state should be diagnostic only and must not replace the final model/reasoning verification used before sending a request.

## First configuration recommendation

Keep the existing local popup pairing flow as the source of truth for both Windows and Linux:

- `serverUrl`: chat2api server URL;
- `pairingCode`: one-time/administrator-managed pairing credential;
- `extensionName`: human-readable worker name;
- generated `deviceId`, returned `clientId` and client token remain in `chrome.storage.local` for that browser profile.

For multi-worker Linux hosts, use one persistent browser profile per worker so each profile owns a stable extension identity and ChatGPT login session. Avoid sharing one profile between simultaneously running Chrome instances.
