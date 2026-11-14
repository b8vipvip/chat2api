from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.responses_v108_patch import _store_response, install_responses_v108_patch


def test_responses_retrieval_is_scoped_to_owner_and_master_remains_privileged(tmp_path: Path) -> None:
    app = create_app(Settings(
        CHAT2API_API_KEY="master-secret",
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_responses_v108_patch(app)

    with TestClient(app) as client:
        alice_key, alice_token = asyncio.run(app.state.api_keys.create("Alice response key"))
        bob_key, bob_token = asyncio.run(app.state.api_keys.create("Bob response key"))
        response_id = "resp_owner_scope_123456"
        response = {
            "id": response_id,
            "object": "response",
            "output_text": "Alice private response",
        }
        _store_response(app, response_id, response, alice_key["key_id"])

        alice = client.get(
            f"/v1/responses/{response_id}",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert alice.status_code == 200
        assert alice.json()["output_text"] == "Alice private response"

        # Bob is authenticated, but knowing Alice's response ID is not an
        # ownership grant. Return the same 404 as an unknown response.
        bob = client.get(
            f"/v1/responses/{response_id}",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
        assert bob.status_code == 404
        assert bob.json()["error"]["code"] == "response_not_found"

        # The configured master API key intentionally retains cross-key
        # administrative retrieval access.
        master = client.get(
            f"/v1/responses/{response_id}",
            headers={"Authorization": "Bearer master-secret"},
        )
        assert master.status_code == 200
        assert master.json()["output_text"] == "Alice private response"

        # Missing ownership metadata must fail closed, not silently become
        # readable by any authenticated managed key or master.
        orphan_id = "resp_owner_scope_orphan"
        app.state.responses_v108_store[orphan_id] = {
            "id": orphan_id,
            "object": "response",
            "output_text": "orphaned response",
        }
        assert client.get(
            f"/v1/responses/{orphan_id}",
            headers={"Authorization": f"Bearer {bob_token}"},
        ).status_code == 404
        assert client.get(
            f"/v1/responses/{orphan_id}",
            headers={"Authorization": "Bearer master-secret"},
        ).status_code == 404


def test_responses_owner_metadata_is_evicted_with_bounded_response_store() -> None:
    from collections import OrderedDict
    from types import SimpleNamespace

    app = SimpleNamespace(state=SimpleNamespace(
        responses_v108_store=OrderedDict(),
        responses_v108_owners={},
    ))
    for index in range(257):
        response_id = f"resp_owner_scope_{index:03d}"
        _store_response(app, response_id, {"id": response_id}, f"key_{index:03d}")

    assert len(app.state.responses_v108_store) == 256
    assert len(app.state.responses_v108_owners) == 256
    assert "resp_owner_scope_000" not in app.state.responses_v108_store
    assert "resp_owner_scope_000" not in app.state.responses_v108_owners
    assert app.state.responses_v108_owners["resp_owner_scope_256"] == "key_256"
