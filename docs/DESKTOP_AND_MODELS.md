# Desktop wake-up and dynamic model selection

This document covers the experimental v0.2 workflow. The normal manually installed extension workflow remains supported.

## Architecture

```text
OpenAI-compatible request
        |
        v
chat2api server
        |
        | no extension online -> long-poll wake command
        v
chat2api desktop client on Windows
        |
        | starts dedicated Chrome profile and local bootstrap bridge
        v
chat2api Chrome extension
        |
        | pairs, binds the only ChatGPT tab, discovers models
        v
ChatGPT web
```

## Server configuration

Add the public URL and wake timeout to `.env`:

```env
CHAT2API_PUBLIC_URL=https://chat2api.mv3.cn
CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=45
```

Rebuild the server after updating to v0.2:

```bash
cd /opt/chat2api
git pull --ff-only
docker compose -f docker-compose.server.yml up -d --build
```

## Windows desktop client

The desktop client stores the server API key in `%LOCALAPPDATA%\chat2api\client.json`. Protect this file like a password.

Configure once:

```powershell
cd D:\AI\chat2api
.\scripts\configure_desktop_client.ps1 `
  -ServerUrl "https://chat2api.mv3.cn" `
  -ApiKey "YOUR_CHAT2API_API_KEY"
```

Open the dedicated Chrome profile immediately for first-time ChatGPT login:

```powershell
.\scripts\run_desktop_client.ps1 -LaunchNow
```

After signing in once, keep the desktop client running without `-LaunchNow`:

```powershell
.\scripts\run_desktop_client.ps1
```

When an API request arrives and no extension is online, the server asks the desktop client to start Chrome. The client exposes the pairing data only on `127.0.0.1:8791` for a short bootstrap window. The extension pairs automatically and binds the only ChatGPT tab.

Chrome still controls account authentication. The desktop client does not store or submit the ChatGPT password. If the session expires, sign in manually in the dedicated profile again.

## Dynamic models

After binding a ChatGPT tab, open the extension and select **Refresh available models**. The extension reads the options currently visible to that account and reports them to the server.

Query the resulting catalog:

```bash
curl https://chat2api.mv3.cn/v1/models \
  -H "Authorization: Bearer YOUR_CHAT2API_API_KEY"
```

Example IDs may include:

```text
gpt-5.6-sol-medium
gpt-5.6-sol-high
gpt-5.5-instant
gpt-5.5
gpt-5.3
o3
```

The exact list is account- and UI-dependent. `chatgpt-web` always means keep the currently selected/default model.

Select a reported model in an API call:

```json
{
  "model": "gpt-5.6-sol-high",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": true
}
```

The extension opens the ChatGPT model picker and selects the requested family and reasoning level before sending the prompt. Because this relies on the ChatGPT web UI, a UI redesign can require selector updates.

## Image and Live voice roadmap

Image generation and Live voice are not exposed as completed APIs in v0.2.

- Image generation needs a separate job API that can open the Images experience, wait for generation, download the actual image asset, and return files or URLs. It should not be represented as a text-only chat completion.
- Live voice needs a realtime audio transport, browser microphone/speaker routing, interruption handling, and audio streaming. It should use a dedicated WebSocket/WebRTC-style API rather than returning text chunks from `/v1/chat/completions`.

These two paths will be implemented separately after the text/model path is stable.
