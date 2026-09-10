"""Telegram notifications sent directly by the engine (plan §12, §33).

n8n stays thin: jobs report their own outcome. Never raises — a failed alert must not fail the job.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


def send_telegram(text: str) -> bool:
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        log.info("telegram not configured; message dropped: %s", text[:80])
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
            data={"chat_id": s.telegram_chat_id, "text": text[:4000], "disable_web_page_preview": True},
            timeout=10,
        )
        ok = r.status_code == 200 and r.json().get("ok") is True
        if not ok:
            log.warning("telegram send failed: %s %s", r.status_code, r.text[:200])
        return ok
    except Exception as e:  # noqa: BLE001
        log.warning("telegram send error: %s", e)
        return False
