"""
/myprofile, /mysubjects, and /reregister.

  /myprofile   — show the user's registered sem/track/subjects + notification status.
  /mysubjects  — show just the MJ/MN/MDC (or PG) subjects, with a button to change them.
  /reregister  — restart the full registration flow (keeps notification pref).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import storage
from handlers.start import _show_semester_keyboard, start_subject_selection

router = Router(name="profile")


def _subjects_lines(user: dict) -> list[str]:
    subjects = user.get("subjects")
    track = user.get("track")
    if not subjects or not track:
        return ["Subjects: <b>not set yet</b> — send /mysubjects or /reregister"]
    lines = [f"Track: <b>{track}</b>"]
    if track == "UG":
        lines.append(f"Major (MJ): <b>{subjects.get('MJ', '—')}</b>")
        if subjects.get("MN"):
            lines.append(f"Minor (MN): <b>{subjects['MN']}</b>")
        if subjects.get("MDC"):
            lines.append(f"MDC: <b>{', '.join(subjects['MDC'])}</b>")
    else:
        # PG: subjects is {pg_tag: subject}
        for _tag, sub in subjects.items():
            lines.append(f"Subject: <b>{sub}</b>")
    return lines


@router.message(Command("myprofile"))
async def cmd_myprofile(message: Message) -> None:
    user = storage.get_user(message.chat.id)
    if not user:
        await message.answer(
            "\u26A0\uFE0F You're not registered yet. Send /start to begin."
        )
        return
    notif = "ON" if user.get("notificationsEnabled") else "OFF"
    lines = [
        "\U0001F464 <b>Your Profile</b>\n",
        f"Semester: <b>{user.get('sem', '—')}</b>",
    ]
    lines.extend(_subjects_lines(user))
    lines.append(f"Notifications: <b>{notif}</b>")
    lines.append(f"Registered at: {user.get('registeredAt', '—')}")
    await message.answer("\n".join(lines))


@router.message(Command("mysubjects"))
async def cmd_mysubjects(message: Message) -> None:
    user = storage.get_user(message.chat.id)
    if not user or not user.get("sem"):
        await message.answer(
            "\u26A0\uFE0F You're not registered yet. Send /start to begin."
        )
        return

    lines = ["\U0001F4DA <b>Your Subjects</b>\n", f"Semester: <b>{user.get('sem')}</b>"]
    lines.extend(_subjects_lines(user))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Change subjects", callback_data="mysubjects:change")]
        ]
    )
    await message.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(F.data == "mysubjects:change")
async def on_change_subjects(callback: CallbackQuery, state: FSMContext) -> None:
    user = storage.get_user(callback.message.chat.id)
    if not user or not user.get("sem"):
        await callback.message.edit_text(
            "\u26A0\uFE0F You're not registered yet. Send /start to begin."
        )
        await callback.answer()
        return

    sem = user.get("sem")
    track = user.get("track")
    if not track:
        # Old/incomplete registration with no track on file — fall back to
        # a full /reregister so semester gets (re)confirmed too.
        await callback.message.edit_text(
            "Your semester is on file, but I need to redo semester + subjects "
            "to set this up cleanly. Send /reregister."
        )
        await callback.answer()
        return

    await start_subject_selection(callback.message, state, sem, track)
    await callback.answer()


@router.message(Command("reregister"))
async def cmd_reregister(message: Message, state: FSMContext) -> None:
    user = storage.get_user(message.chat.id)
    if not user:
        # If somehow not registered, just go through /start flow
        await _show_semester_keyboard(message, state)
        return
    # Don't touch notificationsEnabled — only re-collect sem/track/subjects.
    await state.clear()
    await _show_semester_keyboard(message, state)
