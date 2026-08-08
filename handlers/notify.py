"""
/notify (aliases /notifications, /togglenotify) — inline ON/OFF toggle.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import storage
import scraper

router = Router(name="notify")


def _notify_keyboard(current: bool) -> InlineKeyboardMarkup:
    on_label = "\U0001F514 Turn ON" if not current else "\u2705 ON (active)"
    off_label = "\U0001F515 Turn OFF" if current else "\u274C OFF (active)"
    buttons = [
        [
            InlineKeyboardButton(text=on_label, callback_data="notify:on"),
            InlineKeyboardButton(text=off_label, callback_data="notify:off"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("notification"))
async def cmd_notification(message: Message) -> None:
    progress = await message.answer("\U0001F50D Fetching notifications...")
    try:
        notices = await scraper.scrape_notices()
        if not notices:
            await progress.edit_text("No notifications found right now.")
            return

        recent_notices = notices[:5]
        lines = ["\U0001F514 <b>Recent Notifications</b>\n"]
        for n in recent_notices:
            lines.append(f"\u2022 <a href='{n['link']}'>{n['title']}</a>")

        await progress.edit_text("\n\n".join(lines), disable_web_page_preview=True)
    except Exception as exc:
        await progress.edit_text(f"\u26A0\uFE0F Failed to fetch notifications: {exc}")


@router.message(Command("notify", "notifications", "togglenotify"))
async def cmd_notify(message: Message) -> None:
    user = storage.get_user(message.chat.id)
    if not user:
        # Auto-subscribe so the user can still toggle, but they need registration for /r
        storage.upsert_user(message.chat.id, notificationsEnabled=True)
        user = storage.get_user(message.chat.id)
    current = bool(user and user.get("notificationsEnabled"))
    status_emoji = "\U0001F514" if current else "\U0001F515"
    await message.answer(
        f"{status_emoji} <b>Notifications: {'ON' if current else 'OFF'}</b>\n\n"
        f"You will {'receive' if current else 'NOT receive'} new-notice and class-off alerts.\n"
        f"Use the buttons below to change:",
        reply_markup=_notify_keyboard(current),
    )


@router.callback_query(F.data.in_({"notify:on", "notify:off"}))
async def on_notify_toggle(callback: CallbackQuery) -> None:
    new_val = callback.data == "notify:on"
    storage.set_notification_pref(callback.message.chat.id, new_val)
    status_emoji = "\U0001F514" if new_val else "\U0001F515"
    await callback.message.edit_text(
        f"{status_emoji} <b>Notifications: {'ON' if new_val else 'OFF'}</b>\n\n"
        f"You will {'receive' if new_val else 'NOT receive'} new-notice and class-off alerts.\n"
        f"Use the buttons below to change:",
        reply_markup=_notify_keyboard(new_val),
    )
    await callback.answer(f"Notifications {'enabled' if new_val else 'disabled'}.")
