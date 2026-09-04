#!/usr/bin/env python3
"""
Put the existing TELEGRAM_CHAT_ID into telegram_subscribers.

The alerter now fans out to whoever is in that table instead of to the single
configured chat id, so without this the switch silently stops alerting the one
person who was already receiving alerts. Run it once, after applying the schema
and before the next daily ingest.

Idempotent — ON CONFLICT DO NOTHING, so a re-run never resurrects a chat that
has since unsubscribed.

Usage:
    python3 scripts/seed_telegram_subscriber.py
"""
from src.config import telegram_credentials
from src.db.connection import get_conn
from src.ingest.common import log


def main() -> int:
    _, chat_id = telegram_credentials()
    if not chat_id:
        log("TELEGRAM_CHAT_ID is not set — nothing to seed.")
        return 1

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO telegram_subscribers (chat_id, chat_type, title)
                VALUES (%s, 'private', 'seeded from TELEGRAM_CHAT_ID')
                ON CONFLICT (chat_id) DO NOTHING
                """,
                (int(chat_id),),
            )
            inserted = cur.rowcount

    if inserted:
        log(f"Seeded chat {chat_id} as an active subscriber.")
    else:
        log(f"Chat {chat_id} was already in telegram_subscribers — left untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
