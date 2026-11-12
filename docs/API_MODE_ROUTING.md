# API Mode Routing Contract

Status: implemented ingress guard; Mode 1 data plane remains NO-GO and unimplemented.

## Default

All HTTP requests under /v1 resolve to **Mode 2 (OpenAI-compatible API)** unless the optional X-Chat2API-Mode extension header is supplied. Existing OpenAI SDK clients do not need to send this header.

The ingress middleware records the resolved values on the ASGI request state:

- chat2api_mode: mode2
- chat2api_mode_source: default or header
- chat2api_entrypoint: openai_api

Downstream handlers must treat this resolution as authoritative and must not infer a different mode from body fields, query parameters, model names, or user-controlled metadata.

## Optional header

The header is a Chat2API extension, not a required OpenAI protocol field.

| Header value | Result |
|---|---|
| omitted | Mode 2 |
| api | Mode 2 |
| mode2 | Mode 2 |
| chat or mode1 on /v1/* | HTTP 400 mode_entrypoint_conflict |
| unknown/empty value | HTTP 400 invalid_mode |
| duplicate header | HTTP 400 duplicate_mode_header |
| header on a non-/v1 HTTP path | HTTP 400 mode_header_not_supported_for_entrypoint |

The error response uses the standard OpenAI-style error object.

## Mode 1 boundary

Mode 1 is a separate web-proxy entrypoint, not an OpenAI API transport. The middleware does not create a proxy route, accept upstream URLs, collect cookies, read browser sessions, or forward private web API traffic.

The hosted multi-user private-web proxy remains NO-GO pending explicit written authorization and a separately reviewed implementation. The admin Mode 1 checkbox records test intent only; effective_enabled remains false.

## Compatibility guarantees

- An unmarked /v1 request stays on Mode 2.
- Existing request bodies and standard OpenAI SDK payloads are not modified.
- The middleware does not read or buffer request bodies or streaming responses.
- WebSocket scopes are passed through unchanged.
- Unknown API paths remain subject to the normal router and return their ordinary 404 response; they do not fall through to a proxy.
