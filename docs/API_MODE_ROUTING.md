# API Mode Routing Contract

Status: Mode 2-only API ingress.

## Default

All HTTP requests under `/v1` resolve to **Mode 2 (OpenAI-compatible API)**. Existing OpenAI SDK clients do not need to send a mode header.

The ingress middleware records the resolved values on ASGI request state:

- `chat2api_mode: mode2`
- `chat2api_mode_source: default or header`
- `chat2api_entrypoint: openai_api`

Downstream handlers must treat this resolution as authoritative and must not infer a different mode from body fields, query parameters, model names, or user-controlled metadata.

## Optional header

`X-Chat2API-Mode` is a Chat2API compatibility extension, not a required OpenAI protocol field.

| Header value | Result |
|---|---|
| omitted | Mode 2 |
| `api` | Mode 2 |
| `mode2` | Mode 2 |
| unknown, empty, or non-API value | HTTP 400 `invalid_mode` |
| duplicate header | HTTP 400 `duplicate_mode_header` |
| header on a non-`/v1` HTTP path | HTTP 400 `mode_header_not_supported_for_entrypoint` |

The error response uses the standard OpenAI-style error object. The API ingress never routes to a browser session or a web-chat entrypoint.

## Chat Completions message semantics

The standard `messages` array is the conversation context. For `POST /v1/chat/completions`, the default is `prompt_mode=full`: system/developer instructions and the ordered user/assistant/tool history are preserved for the current request. Standard OpenAI SDK clients do not need to send any Chat2API-specific field to receive full-history behavior.

`prompt_mode=last_user` remains available as an explicit Chat2API extension for legacy one-turn callers. It is opt-in and must not be assumed by clients that omit the field.

## Compatibility guarantees

- An unmarked `/v1` request stays on Mode 2.
- Existing request bodies and standard OpenAI SDK payloads are not modified.
- The middleware does not read or buffer request bodies or streaming responses.
- WebSocket scopes are passed through unchanged.
- Unknown API paths remain subject to the normal router and return their ordinary 404 response; they do not fall through to a browser or proxy route.
