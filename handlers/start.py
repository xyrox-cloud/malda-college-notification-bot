"""
/start — subscribe + registration flow (Semester -> Track -> Subjects).

Flow:
  1. /start -> register chatId as subscriber (notificationsEnabled=True by
     default, but preserved if user is returning). Show semester buttons.
  2. User picks sem -> if the semester offers both UG (MJ/MN/MDC/...) and PG
     (PG1/PG2/PG4/...) papers, ask which track; otherwise the track is
     inferred automatically.
  3a. UG track: pick one MJ subject -> one MN subject (if the sem has an MN
      paper) -> one or more MDC subjects (if the sem has an MDC paper, via a
      multi-select toggle keyboard).
  3b. PG track: pick one PG subject.
  4. Save the full registration (sem, track, subjects) and confirm.

Tutorial/Practical/SEC/IAPC/REM/VEC papers are NOT selected separately —
they automatically follow the student's MJ (or MN, for PRT-MN) subject.
See sheets.classes_for_subjects() for the exact tie-mapping.

If returning user sends /start again, we DON'T wipe notificationsEnabled —
the FSM re-collects sem/track/subjects but keeps the notification preference.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import storage
from sheets import CACHE

router = Router(name="start")


class Registration(StatesGroup):
    waiting_for_sem = State()
    waiting_for_track = State()
    waiting_for_mj = State()
    waiting_for_mn = State()
    waiting_for_mdc = State()
    waiting_for_pg_subject = State()


# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------

def _split_categories(sem: str) -> tuple[list[str], list[str]]:
    """Return (ug_tags, pg_tags) for a semester's cached routine data."""
    cats = CACHE.available_categories(sem)
    pg_tags = [c for c in cats if c.upper().startswith("PG")]
    ug_tags = [c for c in cats if not c.upper().startswith("PG")]
    return ug_tags, pg_tags


