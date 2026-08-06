# Existing Chrome desktop automation and request-driven model selection

chat2api v0.3 uses only the user's existing Chrome profile. The dedicated Chrome profile mode, `--user-data-dir`, `--load-extension`, and automatic extension loading have been removed.

## Prerequisites

1. Install `chrome_extension` manually once through `chrome://extensions/`.
2. Sign in to ChatGPT manually in that same Chrome profile.
3. Keep the ChatGPT login state under your own control. chat2api does not store a ChatGPT password, cookies, or verification codes.

## Architecture

```text
OpenAI-compatible request (model + messages)
        |
        v
chat2api server
        |
        | forwards requested model without relying on a stale model cache
        v
existing Chrome profile + installed chat2api extension
        |
        | precisely binds/creates the target ChatGPT tab
        | opens the current model picker, verifies and selects model
        | reports the resulting model catalog back to the server
        v
ChatGPT web
```

When Chrome and the extension are already online, the extension executes the request directly. If there is no valid bound tab, it creates and binds a fresh ChatGPT tab automatically. Other ChatGPT tabs may remain open.

## Update

```powershell
cd D:\AI\chat2api
git pull --ff-only
```

Open `chrome://extensions/`, reload **chat2api Chrome Bridge**, and confirm version `0.3.2`.

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

Only the installed Chrome extension can read the matching bootstrap payload from `127.0.0.1:8791`. The extension retries the binding until the page and local bridge are both ready. After verifying the token, it binds that exact tab and removes the marker from the address bar without reloading the page.

## Request-driven models

The `model` field in each API request is the source of truth:

```json
{
  "model": "gpt-5.6-sol-high",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": true
}
```

The server forwards the requested model to the Chrome extension. The extension opens the current ChatGPT model menu, verifies the requested family/reasoning level, selects it, and only then submits the prompt. If the option is unavailable, the API receives a browser error containing the choices that were visible to the extension.

Version 0.3.2 adds a text-compatible picker for the current ChatGPT menu. It searches visible popover text, walks to the nearest clickable ancestor, distinguishes the top-level reasoning choices from the model-family submenu, and supports menu items that do not expose stable ARIA or Radix attributes.

`GET /v1/models` remains useful as a discovered catalog, but it is advisory rather than a prerequisite. The model menu can change by account, plan, rollout, and page version, so an older catalog no longer blocks a new request before the extension has a chance to verify it.

The extension popup button **Refresh available models** only refreshes the preview catalog. API calls do not require clicking it first.

```bash
curl https://chat2api.mv3.cn/v1/models \
  -H "Authorization: Bearer YOUR_CHAT2API_API_KEY"
```

Web model selection remains experimental because it depends on the current ChatGPT page structure.
