import os
import time

import requests


def _format_message(job: dict) -> str:
    title = job.get("title", "Unknown role")
    company = job.get("company", "Unknown company")
    score = job.get("match_score", 0)
    url = job.get("url", "")
    return (
        f"\U0001F525 New Match Found!\n\n"
        f"\U0001F4C4 {title}\n"
        f"\U0001F3E2 {company}\n"
        f"\U0001F4AF Match: {score:.1f}%\n\n"
        f"\U0001F517 Apply: {url}"
    )


def send_telegram(job: dict, token: str, chat_id: str) -> bool:
    """Send a job match via the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": _format_message(job)}
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()
    return response.json().get("ok", False)


def send_discord(job: dict, webhook_url: str) -> bool:
    """Send a job match via a Discord webhook."""
    response = requests.post(webhook_url, json={"content": _format_message(job)}, timeout=15)
    response.raise_for_status()
    return True


def send_notification(job: dict, rate_limit_sleep: float = 1.5) -> bool:
    """Route a job match to the configured notify channel.

    Parameters
    ----------
    job : dict
        Job record with at least ``title``, ``company``, ``match_score``, ``url``.
    rate_limit_sleep : float
        Seconds to sleep after a successful send.  Prevents Telegram/Discord
        429 (rate-limit) errors when many new jobs arrive in the same pipeline
        run.  Default is 1.5 s, which is well within Telegram's 30 msg/s limit
        for bots and Discord's webhook limit (5 requests / 2 s per webhook).
    """
    channel = os.getenv("NOTIFY_CHANNEL", "telegram").lower()

    if channel == "none":
        print(f"[notify] channel disabled; skipped -> {job.get('title')}")
        return True

    if channel == "telegram":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("ALLOWED_TELEGRAM_USER_ID", "")
        if not token or not chat_id:
            print("[notify] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing; skipping")
            return False
        if not send_telegram(job, token, chat_id):
            print("[notify] Telegram API rejected the message")
            return False

    elif channel == "discord":
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
        if not webhook_url:
            print("[notify] DISCORD_WEBHOOK_URL missing; skipping")
            return False
        send_discord(job, webhook_url)

    else:
        print(f"[notify] unknown NOTIFY_CHANNEL: {channel}; skipping")
        return False

    print(f"[notify] sent -> {job['title']} ({channel})")

    # Rate-limit guard: pause briefly between sends to stay within API limits.
    if rate_limit_sleep > 0:
        time.sleep(rate_limit_sleep)
    return True


if __name__ == "__main__":
    send_notification(
        {"title": "Python Backend Engineer", "company": "Acme",
         "match_score": 82.5, "url": "https://example.com/job"}
    )