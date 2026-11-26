"""Resolve archive defaults from the account belonging to the active workspace."""

from .models import OWNER_WORKSPACE_ID, Account, AccountSettings


def preferences(db, account_id, config):
    if getattr(config, "auth_mode", "legacy") != "google":
        return config
    account = db.get(Account, account_id)
    row = db.get(AccountSettings, account_id)
    values = row.values if row and row.values else {}
    owner_archive = bool(
        account
        and account.workspace_id == OWNER_WORKSPACE_ID
        and account.id == config.dm_default_account_id
    )

    def value(key, default):
        saved = values.get(key)
        return default if saved is None else saved

    return config.model_copy(
        update={
            "dm_default_account_id": account_id,
            "dm_spotify_playlist_id": config.dm_spotify_playlist_id if owner_archive else "",
            "dm_auto_music": value("shuffle_dm_music", owner_archive and config.dm_auto_music),
            "dm_self_receipts": value(
                "dm_self_receipts", owner_archive and config.dm_self_receipts
            ),
            "dm_caption_sources": value(
                "caption_sources", config.dm_caption_sources if owner_archive else ""
            ),
            "dm_default_from": config.dm_default_from if owner_archive else None,
            "dm_caption_autopost": False,
            "dm_reel_autopost": False,
        }
    )
