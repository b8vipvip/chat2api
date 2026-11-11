from __future__ import annotations

import os

from fastapi import FastAPI

from .api_keys import _fernet
from .data_keyring import DataKeyring


def install_v17_crypto_patch(app: FastAPI) -> FastAPI:
    api_keys = app.state.api_keys
    legacy_secret = str(getattr(app.state, "legacy_api_key_for_migration", "") or "")
    data_keyring = getattr(app.state, "data_keyring", None)
    if data_keyring is None:
        data_keyring = DataKeyring(
            app.state.settings.data_dir,
            legacy_secrets={
                "api_key.recoverable": [legacy_secret],
                "media_archive.settings": [legacy_secret],
                "payment_channels": [legacy_secret, "chat2api"],
            },
            allow_empty_media_legacy=os.getenv("CHAT2API_CONFIRM_EMPTY_MEDIA_LEGACY_KEY", "").strip().lower() in {"1", "true", "yes"},
        )
        app.state.data_keyring = data_keyring
    data_secret = data_keyring.root_secret

    if getattr(api_keys, "_chat2api_v17_data_key_wrapped", False):
        return app

    base_load = api_keys.load

    async def load_with_data_key_validation() -> None:
        await base_load()
        api_keys.cipher = _fernet(data_secret)
        api_keys.keyring = data_keyring
        legacy_values = list(data_keyring.legacy_secrets_for("api_key.recoverable"))
        if legacy_secret and legacy_secret not in legacy_values:
            legacy_values.append(legacy_secret)
        api_keys.legacy_ciphers = [
            cipher for cipher in (_fernet(value) for value in legacy_values) if cipher is not None
        ]
        # Startup validates every ciphertext but does not rewrite legacy files.
        # Migration is a separate, explicit maintenance operation.
        await api_keys.validate_ciphertexts()
        app.state.legacy_api_key_for_migration = ""

    api_keys.load = load_with_data_key_validation
    api_keys._chat2api_v17_data_key_wrapped = True
    return app
