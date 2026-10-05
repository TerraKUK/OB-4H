"""Small Telegram client used by GitHub Actions workflows."""

from __future__ import annotations

import os

def send_message(text: str, dry_run: bool = False) -> bool:
    if dry_run:
        print(f"DRY RUN TELEGRAM:\n{text}")
        return True
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be configured")
    # Import only for real delivery so DRY_RUN can be executed with the
    # detector's lightweight local dependencies.
    import requests

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        try:
            description = response.json().get("description", response.text)
        except ValueError:
            description = response.text
        raise RuntimeError(f"Telegram sendMessage failed: {description}") from error
    return True


def send_digest(items, formatter, dry_run=False):
    """Deliver every entry, keeping each message below Telegram's size limit."""
    batch = []
    for item in items:
        candidate = batch + [item]
        if len(formatter(candidate).encode("utf-16-le")) // 2 > 4000:
            if not batch:
                raise ValueError("A single digest entry exceeds Telegram's message limit")
            send_message(formatter(batch), dry_run)
            batch = [item]
            if len(formatter(batch).encode("utf-16-le")) // 2 > 4000:
                raise ValueError("A single digest entry exceeds Telegram's message limit")
        else:
            batch = candidate
    if batch:
        send_message(formatter(batch), dry_run)
