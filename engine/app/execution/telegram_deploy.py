"""Telegram Deploy / Cancel buttons — the owner's approval tap until the Phase 9 dashboard exists.

A proposed signal is pushed as a card with two inline buttons. Tapping one sends a callback_query to
the bot; n8n's Telegram Trigger receives it and calls `/execute/deploy` or `/execute/cancel` here.

Two things make the button safe to expose:

  * The callback payload carries only the signal id and the action. It is not a command — the engine
    re-loads the signal, re-runs the full Guardian gate and re-checks the kill switch before anything
    is submitted. A stale or replayed tap cannot bypass a single check.
  * Only the configured `TELEGRAM_CHAT_ID` is honoured (`is_owner`). A message from any other chat is
    ignored and logged, so a leaked bot link cannot place an order.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


def is_owner(chat_id) -> bool:
    """Only the configured chat may act. Anything else is ignored, never obeyed."""
    expected = (get_settings().telegram_chat_id or "").strip()
    return bool(expected) and str(chat_id).strip() == expected


def _call(method: str, payload: dict) -> dict | None:
    s = get_settings()
    if not s.telegram_bot_token:
        log.info("telegram not configured; %s dropped", method)
        return None
    try:
        r = httpx.post(API.format(token=s.telegram_bot_token, method=method), json=payload, timeout=15)
        data = r.json()
        if not data.get("ok"):
            log.warning("telegram %s failed: %s", method, str(data)[:200])
        return data
    except Exception as e:  # noqa: BLE001
        log.warning("telegram %s error: %s", method, e)
        return None


def signal_card(signal: dict, strategy_code: str = "") -> str:
    ev = signal.get("evidence") or {}
    g = signal.get("guardian_precheck") or {}
    lines = [
        f"📋 {signal['symbol']} — {strategy_code} {signal.get('direction', '').upper()}",
        ev.get("in_play_sentence") or "",
        "",
        f"entry {signal.get('entry_price')} ({ev.get('entry_basis') or '—'})",
        f"stop  {signal.get('stop')} ({ev.get('stop_basis') or '—'})",
        f"target {signal.get('target')} ({ev.get('target_basis') or '—'})",
        f"size {signal.get('size')} · confidence {signal.get('confidence')}",
        "",
        f"pre-check: {g.get('verdict', '?')}"
        + (" (advisory)" if g.get("advisory") else ""),
        "",
        (signal.get("reasoning") or "")[:400],
        "",
        "The full Guardian gate runs again when you tap Deploy.",
    ]
    return "\n".join(x for x in lines if x is not None)


def push_signal(signal: dict, strategy_code: str = "") -> dict | None:
    """Send one signal card with Deploy / Cancel buttons."""
    s = get_settings()
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Deploy", "callback_data": f"deploy:{signal['id']}"},
        {"text": "✖️ Cancel", "callback_data": f"cancel:{signal['id']}"},
    ]]}
    return _call("sendMessage", {
        "chat_id": s.telegram_chat_id, "text": signal_card(signal, strategy_code),
        "reply_markup": keyboard, "disable_web_page_preview": True,
    })


def answer_callback(callback_query_id: str, text: str) -> None:
    """Clear the button's spinner with a short toast. Telegram caps this at 200 characters."""
    _call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text[:200],
                                  "show_alert": False})


def parse_callback(data: str) -> tuple[str, str]:
    """'deploy:<uuid>' → ('deploy', '<uuid>'). Raises ValueError on anything else."""
    action, _, signal_id = (data or "").partition(":")
    if action not in ("deploy", "cancel") or not signal_id:
        raise ValueError(f"unrecognised callback payload {data!r}")
    return action, signal_id


def edit_card(chat_id, message_id: int, text: str) -> None:
    """Replace the card after a decision so the buttons cannot be tapped twice."""
    _call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text[:4000],
                              "disable_web_page_preview": True})
