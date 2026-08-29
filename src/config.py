"""
Single source of truth for environment configuration.

Importing this module loads the repo-root ``.env`` (if present) into the
process environment, once. Real environment variables always win over
``.env`` values, so GitHub Actions secrets are never shadowed.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(_REPO_ROOT / ".env", override=False)


def database_url() -> str:
    """
    Neon connection string. Raises rather than returning None: every
    entrypoint needs the database, so a missing value is a setup error to
    surface loudly, not a condition to handle.
    """
    try:
        return os.environ["DATABASE_URL"]
    except KeyError:
        raise RuntimeError(
            "DATABASE_URL is not set. Put it in the repo-root .env for local runs, "
            "or set the DATABASE_URL secret for GitHub Actions."
        ) from None


def telegram_credentials() -> tuple[str, str]:
    """``(bot_token, chat_id)``. Both empty strings when alerts are unconfigured."""
    return (
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
    )
