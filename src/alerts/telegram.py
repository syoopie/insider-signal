"""
Telegram alert sender.

Two functions:
  send_signal(evidence_dict)  — formatted buy/watch signal with full evidence
  send_error(error_msg)       — ⚠️ pipeline failure notification

Both are fire-and-forget: log on failure but never raise (alerts must not
crash the ingest pipeline).
"""

import os
import requests
from datetime import date
from typing import Tuple


TELEGRAM_API = "https://api.telegram.org"


def _get_credentials() -> Tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    return token, chat_id


def _send(text: str) -> bool:
    token, chat_id = _get_credentials()
    if not token or not chat_id:
        print("Telegram not configured — skipping alert")
        return False
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False


def send_signal(evidence: dict) -> bool:
    from src.signals.formatter import format_telegram_message
    msg = format_telegram_message(evidence)
    return _send(msg)


def send_error(error: Exception | str, context: str = "daily ingest") -> bool:
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
