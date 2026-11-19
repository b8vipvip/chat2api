from __future__ import annotations

import json

from app.diagnostics import (
    DiagnosticStore,
    _decode_headers,
    _safe_path,
    _safe_query,
    safe_error_response,
)


def test_diagnostic_query_and_referer_redact_secret_parameters_but_keep_safe_ids():
    query = (
        b"refreshToken=refresh-secret&api_key=api-secret&password=pass-secret"
        b"&tokenCount=42&apiKeyID=key_safe_identifier"
    )
    safe = _safe_query(query)
    for secret in ("refresh-secret", "api-secret", "pass-secret"):
        assert secret not in safe
    assert "tokenCount=42" in safe
    assert "apiKeyID=key_safe_identifier" in safe

    headers = _decode_headers([
        (b"referer", b"https://user:pass@example.test/private?access_token=ref-secret"),
        (b"origin", b"https://example.test"),
    ])
    assert headers["referer"] == "https://example.test"
    assert headers["origin"] == "https://example.test"
    assert "ref-secret" not in json.dumps(headers)


def test_diagnostic_error_redaction_is_recursive_and_handles_plain_text():
    secrets = (
        "json-access-secret", "nested-refresh-secret", "client-secret-value",
        "bearer-secret-value", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signaturesecretvalue",
    )
    payload = {
        "detail": {
            "Authorization": "Bearer bearer-secret-value",
            "refreshToken": secrets[1],
            "clientSecret": secrets[2],
            "tokenCount": 7,
            "tokenID": "token_safe_identifier",
            "nested": {"access_token": secrets[0], "jwt": secrets[4]},
        },
        "message": "request failed api_key=json-access-secret",
        "safe": "kept-out-of-export",
    }
    clean = safe_error_response(json.dumps(payload).encode(), "application/json")
    serialized = json.dumps(clean)
    for secret in secrets:
        assert secret not in serialized
    assert clean["detail"]["tokenCount"] == 7
    assert clean["detail"]["tokenID"] == "token_safe_identifier"

    plain = safe_error_response(
        b"provider rejected Authorization: Bearer bearer-secret-value "
        b"refresh_token=nested-refresh-secret",
        "text/plain",
    )
    assert "bearer-secret-value" not in plain
    assert "nested-refresh-secret" not in plain


def test_diagnostic_paths_redact_capability_tokens():
    paths = (
        "/api/media/media_12345678/media-capability-secret/preview",
        "/api/generated-files/pending/file_12345678/pending-capability-secret/status",
        "/api/generated-files/file_12345678/generated-capability-secret",
    )
    for path in paths:
        safe = _safe_path(path)
        assert "capability-secret" not in safe
        assert "[REDACTED]" in safe


def test_diagnostic_store_sanitizes_new_and_legacy_events(tmp_path):
    store = DiagnosticStore(tmp_path)
    store.path.write_text(
        json.dumps({
            "trace_id": "legacy",
            "response_error": {"detail": {"sessionToken": "legacy-session-secret"}},
        }) + "\n",
        encoding="utf-8",
    )
    store = DiagnosticStore(tmp_path)
    assert "legacy-session-secret" not in json.dumps(store.recent())

    import asyncio
    asyncio.run(store.append({
        "trace_id": "new",
        "exception": "Authorization: Bearer new-session-secret",
        "headers": {"cookie": "new-cookie-secret", "user-agent": "safe-agent"},
    }))
    persisted = store.path.read_text(encoding="utf-8")
    visible = json.dumps(store.recent())
    for secret in ("new-session-secret", "new-cookie-secret"):
        assert secret not in persisted
        assert secret not in visible
    assert store.by_trace("new")[0]["headers"]["cookie"] == "[REDACTED]"

