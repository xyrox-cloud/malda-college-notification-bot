"""
Universal broadcast helper.

Every push-style send (notice broadcast, exception broadcast, future
"class starting in 10 min" reminders) goes through `broadcast_text()`.

Behaviour:
  - Loop over all subscribers with notificationsEnabled == True.
  - Send to each chat_id individually (no shared group).
  - Sleep BROADCAST_DELAY_SEC between sends to stay under Telegram's ~30 msg/sec limit.
  - On 403 ("bot was blocked by the user"), auto-disable that user's
    notifications so we don't retry forever (self-cleaning subscriber list).
  - Log a summary "Sent to X/Y subscribers" after each cycle.
"""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

import config
import storage


async def _send_one(bot: Bot, chat_id: int, text: str, parse_mode: str = "HTML",
                    disable_preview: bool = True) -> bool:
    """
    Send to a single chat. Returns True on success, False on (logged) failure.
    Handles:
      - 403 Forbidden (user blocked the bot) -> auto-disable notifications
      - 429 RetryAfter -> sleep the requested delay, then retry once
    """
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_preview,
        )
        return True
    except TelegramRetryAfter as exc:
        config.logger.warning(
            "Rate-limited sending to %s; retrying after %ds", chat_id, exc.retry_after
        )
        await asyncio.sleep(exc.retry_after + 1)
        try:
            await bot.send_message(
                chat_id=chat_id, text=text, parse_mode=parse_mode,
                disable_web_page_preview=disable_preview,
            )
            return True
        except Exception as exc2:  # noqa: BLE001
            config.logger.error("Retry failed for %s: %s", chat_id, exc2)
            return False
    except TelegramForbiddenError:
        config.logger.info(
            "User %s blocked the bot — disabling notifications automatically.", chat_id
        )
        storage.set_notification_pref(chat_id, False)
        return False
    except Exception as exc:  # noqa: BLE001
        config.logger.error("Broadcast send to %s failed: %s", chat_id, exc)
        return False


async def broadcast_text(bot: Bot, text: str, parse_mode: str = "HTML",
                         disable_preview: bool = True) -> dict:
    """
    Send `text` to every subscriber with notifications on.
    Returns {"sent": int, "total": int}.
    """
    targets = storage.subscribers_with_notifications()
    total = len(targets)
    sent = 0
    for chat_id in targets:
        ok = await _send_one(bot, chat_id, text, parse_mode, disable_preview)
        if ok:
            sent += 1
        if config.BROADCAST_DELAY_SEC > 0:
            await asyncio.sleep(config.BROADCAST_DELAY_SEC)
    config.logger.info("Broadcast: sent to %d/%d subscribers", sent, total)
    return {"sent": sent, "total": total}


async def send_admin(bot: Bot, text: str, parse_mode: str = "HTML") -> None:
    """Convenience: send a message to every configured admin chat."""
    for admin_id in config.ADMIN_CHAT_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode=parse_mode)
        except Exception as exc:  # noqa: BLE001
            config.logger.error("Failed to message admin %s: %s", admin_id, exc)
