"""
/suggest      — anyone can submit a suggestion/feedback for the bot or college
/showsuggest  — admin-only, lists pending suggestions with review/delete buttons

Flow:
  1. Any user sends /suggest <text>. It's stored in suggestions.json and the
     user gets a confirmation. Every admin (config.ADMIN_CHAT_IDS) is also
     notified immediately with the suggestion + inline "Mark reviewed" /
     "Delete" buttons, so admins don't have to remember to check /showsuggest.
  2. /showsuggest (admin-only) lists all still-pending suggestions on demand,
     each with the same review/delete buttons — useful for catching up on
     ones sent while the admin was offline.
  3. Buttons work from either surface (the live notification or /showsuggest)
     since they both encode the suggestion id in the callback_data.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import storage

router = Router(name="suggest")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _review_kb(suggestion_id: str) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="\u2705 Mark reviewed", callback_data=f"sugrev:{suggestion_id}"),
            InlineKeyboardButton(text="\U0001F5D1 Delete", callback_data=f"sugdel:{suggestion_id}"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_suggestion(rec: dict) -> str:
    who = rec.get("fullName") or "Unknown"
    uname = f" (@{rec['username']})" if rec.get("username") else ""
    return (
        f"\U0001F4A1 <b>Suggestion #{rec.get('id')}</b>\n"
        f"From: {who}{uname} [<code>{rec.get('chatId')}</code>]\n\n"
        f"{rec.get('text', '')}"
    )


# ---------------------------------------------------------------------------
# /suggest — everyone
# ---------------------------------------------------------------------------

@router.message(Command("suggest"))
async def cmd_suggest(message: Message, command: CommandObject) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "\U0001F4A1 <b>Suggest a change or setting</b>\n\n"
            "Usage: <code>/suggest your idea here</code>\n\n"
            "Example: <code>/suggest Please add /r for tomorrow's routine too</code>"
        )
        return

    user = message.from_user
    rec = storage.add_suggestion(
        chat_id=message.chat.id,
        text=text,
        username=user.username if user else None,
        full_name=user.full_name if user else None,
    )

    await message.answer(
        f"\u2705 Thanks! Your suggestion (#{rec['id']}) has been sent to the admin."
    )

    # Notify every admin immediately so they don't have to poll /showsuggest.
    for admin_id in config.ADMIN_CHAT_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                _format_suggestion(rec),
                reply_markup=_review_kb(rec["id"]),
            )
        except Exception as exc:  # noqa: BLE001
            config.logger.error("Failed to notify admin %s of suggestion: %s", admin_id, exc)


# ---------------------------------------------------------------------------
# /showsuggest — admin-only
# ---------------------------------------------------------------------------

@router.message(Command("showsuggest"))
async def cmd_showsuggest(message: Message) -> None:
    if not config.is_admin(message.chat.id):
        await message.answer("\U0001F6AB This command is admin-only.")
        return

    pending = storage.pending_suggestions()
    if not pending:
        await message.answer("\u2139\uFE0F No pending suggestions.")
        return

    await message.answer(f"\U0001F4CB <b>{len(pending)} pending suggestion(s):</b>")
    for rec in pending:
        await message.answer(_format_suggestion(rec), reply_markup=_review_kb(rec["id"]))


# ---------------------------------------------------------------------------
# Review / delete buttons — admin-only (checked inline; buttons only ever
# reach an admin's chat since they're sent there, but this guards against a
# forwarded/copied callback too).
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("sugrev:"))
async def on_mark_reviewed(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else 0
    if not config.is_admin(chat_id):
        await callback.answer("Admin only.", show_alert=True)
        return
    suggestion_id = callback.data.split(":", 1)[1]
    ok = storage.mark_suggestion_reviewed(suggestion_id)
    if ok and callback.message:
        await callback.message.edit_text(
            callback.message.html_text + "\n\n\u2705 <i>Marked reviewed.</i>",
            reply_markup=None,
        )
    await callback.answer("Marked reviewed." if ok else "Already gone.")


@router.callback_query(F.data.startswith("sugdel:"))
async def on_delete_suggestion(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id if callback.message else 0
    if not config.is_admin(chat_id):
        await callback.answer("Admin only.", show_alert=True)
        return
    suggestion_id = callback.data.split(":", 1)[1]
    ok = storage.delete_suggestion(suggestion_id)
    if ok and callback.message:
        await callback.message.edit_text(
            callback.message.html_text + "\n\n\U0001F5D1 <i>Deleted.</i>",
            reply_markup=None,
        )
    await callback.answer("Deleted." if ok else "Already gone.")
