"""
/help, /ping, /latest, and a fallback for unknown commands.
"""

from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

import config
import scraper

router = Router(name="misc")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    is_admin = config.is_admin(message.chat.id)
    admin_lines = ""
    if is_admin:
        admin_lines = (
            "\n\n<b>Admin commands:</b>\n"
            "/refreshdata - Re-fetch all sheet data now\n"
            "/setexception - Set an ad-hoc class-off day + broadcast\n"
            "/clearexception <i>DD Mon YYYY</i> - Remove an exception\n"
            "/listexceptions - View upcoming exceptions\n"
            "/showsuggest - View pending suggestions from users"
        )
    await message.answer(
        "<b>Available commands:</b>\n\n"
        "/start - Subscribe + register your semester + MJ/MN/MDC (or PG) subjects\n"
        "/myprofile - View your registration + notification status\n"
        "/mysubjects - View/change your MJ/MN/MDC (or PG) subjects\n"
        "/reregister - Change semester + subjects (keeps notifications)\n"
        "/notify - Toggle notifications ON/OFF\n"
        "/r - Today's routine\n"
        "/r today | /r now | /r next | /r <i>mon</i>\n"
        "/status - Bot uptime + subscriber count\n"
        "/latest - Show the most recent notice from the college website\n"
        "/suggest <i>text</i> - Suggest a change or setting to the admin\n"
        "/ping - Check bot latency\n"
        "/help - Show this message"
        + admin_lines
    )


@router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    start = time.monotonic()
    sent = await message.answer("\U0001F3D3 Pinging...")
    elapsed_ms = int((time.monotonic() - start) * 1000)
    await sent.edit_text(f"\U0001F3D3 Pong! {elapsed_ms} ms (Telegram API round-trip)")


@router.message(Command("latest"))
async def cmd_latest(message: Message) -> None:
    progress = await message.answer("\U0001F50D Fetching latest notice...")
    try:
        notices = await scraper.scrape_notices()
        if notices:
            top = notices[0]
            await progress.edit_text(
                "\U0001F4C4 <b>Latest Notice</b>\n\n"
                f"{top['title']}\n\n"
                f'\U0001F517 <a href="{top["link"]}">View Details</a>',
                disable_web_page_preview=False,
            )
        else:
            await progress.edit_text("No notices found on the page right now.")
    except Exception as exc:  # noqa: BLE001
        await progress.edit_text(f"\u26A0\uFE0F Failed to fetch notices: {exc}")


# Fallback for unrecognized slash commands (only catches /-prefixed text)
@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    await message.answer(
        "Unknown command. Send /help to see available commands."
    )
