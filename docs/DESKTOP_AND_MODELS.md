# Existing Chrome desktop automation and dynamic model selection

chat2api v0.3 uses only the user's existing Chrome profile. The dedicated Chrome profile mode, `--user-data-dir`, `--load-extension`, and automatic extension loading have been removed.

## Prerequisites

1. Install `chrome_extension` manually once through `chrome://extensions/`.
2. Sign in to ChatGPT manually in that same Chrome profile.
3. Keep the ChatGPT login state under your own control. chat2api does not store a ChatGPT password, cookies, or verification codes.

## Architecture

```text
OpenAI-compatible request
        |
        v
chat2api server
        |
        | extension offline -> desktop wake command
        v
chat2api desktop client
        |
        | opens normal chrome.exe with a one-time launch token
        v
existing Chrome profile + already-installed chat2api extension
        |
        | reads short-lived localhost bootstrap, pairs/reconnects,
        | precisely binds the launched ChatGPT tab
        v
ChatGPT web
```

When Chrome and the extension are already online, the extension executes the request directly. If there is no valid bound tab, it creates and binds a fresh ChatGPT tab automatically. Other ChatGPT tabs may remain open.

## Update

```powershell
cd D:\AI\chat2api
git pull --ff-only
```

Open `chrome://extensions/`, reload **chat2api Chrome Bridge**, and confirm version `0.3.0`.

## Configure the desktop client

```powershell
cd D:\AI\chat2api
.\scripts\configure_desktop_client.ps1 `
  -ServerUrl "https://chat2api.mv3.cn" `
  -ApiKey "YOUR_CHAT2API_API_KEY"
```

The client saves its configuration in:

```text
%LOCALAPPDATA%\chat2api\client.json
```

Old `extension_dir` and `profile_dir` fields are ignored and removed the next time the configuration is saved.

## First run

Make sure the extension is installed and ChatGPT is already signed in, then run:

```powershell
.\scripts\run_desktop_client.ps1 -LaunchNow
```

This opens a new window in the existing Chrome profile. It does not create another profile and does not load an extension from the command line.

Normal background operation:

```powershell
.\scripts\run_desktop_client.ps1
```

## Automatic binding

The desktop client opens a URL containing a short-lived random marker:

```text
https://chatgpt.com/?chat2api_launch=<one-time-token>
```

Only the installed Chrome extension can read the matching bootstrap payload from `127.0.0.1:8791`. After verifying the token, it binds that exact tab and removes the marker from the address bar without reloading the page.

## Dynamic models

Open the extension and click **Refresh available models**, then query:

```bash
curl https://chat2api.mv3.cn/v1/models \
  -H "Authorization: Bearer YOUR_CHAT2API_API_KEY"
```

Use only model IDs returned by the endpoint. Web model selection remains experimental because it depends on the current ChatGPT page structure.
