"""
/help, /ping, /latest, and a fallback for unknown commands.
"""

from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

import config
import scraper
from text_style import bold_italic

router = Router(name="misc")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    is_admin = config.is_admin(message.chat.id)
    admin_lines = ""
    if is_admin:
        admin_lines = (
            "\n\n<b>Admin commands:</b>\n"
            "/status - Bot uptime + subscriber count\n"
            "/refreshdata - Re-fetch all sheet data now\n"
            "/setexception - Set an ad-hoc class-off day (or range) + broadcast\n"
            "/clearexception <i>DD Mon YYYY [to DD Mon YYYY]</i> - Remove an exception\n"
            "/listexceptions - View upcoming exceptions\n"
            "/showsuggest - View pending suggestions from users\n"
            "/users - List every registered user"
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
        "/latest - Show the most recent notice from the college website\n"
        "/notification - Show your last 5 received notifications\n"
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
        if not notices:
            await progress.edit_text("No notices found on the page right now.")
            return

        top = notices[0]
        header = bold_italic("Latest Notice")
        caption = (
            f"\U0001F4C4 {header}\n\n"
            f"{top['title']}\n\n"
            f'\U0001F587\uFE0F <a href="{top["link"]}">Original link</a>'
        )

        file_info = await scraper.download_notice_file(top["link"])
        if file_info is not None:
            content, filename, mime = file_info
            try:
                if mime.startswith("image/"):
                    await message.answer_photo(
                        BufferedInputFile(content, filename=filename), caption=caption,
                    )
                else:
                    await message.answer_document(
                        BufferedInputFile(content, filename=filename), caption=caption,
                    )
                await progress.delete()
                return
            except Exception as exc:  # noqa: BLE001
                config.logger.error("Failed sending downloaded file for /latest: %s", exc)

        # Couldn't download the notice content (or sending it failed) —
        # fall back to the plain title + link message.
        await progress.edit_text(caption, disable_web_page_preview=False)
    except Exception as exc:  # noqa: BLE001
        await progress.edit_text(f"\u26A0\uFE0F Failed to fetch notices: {exc}")


# Fallback for unrecognized slash commands (only catches /-prefixed text)
@router.message(F.text.startswith("/"))
async def unknown_command(message: Message) -> None:
    await message.answer(
        "Unknown command. Send /help to see available commands."
    )
