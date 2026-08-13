from __future__ import annotations

from fastapi import FastAPI

from .api_keys import _fernet, load_or_create_data_secret


def install_v17_crypto_patch(app: FastAPI) -> FastAPI:
    api_keys = app.state.api_keys
    legacy_secret = str(getattr(app.state, "legacy_api_key_for_migration", "") or "")
    data_secret = load_or_create_data_secret(app.state.settings.data_dir)

    if getattr(api_keys, "_chat2api_v17_data_key_wrapped", False):
        return app

    base_load = api_keys.load

    async def load_with_data_key_migration() -> None:
        await base_load()
        api_keys.cipher = _fernet(data_secret)
        if legacy_secret:
            await api_keys.migrate_cipher_from(legacy_secret)
        app.state.legacy_api_key_for_migration = ""

    api_keys.load = load_with_data_key_migration
    api_keys._chat2api_v17_data_key_wrapped = True
    return app
