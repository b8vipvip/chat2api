# Existing Chrome desktop automation and request-driven model selection

chat2api uses the user's existing Chrome profile. Install the extension manually once and keep ChatGPT signed in yourself; chat2api does not store the ChatGPT password, cookies, or verification codes.

## Model routing strategy (0.3.4)

`model` is request-driven. The fast path is `default`:

```json
{
  "model": "default",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": true
}
```

`default` means: use whatever model and reasoning level the bound ChatGPT composer currently has selected. The extension does **not** open or modify any model UI. `chatgpt-web` remains a backwards-compatible alias for the same zero-touch behavior. If `model` is omitted, the server now defaults to `default`.

For an explicit model such as `gpt-5.6-sol-high`, the extension:

1. Waits up to 30 seconds for the unified composer and prompt input to become usable.
2. Selects the model family first (`GPT-5.6 Sol`, `GPT-5.5`, `GPT-5.3`, `o3`).
3. Selects reasoning strength second.
4. Only then submits the prompt.

The model-family path is scoped to the composer menu and follows the current `高级 / Advanced -> 模型 / Model -> family` structure with visible-text fallbacks and post-action verification.

Reasoning selection is deliberately hybrid:

- First try the ChatGPT shortcut `Ctrl+Shift+M`.
- If the shortcut opens a reasoning slider, set it by position (`instant` low, `medium` center, `high` high).
- If that is unavailable, open the composer pill directly.
- If the slider still is unavailable, fall back to `高级 / Advanced -> 思考强度 / 思考程度 -> 极速 / 中 / 高`.

This minimizes dependence on any single DOM structure. Synthetic shortcut events are best-effort because a future ChatGPT build may require trusted keyboard events; the DOM/slider/menu paths remain as fallbacks.

## Existing Chrome and automatic binding

The desktop client opens a short-lived launch URL in the existing Chrome profile:

```text
https://chatgpt.com/?chat2api_launch=<one-time-token>
```

The extension validates the local bootstrap token, binds that exact tab, then removes the marker. Binding no longer waits for Chrome's `tab.status == complete`; it retries until the ChatGPT page controller is injectable/responding. Composer readiness is handled separately by the model controller. This avoids false `Timed out waiting for the new ChatGPT tab` errors on slowly rendered ChatGPT SPA pages.

## Update

```powershell
cd D:\AI\chat2api
git pull --ff-only
```

Open `chrome://extensions/`, reload **chat2api Chrome Bridge**, and confirm version `0.3.4`.

Because the API request default changed to `default`, update/rebuild the server as well:

```bash
cd /opt/chat2api
git pull --ff-only
docker compose -f docker-compose.server.yml up -d --build
```

## Example IDs

- `default` — zero-touch current ChatGPT selection
- `chatgpt-web` — compatibility alias for `default`
- `gpt-5.6-sol`
- `gpt-5.6-sol-instant`
- `gpt-5.6-sol-medium`
- `gpt-5.6-sol-high`
- `gpt-5.5`
- `gpt-5.5-instant`
- `gpt-5.5-medium`
- `gpt-5.5-high`
- `gpt-5.3`
- `o3`

Actual availability depends on what the signed-in ChatGPT account exposes at that moment.

## Model discovery

`GET /v1/models` is advisory. The web UI changes by account, rollout and page version, so API execution does not depend on a stale cached catalog. Manual **Refresh available models** performs discovery only; normal explicit requests select and verify the requested model at execution time without doing a second full discovery pass.

Web model selection remains experimental because it depends on the current ChatGPT web application. The code intentionally uses semantic anchors, multiple fallbacks and post-action verification so UI changes fail explicitly instead of silently selecting the wrong control.
