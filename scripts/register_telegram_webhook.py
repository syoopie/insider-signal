#!/usr/bin/env python3
"""
Point the Telegram bot at the webhook in web/, or unhook it again.

Telegram delivers updates to exactly one destination, chosen by whoever called
setWebhook last, so this is a manual operational script — same tier as
apply_schema.py. Nothing runs it automatically.

The secret is echoed back by Telegram in X-Telegram-Bot-Api-Secret-Token and is
the only thing the route authenticates on, so TELEGRAM_WEBHOOK_SECRET must match
the value set in Vercel.

Usage:
    python3 scripts/register_telegram_webhook.py --url https://<domain>/api/telegram/webhook
    python3 scripts/register_telegram_webhook.py --delete   # back to getUpdates for local debugging
"""
import argparse
import os

import requests

from src.config import telegram_credentials
from src.ingest.common import log

TELEGRAM_API = "https://api.telegram.org"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="full webhook URL, e.g. https://<domain>/api/telegram/webhook")
    ap.add_argument("--delete", action="store_true",
                    help="call deleteWebhook instead, falling back to getUpdates")
    args = ap.parse_args()

    if not args.delete and not args.url:
        ap.error("--url is required unless --delete is given")

    token, _ = telegram_credentials()
    if not token:
        log("TELEGRAM_BOT_TOKEN is not set.")
        return 1

    if args.delete:
        resp = requests.post(f"{TELEGRAM_API}/bot{token}/deleteWebhook", timeout=15)
    else:
        secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        if not secret:
            log("TELEGRAM_WEBHOOK_SECRET is not set — the route would reject every update.")
            return 1
        resp = requests.post(
            f"{TELEGRAM_API}/bot{token}/setWebhook",
            json={
                "url": args.url,
                "secret_token": secret,
                "allowed_updates": ["message", "my_chat_member"],
            },
            timeout=15,
        )

    log(f"HTTP {resp.status_code}: {resp.text}")
    return 0 if resp.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
