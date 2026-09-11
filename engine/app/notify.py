"""Telegram notifications sent directly by the engine (plan §12, §33).

n8n stays thin: jobs report their own outcome. Never raises — a failed alert must not fail the job.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.ops import resilience

log = logging.getLogger(__name__)


def send_telegram(text: str) -> bool:
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        log.info("telegram not configured; message dropped: %s", text[:80])
        return False
    def _post():
        r = httpx.post(
            f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
            data={"chat_id": s.telegram_chat_id, "text": text[:4000], "disable_web_page_preview": True},
            timeout=10,
        )
        r.raise_for_status()
        return r

    try:
        # Telegram answers 429 with a Retry-After when a burst of alerts arrives at once; `resilience`
        # honours that header rather than guessing. An alert worth sending is worth three attempts.
        r = resilience.call("telegram", _post, what="sendMessage")
        ok = r.status_code == 200 and r.json().get("ok") is True
        if not ok:
            log.warning("telegram send failed: %s %s", r.status_code, r.text[:200])
        return ok
    except Exception as e:  # noqa: BLE001
        log.warning("telegram send error: %s", e)
        return False
