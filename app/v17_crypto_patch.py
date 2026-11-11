from __future__ import annotations

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
                "payment_channels": [legacy_secret, "chat2api", "chat2api-payment-v106"],
            },
        )
        app.state.data_keyring = data_keyring
    data_secret = data_keyring.root_secret

    if getattr(api_keys, "_chat2api_v17_data_key_wrapped", False):
        return app

    base_load = api_keys.load

    async def load_with_data_key_migration() -> None:
        await base_load()
        api_keys.cipher = _fernet(data_secret)
        api_keys.keyring = data_keyring
        await api_keys.migrate_cipher_from(legacy_secret)
        app.state.legacy_api_key_for_migration = ""

    api_keys.load = load_with_data_key_migration
    api_keys._chat2api_v17_data_key_wrapped = True
    return app
