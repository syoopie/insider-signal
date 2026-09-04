"""
Telegram alert sender.

Two functions:
  send_signal(evidence_dict)  — formatted buy/watch signal with full evidence
  send_error(error_msg)       — ⚠️ pipeline failure notification

Both are fire-and-forget: log on failure but never raise (alerts must not
crash the ingest pipeline).
"""

import requests
from datetime import date
from psycopg2.extras import RealDictCursor

from src.config import telegram_credentials
from src.db.connection import get_conn
from src.db.store import get_active_telegram_subscribers


TELEGRAM_API = "https://api.telegram.org"


def _send(text: str) -> bool:
    """
    Fan one message out to every active subscriber.

    Recipients come from the telegram_subscribers table, not from
    TELEGRAM_CHAT_ID: people subscribe by messaging the bot or adding it to a
    group, and the webhook in web/ maintains the list. The chat_id half of
    telegram_credentials() is now only read by scripts/seed_telegram_subscriber.py.

    One failing recipient must not silence the rest — a person who blocked the
    bot makes its own send raise, and everyone after them still gets the alert.
    """
    token, _ = telegram_credentials()
    if not token:
        print("Telegram not configured — skipping alert")
        return False

    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            chat_ids = get_active_telegram_subscribers(cur)

    if not chat_ids:
        print("No active Telegram subscribers — skipping alert")
        return False

    sent = 0
    for chat_id in chat_ids:
        try:
            resp = requests.post(
                f"{TELEGRAM_API}/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=15,
            )
            resp.raise_for_status()
            sent += 1
        except Exception as e:
            print(f"Telegram send to {chat_id} failed: {e}")
    return sent > 0


def send_signal(evidence: dict) -> bool:
    from src.signals.formatter import format_telegram_message
    msg = format_telegram_message(evidence)
    return _send(msg)


def send_error(error, context: str = "daily ingest") -> bool:
    today = date.today().isoformat()
    msg = f"⚠️ <b>Pipeline failure</b> [{today}]\n\n<b>Job:</b> {context}\n<b>Error:</b> {str(error)}"
    return _send(msg)


def send_daily_summary(n_signals: int, n_buy: int, n_cluster: int, n_watch: int) -> bool:
    today = date.today().isoformat()
    if n_signals == 0:
        msg = f"📊 <b>Daily Ingest Complete</b> [{today}]\n\nNo new signals today."
    else:
        msg = (
            f"📊 <b>Daily Ingest Complete</b> [{today}]\n\n"
            f"New signals: {n_signals}\n"
            f"  🔴 CLUSTER_BUY: {n_cluster}\n"
            f"  🟢 BUY:         {n_buy}\n"
            f"  🟡 WATCH:       {n_watch}"
        )
    return _send(msg)
