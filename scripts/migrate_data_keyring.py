from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from app.config import get_settings
from app.data_keyring import DataKeyring, DataKeyringError
from app.data_keyring_migration import DataKeyringMigrationService


def _allow_empty_media_legacy() -> bool:
    return os.getenv("CHAT2API_CONFIRM_EMPTY_MEDIA_LEGACY_KEY", "").strip().lower() in {"1", "true", "yes"}


def _channel_legacy_secrets(data_dir: Path, api_key: str) -> list[str]:
    values = [api_key, "chat2api"]
    legacy_path = data_dir / ".chat2api_data_secret"
    if legacy_path.exists():
        try:
            value = legacy_path.read_text(encoding="utf-8").strip()
            values.insert(0, value or "chat2api-payment-v106")
        except OSError:
            values.insert(0, api_key or "chat2api")
    return list(dict.fromkeys(value for value in values if value))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run, apply or roll back the offline Chat2API DataKeyring migration."
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--dry-run", action="store_true", help="Stage and verify copies without changing source data.")
    actions.add_argument("--apply", action="store_true", help="Back up and migrate encrypted files.")
    actions.add_argument("--rollback", action="store_true", help="Restore the verified pre-migration backups.")
    parser.add_argument(
        "--confirm-offline",
        action="store_true",
        help="Confirm that the API and all Worker processes are stopped (required for apply/rollback).",
    )
    args = parser.parse_args()

    if (args.apply or args.rollback) and not args.confirm_offline:
        parser.error("--confirm-offline is required for --apply and --rollback")

    settings = get_settings()
    data_dir = Path(settings.data_dir)
    key_path = data_dir / DataKeyring.KEY_FILENAME
    if not key_path.is_file() or key_path.is_symlink():
        raise SystemExit("Refusing to initialize a data key during migration; restore/create data_secret.key through the normal startup preflight first.")

    api_key = str(settings.api_key or "")
    keyring = DataKeyring(
        data_dir,
        legacy_secrets={
            "api_key.recoverable": [api_key],
            "media_archive.settings": [api_key],
            "payment_channels": [api_key, "chat2api"],
        },
        allow_empty_media_legacy=_allow_empty_media_legacy(),
    )
    service = DataKeyringMigrationService(
        data_dir,
        keyring,
        legacy_media_secret=api_key,
        api_key_legacy_secrets=[api_key],
        payment_channel_legacy_secrets=_channel_legacy_secrets(data_dir, api_key),
    )

    if args.dry_run:
        result = asyncio.run(service.dry_run())
    elif args.apply:
        result = asyncio.run(service.apply())
    else:
        result = asyncio.run(service.rollback())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DataKeyringError, RuntimeError, OSError) as error:
        print(f"DataKeyring migration stopped safely: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2)
