#!/usr/bin/env python3
"""
Malda College Bot — main entry point (aiogram v3).

Wires up:
  - Dispatcher with all handler routers
  - Startup: load on-disk cache, refresh sheets from network, seed seen-notices
  - Background task: notice-board scraping loop (broadcasts new notices)
  - Background task: 24h auto-refresh of sheet data
  - Graceful shutdown

Environment variables (see .env.example):
  BOT_TOKEN, ADMIN_CHAT_ID, ODD_ROUTINE_URL, EVEN_ROUTINE_URL, CALENDAR_URL,
  INTERVAL, SLOT_DURATION_MIN, SHEET_REFRESH_HOURS, BROADCAST_DELAY_SEC
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import broadcast
import config
import scraper
import sheets
import storage
from handlers import (
    admin_router,
    misc_router,
    notify_router,
    profile_router,
    routine_router,
    start_router,
    status_router,
    suggest_router,
)

logger = logging.getLogger("malda_bot.main")


# ---------------------------------------------------------------------------
# Notice-scraping loop (replaces the old `while True` in the original bot)
# ---------------------------------------------------------------------------

async def notice_scrape_loop(bot: Bot) -> None:
    """
    Periodically scrape the college notice board and broadcast any new notices
    to all subscribers with notifications enabled.
    """
    # Import here to mutate the module-level variables that /status reads
    import handlers.status as status_mod

    # First run: seed seen_notices without broadcasting (so we don't spam old notices)
    seen = storage.load_seen()
    if not seen:
        logger.info("First run detected — seeding seen-notices without broadcasting.")
        try:
            initial = await scraper.scrape_notices()
            for n in initial:
                seen.add(n["title"])
            storage.save_seen(seen)
            logger.info("Seeded %d existing notices. No messages sent.", len(seen))
        except Exception as exc:  # noqa: BLE001
            logger.error("Error during first-run seeding: %s", exc)

    while True:
        try:
            notices = await scraper.scrape_notices()
            status_mod.LAST_CHECK_TIME = datetime.now(timezone.utc)
            status_mod.LAST_SCRAPE_COUNT = len(notices)
            new_notices = [n for n in notices if n["title"] not in seen]
            if new_notices:
                # One shared session for downloading all the slide images/PDFs
                # in this cycle, instead of opening a new connection per notice.
                async with aiohttp.ClientSession() as dl_session:
                    for notice in new_notices:
                        caption = (
                            "\U0001F514 <b>New Notice — Malda College</b>\n\n"
                            f"{notice['title']}\n\n"
                            f'\U0001F517 <a href="{notice["link"]}">Original link</a>'
                        )
                        file_info = await scraper.download_notice_file(
                            notice["link"], session=dl_session
                        )

                        if file_info is not None:
                            content, filename, mime = file_info
                            try:
                                if mime.startswith("image/"):
                                    await broadcast.broadcast_photo(
                                        bot, content, filename, caption
                                    )
                                else:
                                    await broadcast.broadcast_document(
                                        bot, content, filename, caption
                                    )
                                seen.add(notice["title"])
                                storage.save_seen(seen)
                                logger.info(
                                    "Broadcast notice with downloaded file (%s): %s",
                                    mime, notice["title"],
                                )
                                continue
                            except Exception as exc:  # noqa: BLE001
                                logger.error(
                                    "Failed broadcasting downloaded file for %r, "
                                    "falling back to text: %s", notice["title"], exc,
                                )

                        # Couldn't download the notice content (or sending it
                        # failed) — fall back to the old title + link message
                        # so subscribers still get notified.
                        text = (
                            "\U0001F514 <b>New Notice — Malda College</b>\n\n"
                            f"{notice['title']}\n\n"
                            f'\U0001F517 <a href="{notice["link"]}">View Details</a>'
                        )
                        await broadcast.broadcast_text(bot, text, disable_preview=False)
                        seen.add(notice["title"])
                        storage.save_seen(seen)
                logger.info("Broadcast %d new notice(s).", len(new_notices))
            else:
                logger.debug("No new notices this cycle.")
        except asyncio.CancelledError:
            logger.info("Notice-scrape loop cancelled.")
            break
        except Exception as exc:  # noqa: BLE001
            logger.error("Error in notice-scrape cycle: %s", exc, exc_info=True)
        await asyncio.sleep(config.POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

async def on_startup(bot: Bot) -> None:
    logger.info("Starting Malda College Bot (aiogram v3)...")
    problems = config.validate_startup()
    for p in problems:
        logger.warning(p)

    # 1) Load on-disk cache so the bot has data even before network refresh
    sheets.init_from_disk_or_empty()

    # 2) Refresh from network (best-effort, time-bounded so a slow/hung URL
    #    doesn't block polling startup).
    try:
        await asyncio.wait_for(sheets.refresh_all(), timeout=90)
    except asyncio.TimeoutError:
        logger.error("Initial sheet refresh timed out after 90s — continuing with on-disk cache.")
    except Exception as exc:  # noqa: BLE001
        logger.error("Initial sheet refresh failed: %s", exc)

    # 3) Prune past exceptions
    pruned = storage.prune_past_exceptions()
    if pruned:
        logger.info("Pruned %d past exception(s) on startup.", pruned)

    # 4) Notify admin that the bot is up
    if config.ADMIN_CHAT_IDS:
        try:
            await broadcast.send_admin(
                bot,
                "\u2705 <b>Malda College Bot is online.</b>\n\n"
                f"Cached: odd={len(sheets.CACHE.odd_routine)}, "
                f"even={len(sheets.CACHE.even_routine)}, "
                f"calendar={len(sheets.CACHE.calendar)} rows.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to notify admin on startup: %s", exc)


# Background task references — populated by _post_startup, cancelled by on_shutdown.
_background_tasks: list[asyncio.Task] = []


async def on_shutdown(bot: Bot) -> None:
    logger.info("Shutting down...")
    # Cancel background tasks so they don't outlive the bot session.
    for task in _background_tasks:
        if not task.done():
            task.cancel()
    for task in _background_tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _background_tasks.clear()
    try:
        await broadcast.send_admin(bot, "\U0001F6D1 Malda College Bot is going offline.")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    # Order matters: admin router first so admin commands aren't caught by
    # the unknown-command fallback in misc_router.
    dp.include_router(admin_router)
    dp.include_router(start_router)
    dp.include_router(profile_router)
    dp.include_router(notify_router)
    dp.include_router(routine_router)
    dp.include_router(status_router)
    dp.include_router(suggest_router)
    dp.include_router(misc_router)
    return dp


async def main() -> None:
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Exiting.")
        raise SystemExit(1)

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Launch background tasks once polling starts. Keep references so we can
    # cancel them cleanly on shutdown.
    async def _post_startup():
        _background_tasks.append(asyncio.create_task(notice_scrape_loop(bot)))
        _background_tasks.append(asyncio.create_task(sheets.auto_refresh_loop()))
        logger.info("Background tasks launched: notice-scrape + sheet auto-refresh.")

    dp.startup.register(_post_startup)

    # aiogram's polling is the main event loop; everything else runs as tasks
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    import sys
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(0)
