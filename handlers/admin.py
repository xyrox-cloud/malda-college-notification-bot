"""
Admin-only commands:
  /refreshdata       — re-fetch all three cached sheets from the network
  /setexception      — set an ad-hoc class-off day + broadcast it (multi-step FSM)
  /clearexception    — remove a wrongly-set exception
  /listexceptions    — show upcoming exceptions (past ones are auto-pruned)
  /users             — list every registered user (sem, subjects, notify status)

Admin access is checked inline at the top of each handler (config.is_admin).
The whole router is also gated by an outer middleware as a defence-in-depth.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import broadcast
import config
import routine
import storage
from sheets import refresh_all

router = Router(name="admin")


# ---------------------------------------------------------------------------
# Outer middleware — admin-only gate for EVERY update that hits this router.
# ---------------------------------------------------------------------------

@router.message.middleware()
async def _admin_gate_msg(handler, event: Message, data):  # type: ignore[no-untyped-def]
    if not config.is_admin(event.chat.id):
        await event.answer("\U0001F6AB This command is admin-only.")
        return
    return await handler(event, data)


@router.callback_query.middleware()
async def _admin_gate_cb(handler, event: CallbackQuery, data):  # type: ignore[no-untyped-def]
    chat_id = event.message.chat.id if event.message else 0
    if not config.is_admin(chat_id):
        await event.answer("Admin only.", show_alert=True)
        return
    return await handler(event, data)


# ---------------------------------------------------------------------------
# /refreshdata
# ---------------------------------------------------------------------------

@router.message(Command("refreshdata"))
async def cmd_refreshdata(message: Message) -> None:
    progress = await message.answer("\U0001F504 Refreshing sheet data from the network...")
    try:
        summary = await refresh_all(force=True)
        pruned = storage.prune_past_exceptions()
        await progress.edit_text(
            f"\u2705 <b>Refresh complete</b>\n\n"
            f"Odd routine rows: {summary['odd']}\n"
            f"Even routine rows: {summary['even']}\n"
            f"Calendar rows: {summary['calendar']}\n"
            f"Past exceptions pruned: {pruned}"
        )
    except Exception as exc:  # noqa: BLE001
        await progress.edit_text(f"\u26A0\uFE0F Refresh failed: {exc}")


# ---------------------------------------------------------------------------
# /setexception — multi-step FSM
# ---------------------------------------------------------------------------

class SetException(StatesGroup):
    waiting_for_date = State()
    waiting_for_reason = State()


@router.message(Command("setexception"))
async def cmd_setexception(message: Message, state: FSMContext) -> None:
    await state.clear()
    today = routine._now()
    tomorrow = today + timedelta(days=1)
    buttons = [
        [
            InlineKeyboardButton(text=f"Today ({routine.date_key(today)})", callback_data="excdate:today"),
            InlineKeyboardButton(text=f"Tomorrow ({routine.date_key(tomorrow)})", callback_data="excdate:tomorrow"),
        ],
        [InlineKeyboardButton(text="Type a date (DD Mon YYYY)", callback_data="excdate:custom")],
    ]
    await state.set_state(SetException.waiting_for_date)
    await message.answer(
        "\U0001F6A7 <b>Set Class-Off Exception</b>\n\n"
        "Pick the date when classes are off:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(SetException.waiting_for_date, F.data.startswith("excdate:"))
async def on_exc_date(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(":", 1)[1]
    if choice == "today":
        date_key = routine.date_key(routine._now())
    elif choice == "tomorrow":
        date_key = routine.date_key(routine._now() + timedelta(days=1))
    else:
        await callback.message.edit_text(
            "\u270F\uFE0F Send the date in <b>DD Mon YYYY</b> format (e.g. <code>11 Aug 2026</code>):"
        )
        await callback.answer()
        return  # state stays waiting_for_date; the message handler below catches the typed date

    await state.update_data(exc_date=date_key, start_str=date_key, end_str=date_key)
    await state.set_state(SetException.waiting_for_reason)
    await callback.message.edit_text(
        f"Date: <b>{date_key}</b>\n\nNow send the <b>reason</b> as free text "
        f"(e.g. <i>Heavy rainfall — college closed</i>):"
    )
    await callback.answer()


@router.message(SetException.waiting_for_date)
async def on_exc_date_typed(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    import re
    parts = re.split(r'(?i)\s+to\s+', text)
    if len(parts) > 2:
        await message.answer("\u26A0\uFE0F Invalid format. Use <b>DD Mon YYYY</b> or <b>DD Mon YYYY to DD Mon YYYY</b>:")
        return

    try:
        start_d = datetime.strptime(parts[0].strip(), "%d %b %Y")
        if len(parts) == 2:
            end_d = datetime.strptime(parts[1].strip(), "%d %b %Y")
            if end_d < start_d:
                await message.answer("\u26A0\uFE0F End date cannot be before start date. Try again:")
                return
            date_key = f"{start_d.strftime('%d %b %Y')} to {end_d.strftime('%d %b %Y')}"
            start_str = start_d.strftime('%d %b %Y')
            end_str = end_d.strftime('%d %b %Y')
        else:
            date_key = start_d.strftime("%d %b %Y")
            start_str = date_key
            end_str = date_key
    except ValueError:
        await message.answer(
            "\u26A0\uFE0F Could not parse that. Use <b>DD Mon YYYY</b> (e.g. <code>11 Aug 2026</code>) "
            "or a range like <code>10 Aug 2026 to 15 Aug 2026</code>:"
        )
        return
        
    await state.update_data(exc_date=date_key, start_str=start_str, end_str=end_str)
    await state.set_state(SetException.waiting_for_reason)
    await message.answer(
        f"Date: <b>{date_key}</b>\n\nNow send the <b>reason</b> as free text "
        f"(e.g. <i>Heavy rainfall — college closed</i>):"
    )


@router.message(SetException.waiting_for_reason)
async def on_exc_reason(message: Message, state: FSMContext) -> None:
    reason = (message.text or "").strip()
    if not reason:
        await message.answer("Reason can't be empty. Try again:")
        return
    data = await state.get_data()
    date_key = data.get("exc_date")
    start_str = data.get("start_str")
    end_str = data.get("end_str")
    
    if not date_key or not start_str or not end_str:
        await state.clear()
        await message.answer("\u26A0\uFE0F Session expired. Run /setexception again.")
        return

    start_d = datetime.strptime(start_str, "%d %b %Y")
    end_d = datetime.strptime(end_str, "%d %b %Y")
    
    current_d = start_d
    while current_d <= end_d:
        storage.set_exception(current_d.strftime("%d %b %Y"), reason, set_by=message.chat.id)
        current_d += timedelta(days=1)
        
    await state.clear()

    # Immediate broadcast to all subscribers with notifications ON
    if start_str != end_str:
        msg_date = f"from {start_str} to {end_str}"
    else:
        msg_date = f"on {start_str}"

    broadcast_text = (
        "\U0001F514 <b>Update from Malda College Bot</b>\n\n"
        f"\U0001F534 <b>Classes are OFF {msg_date}</b>\n"
        f"Reason: {reason}"
    )
    result = await broadcast.broadcast_text(message.bot, broadcast_text)

    await message.answer(
        f"\u2705 Exception saved for <b>{date_key}</b>.\n"
        f"Reason: {reason}\n"
        f"Broadcast sent to {result['sent']}/{result['total']} subscribers."
    )


# ---------------------------------------------------------------------------
# /clearexception <date>
# ---------------------------------------------------------------------------

@router.message(Command("clearexception"))
async def cmd_clearexception(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer("Usage: <code>/clearexception DD Mon YYYY</code> or <code>/clearexception DD Mon YYYY to DD Mon YYYY</code>")
        return
        
    import re
    parts = re.split(r'(?i)\s+to\s+', arg)
    if len(parts) > 2:
        await message.answer("\u26A0\uFE0F Invalid format. Use <b>DD Mon YYYY</b> or <b>DD Mon YYYY to DD Mon YYYY</b>.")
        return
        
    try:
        start_d = datetime.strptime(parts[0].strip(), "%d %b %Y")
        if len(parts) == 2:
            end_d = datetime.strptime(parts[1].strip(), "%d %b %Y")
            if end_d < start_d:
                await message.answer("\u26A0\uFE0F End date cannot be before start date.")
                return
        else:
            end_d = start_d
    except ValueError:
        await message.answer("\u26A0\uFE0F Could not parse date. Use <b>DD Mon YYYY</b> (e.g. <code>11 Aug 2026</code>) or a range.")
        return
        
    removed_count = 0
    current_d = start_d
    while current_d <= end_d:
        date_key = current_d.strftime("%d %b %Y")
        if storage.clear_exception(date_key):
            removed_count += 1
        current_d += timedelta(days=1)
        
    if removed_count > 0:
        if start_d != end_d:
            await message.answer(f"\u2705 Cleared {removed_count} exception(s) from <b>{start_d.strftime('%d %b %Y')} to {end_d.strftime('%d %b %Y')}</b>. Normal logic resumes.")
        else:
            await message.answer(f"\u2705 Cleared exception for <b>{start_d.strftime('%d %b %Y')}</b>. Normal calendar/routine logic resumes.")
    else:
        if start_d != end_d:
            await message.answer(f"\u2139\uFE0F No exceptions were set between <b>{start_d.strftime('%d %b %Y')} and {end_d.strftime('%d %b %Y')}</b>.")
        else:
            await message.answer(f"\u2139\uFE0F No exception was set for <b>{start_d.strftime('%d %b %Y')}</b>.")


# ---------------------------------------------------------------------------
# /listexceptions
# ---------------------------------------------------------------------------

@router.message(Command("listexceptions"))
async def cmd_listexceptions(message: Message) -> None:
    storage.prune_past_exceptions()
    excs = storage.load_exceptions()
    if not excs:
        await message.answer("\u2139\uFE0F No upcoming exceptions set.")
        return

    def _sort_key(item):
        try:
            return datetime.strptime(item[0], "%d %b %Y")
        except ValueError:
            return datetime.max

    sorted_keys = sorted(excs.keys(), key=_sort_key)
    lines = ["\U0001F4CB <b>Upcoming Exceptions</b>\n"]
    
    grouped = []
    for date_key in sorted_keys:
        try:
            d = datetime.strptime(date_key, "%d %b %Y")
        except ValueError:
            continue
        reason = excs[date_key].get('reason', '—')
        
        if not grouped:
            grouped.append({"start": d, "end": d, "reason": reason})
        else:
            last = grouped[-1]
            if (d - last["end"]).days == 1 and last["reason"] == reason:
                last["end"] = d
            else:
                grouped.append({"start": d, "end": d, "reason": reason})

    for g in grouped:
        start_str = g["start"].strftime("%d %b %Y")
        end_str = g["end"].strftime("%d %b %Y")
        if start_str == end_str:
            lines.append(f"\U0001F534 <b>{start_str}</b> — {g['reason']}")
        else:
            lines.append(f"\U0001F534 <b>{start_str} – {end_str}</b> — {g['reason']}")
            
    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# /users — list every registered user with their registration details
# ---------------------------------------------------------------------------

def _format_subjects(rec: dict) -> str:
    track = rec.get("track")
    subjects = rec.get("subjects") or {}
    if not track or not subjects:
        return "not registered yet"
    if track == "PG":
        # subjects is {pg_tag: subject}
        pairs = ", ".join(f"{k}: {v}" for k, v in subjects.items())
        return f"PG — {pairs}"
    parts = []
    if subjects.get("MJ"):
        parts.append(f"MJ: {subjects['MJ']}")
    if subjects.get("MN"):
        parts.append(f"MN: {subjects['MN']}")
    if subjects.get("MDC"):
        parts.append(f"MDC: {', '.join(subjects['MDC'])}")
    return "UG — " + (", ".join(parts) if parts else "no subjects set")


def _format_user_line(chat_id: str, rec: dict) -> str:
    full_name = rec.get("fullName") or "Unknown"
    username = f" (@{rec['username']})" if rec.get("username") else ""
    sem = rec.get("sem", "—")
    notify = "\U0001F7E2 ON" if rec.get("notificationsEnabled") else "\u26AA OFF"
    return (
        f"\U0001F464 <b>{full_name}</b>{username} [<code>{chat_id}</code>]\n"
        f"   Sem {sem} | {_format_subjects(rec)} | {notify}"
    )


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    users = storage.load_users()
    if not users:
        await message.answer("\u2139\uFE0F No registered users yet.")
        return

    # Sort by semester (numeric where possible), then by name.
    def _sort_key(item):
        chat_id, rec = item
        sem = rec.get("sem")
        try:
            sem_val = int(sem)
        except (TypeError, ValueError):
            sem_val = 99
        return (sem_val, (rec.get("fullName") or "").lower())

    ordered = sorted(users.items(), key=_sort_key)
    total, on = storage.subscriber_count()

    lines = [f"\U0001F465 <b>{total} registered user(s)</b> — {on} with notifications ON\n"]
    for chat_id, rec in ordered:
        lines.append(_format_user_line(chat_id, rec))

    # Telegram caps messages at 4096 chars — send in chunks of ~25 users.
    CHUNK = 25
    header = lines[0]
    body_lines = lines[1:]
    for i in range(0, len(body_lines), CHUNK):
        chunk_lines = body_lines[i : i + CHUNK]
        prefix = header if i == 0 else f"\U0001F465 <b>(continued {i // CHUNK + 1})</b>"
        await message.answer(prefix + "\n\n" + "\n\n".join(chunk_lines))
