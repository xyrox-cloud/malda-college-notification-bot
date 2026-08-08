"""
/status — bot uptime, last scrape, subscriber count, notify-ON count.
"""

from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import config
import storage

router = Router(name="status")

BOT_START_TIME = datetime.now(timezone.utc)

# These are updated by the notice-scraping loop in malda_bot.py
LAST_CHECK_TIME = None  # type: ignore
LAST_SCRAPE_COUNT = 0  # type: ignore


def _format_uptime(delta_seconds: float) -> str:
    mins, secs = divmod(int(delta_seconds), 60)
    hours, mins = divmod(mins, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{mins}m")
    return " ".join(parts)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not config.is_admin(message.chat.id):
        await message.answer("You are not authorized to use this command.")
        return
    uptime = _format_uptime((datetime.now(timezone.utc) - BOT_START_TIME).total_seconds())
    total, on = storage.subscriber_count()
    last_check = (
        LAST_CHECK_TIME.strftime("%Y-%m-%d %H:%M:%S UTC")
        if LAST_CHECK_TIME
        else "not yet run"
    )
    from sheets import CACHE
    sheet_info = (
        f"odd={len(CACHE.odd_routine)}, even={len(CACHE.even_routine)}, "
        f"calendar={len(CACHE.calendar)}"
    )
    await message.answer(
        "\u2705 <b>Bot Status</b>\n\n"
        f"Uptime: {uptime}\n"
        f"Last notice check: {last_check}\n"
        f"Last scrape count: {LAST_SCRAPE_COUNT} notice(s)\n"
        f"Poll interval: {config.POLL_INTERVAL}s\n"
        f"Cached sheet rows: {sheet_info}\n"
        f"Subscribers: {total} total / {on} with notifications ON"
    )