def _checkbox_kb(all_subjects: list[str], selected: list[str], prefix: str) -> InlineKeyboardMarkup:
    buttons = []
    for s in all_subjects:
        mark = "\u2705" if s in selected else "\u2610"
        buttons.append([InlineKeyboardButton(text=f"{mark} {s}", callback_data=f"{prefix}:{s}")])
    buttons.append([InlineKeyboardButton(text="Done \u2705", callback_data=f"{prefix}Done")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _single_select_kb(subjects: list[str], prefix: str, back_cb: str) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=s, callback_data=f"{prefix}:{s}")] for s in subjects]
    buttons.append([InlineKeyboardButton(text="\u2190 Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    chat_id = message.chat.id
    # Subscribe the user (preserve existing notification preference if returning)
    existing = storage.get_user(chat_id)
    if existing:
        storage.upsert_user(
            chat_id,
            notificationsEnabled=existing.get("notificationsEnabled", True),
        )
    else:
        storage.upsert_user(chat_id, notificationsEnabled=True)

    await state.clear()
    await _show_semester_keyboard(message, state)


async def _show_semester_keyboard(message: Message, state: FSMContext) -> None:
    # Offer all 7 semesters — the bot picks odd/even sheet based on parity.
    buttons = [
        [InlineKeyboardButton(text=str(s), callback_data=f"sem:{s}")]
        for s in range(1, 8)
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await state.set_state(Registration.waiting_for_sem)
    await message.answer(
        "\U0001F44B <b>Welcome to Malda College Bot!</b>\n\n"
        "You're now subscribed — you'll receive new notices and class-off alerts automatically.\n\n"
        "To use the routine feature, please pick your <b>semester</b>:",
        reply_markup=kb,
    )


# ---------------------------------------------------------------------------
# Step 2 — semester picked -> track (if needed) or straight into subjects
# ---------------------------------------------------------------------------

@router.callback_query(Registration.waiting_for_sem, F.data.startswith("sem:"))
async def on_sem_picked(callback: CallbackQuery, state: FSMContext) -> None:
    sem = callback.data.split(":", 1)[1]
    await state.update_data(sem=sem, mdc_selected=[])

    ug_tags, pg_tags = _split_categories(sem)
    if not ug_tags and not pg_tags:
        await callback.message.edit_text(
            f"\u26A0\uFE0F No routine data cached for semester {sem} yet.\n"
            f"Ask an admin to run /refreshdata, or try /reregister later."
        )
        await callback.answer()
        await state.clear()
        return

    if ug_tags and pg_tags:
        buttons = [
            [InlineKeyboardButton(text="\U0001F393 Undergraduate (UG)", callback_data="track:UG")],
            [InlineKeyboardButton(text="\U0001F393 Postgraduate (PG)", callback_data="track:PG")],
            [InlineKeyboardButton(text="\u2190 Back", callback_data="back:sem")],
        ]
        await state.set_state(Registration.waiting_for_track)
        await callback.message.edit_text(
            f"Semester <b>{sem}</b> selected.\n\nAre you Undergraduate or Postgraduate?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()
        return

    track = "UG" if ug_tags else "PG"
    await state.update_data(track=track)
    if track == "UG":
        await _show_mj_keyboard(callback.message, state, sem)
    else:
        await _show_pg_keyboard(callback.message, state, sem, pg_tags)
    await callback.answer()


@router.callback_query(Registration.waiting_for_track, F.data.startswith("track:"))
async def on_track_picked(callback: CallbackQuery, state: FSMContext) -> None:
    track = callback.data.split(":", 1)[1]
    data = await state.get_data()
    sem = data.get("sem")
    await state.update_data(track=track)
    if track == "UG":
        await _show_mj_keyboard(callback.message, state, sem)
    else:
        _, pg_tags = _split_categories(sem)
        await _show_pg_keyboard(callback.message, state, sem, pg_tags)
    await callback.answer()


# ---------------------------------------------------------------------------
# UG — Step 3a: MJ (single-select)
# ---------------------------------------------------------------------------

async def _show_mj_keyboard(message: Message, state: FSMContext, sem: str) -> None:
    subjects = CACHE.available_subjects(sem, "MJ")
    if not subjects:
        await message.edit_text(
            f"\u26A0\uFE0F No Major (MJ) subjects found for semester {sem}."
        )
        await state.clear()
        return
    kb = _single_select_kb(subjects, "mj", "back:track")
    await state.set_state(Registration.waiting_for_mj)
    await message.edit_text(
        f"Semester <b>{sem}</b>.\n\nPick your <b>Major (MJ)</b> subject:",
        reply_markup=kb,
    )


@router.callback_query(Registration.waiting_for_mj, F.data.startswith("mj:"))
async def on_mj_picked(callback: CallbackQuery, state: FSMContext) -> None:
    mj = callback.data.split(":", 1)[1]
    data = await state.get_data()
    sem = data.get("sem")
    await state.update_data(mj=mj)

    if "MN" in CACHE.available_categories(sem):
        await _show_mn_keyboard(callback.message, state, sem)
    else:
        await _show_mdc_keyboard(callback.message, state, sem)
    await callback.answer()


# ---------------------------------------------------------------------------
# UG — Step 3b: MN (single-select, optional)
# ---------------------------------------------------------------------------

async def _show_mn_keyboard(message: Message, state: FSMContext, sem: str) -> None:
    subjects = CACHE.available_subjects(sem, "MN")
    if not subjects:
        await _show_mdc_keyboard(message, state, sem)
        return
    kb = _single_select_kb(subjects, "mn", "back:mj")
    await state.set_state(Registration.waiting_for_mn)
    await message.edit_text(
        f"Semester <b>{sem}</b>.\n\nPick your <b>Minor (MN)</b> subject:",
        reply_markup=kb,
    )


@router.callback_query(Registration.waiting_for_mn, F.data.startswith("mn:"))
async def on_mn_picked(callback: CallbackQuery, state: FSMContext) -> None:
    mn = callback.data.split(":", 1)[1]
    data = await state.get_data()
    sem = data.get("sem")
    await state.update_data(mn=mn)
    await _show_mdc_keyboard(callback.message, state, sem)
    await callback.answer()


# ---------------------------------------------------------------------------
# UG — Step 3c: MDC (multi-select toggle, optional)
# ---------------------------------------------------------------------------

async def _show_mdc_keyboard(message: Message, state: FSMContext, sem: str) -> None:
    subjects = CACHE.available_subjects(sem, "MDC")
    if not subjects:
        await _finalize_ug(message, state)
        return
    data = await state.get_data()
    selected = data.get("mdc_selected", [])
    kb = _checkbox_kb(subjects, selected, "mdc")
    await state.set_state(Registration.waiting_for_mdc)
    await message.edit_text(
        f"Semester <b>{sem}</b>.\n\n"
        f"Pick one or more <b>MDC</b> subjects (tap to toggle), then <b>Done</b>:",
        reply_markup=kb,
    )


@router.callback_query(Registration.waiting_for_mdc, F.data.startswith("mdc:"))
async def on_mdc_toggled(callback: CallbackQuery, state: FSMContext) -> None:
    subject = callback.data.split(":", 1)[1]
    data = await state.get_data()
    sem = data.get("sem")
    selected = list(data.get("mdc_selected", []))
    if subject in selected:
        selected.remove(subject)
    else:
        selected.append(subject)
    await state.update_data(mdc_selected=selected)

    subjects = CACHE.available_subjects(sem, "MDC")
    kb = _checkbox_kb(subjects, selected, "mdc")
    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()


@router.callback_query(Registration.waiting_for_mdc, F.data == "mdcDone")
async def on_mdc_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = data.get("mdc_selected", [])
    if not selected:
        await callback.answer("Pick at least one MDC subject first.", show_alert=True)
        return
    await _finalize_ug(callback.message, state)
    await callback.answer()


async def _finalize_ug(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sem = data.get("sem")
    mj = data.get("mj")
    mn = data.get("mn")
    mdc = data.get("mdc_selected", [])
    chat_id = message.chat.id

    subjects: dict = {"MJ": mj}
    if mn:
        subjects["MN"] = mn
    if mdc:
        subjects["MDC"] = mdc

    storage.upsert_user(
        chat_id,
        sem=sem,
        track="UG",
        subjects=subjects,
        subjectsHintShown=False,
    )
    # Clean up the legacy single course/sub fields from any older registration.
    storage.upsert_user(chat_id, course=None, sub=None)
    await state.clear()

    lines = [
        "\u2705 <b>Registration complete!</b>\n",
        f"Semester: <b>{sem}</b>",
        f"Track: <b>UG</b>",
        f"Major (MJ): <b>{mj}</b>",
    ]
    if mn:
        lines.append(f"Minor (MN): <b>{mn}</b>")
    if mdc:
        lines.append(f"MDC: <b>{', '.join(mdc)}</b>")
    notif = "ON" if (storage.get_user(chat_id) or {}).get("notificationsEnabled") else "OFF"
    lines.append(f"Notifications: <b>{notif}</b>")
    lines.append("")
    lines.append("You can now use:")
    lines.append("  /r — today's routine")
    lines.append("  /r today | /r now | /r next | /r mon")
    lines.append("  /mysubjects — view/change your subjects")
    lines.append("  /myprofile — view your details")
    lines.append("  /notify — toggle notifications")
    lines.append("  /reregister — change semester + subjects")
    await message.edit_text("\n".join(lines))


# ---------------------------------------------------------------------------
# PG — single subject pick
# ---------------------------------------------------------------------------

async def _show_pg_keyboard(message: Message, state: FSMContext, sem: str, pg_tags: list[str]) -> None:
    # Normally exactly one PG tag applies per semester (e.g. sem 2 -> PG2).
    pg_tag = pg_tags[0] if pg_tags else None
    subjects = CACHE.available_subjects(sem, pg_tag) if pg_tag else []
    if not pg_tag or not subjects:
        await message.edit_text(
            f"\u26A0\uFE0F No PG subjects found for semester {sem}."
        )
        await state.clear()
        return
    await state.update_data(pg_tag=pg_tag)
    kb = _single_select_kb(subjects, "pgsub", "back:track")
    await state.set_state(Registration.waiting_for_pg_subject)
    await message.edit_text(
        f"Semester <b>{sem}</b> (PG).\n\nPick your <b>subject</b>:",
        reply_markup=kb,
    )


@router.callback_query(Registration.waiting_for_pg_subject, F.data.startswith("pgsub:"))
async def on_pg_subject_picked(callback: CallbackQuery, state: FSMContext) -> None:
    subject = callback.data.split(":", 1)[1]
    data = await state.get_data()
    sem = data.get("sem")
    pg_tag = data.get("pg_tag")
    chat_id = callback.message.chat.id

    storage.upsert_user(
        chat_id,
        sem=sem,
        track="PG",
        subjects={pg_tag: subject},
        subjectsHintShown=False,
    )
    storage.upsert_user(chat_id, course=None, sub=None)
    await state.clear()

    notif = "ON" if (storage.get_user(chat_id) or {}).get("notificationsEnabled") else "OFF"
    await callback.message.edit_text(
        f"\u2705 <b>Registration complete!</b>\n\n"
        f"Semester: <b>{sem}</b>\n"
        f"Track: <b>PG</b>\n"
        f"Subject: <b>{subject}</b>\n"
        f"Notifications: <b>{notif}</b>\n\n"
        f"You can now use:\n"
        f"  /r — today's routine\n"
        f"  /r today | /r now | /r next | /r mon\n"
        f"  /mysubjects — view/change your subject\n"
        f"  /myprofile — view your details\n"
        f"  /notify — toggle notifications\n"
        f"  /reregister — change semester/subject"
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Re-entry point for /mysubjects "Change subjects" — skips sem (+track)
# ---------------------------------------------------------------------------

async def start_subject_selection(message: Message, state: FSMContext, sem: str, track: str) -> None:
    """Jump straight into subject selection for an already-known sem/track."""
    await state.clear()
    await state.update_data(sem=sem, track=track, mdc_selected=[])
    if track == "UG":
        await _show_mj_keyboard(message, state, sem)
    else:
        _, pg_tags = _split_categories(sem)
        await _show_pg_keyboard(message, state, sem, pg_tags)


# ---------------------------------------------------------------------------
# Back buttons
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "back:sem")
async def back_to_sem(callback: CallbackQuery, state: FSMContext) -> None:
    """Go back to the semester picker — edits the existing message in place."""
    buttons = [
        [InlineKeyboardButton(text=str(s), callback_data=f"sem:{s}")]
        for s in range(1, 8)
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await state.set_state(Registration.waiting_for_sem)
    try:
        await callback.message.edit_text(
            "\U0001F44B <b>Welcome to Malda College Bot!</b>\n\n"
            "You're now subscribed — you'll receive new notices and class-off alerts automatically.\n\n"
            "To use the routine feature, please pick your <b>semester</b>:",
            reply_markup=kb,
        )
    except Exception:
        await _show_semester_keyboard(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "back:track")
async def back_to_track(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    sem = data.get("sem")
    ug_tags, pg_tags = _split_categories(sem) if sem else ([], [])
    if ug_tags and pg_tags:
        buttons = [
            [InlineKeyboardButton(text="\U0001F393 Undergraduate (UG)", callback_data="track:UG")],
            [InlineKeyboardButton(text="\U0001F393 Postgraduate (PG)", callback_data="track:PG")],
            [InlineKeyboardButton(text="\u2190 Back", callback_data="back:sem")],
        ]
        await state.set_state(Registration.waiting_for_track)
        await callback.message.edit_text(
            f"Semester <b>{sem}</b> selected.\n\nAre you Undergraduate or Postgraduate?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    else:
        await back_to_sem(callback, state)
        return
    await callback.answer()


@router.callback_query(F.data == "back:mj")
async def back_to_mj(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    sem = data.get("sem")
    if not sem:
        await back_to_sem(callback, state)
        return
    await _show_mj_keyboard(callback.message, state, sem)
    await callback.answer()
