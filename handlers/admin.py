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

def normalize_date_str(d_str: str) -> str:
    import re
    parts = re.split(r'\s+', d_str.strip())
    if len(parts) != 3:
        return d_str
    
    day, month, year = parts
    month_map = {
        'january': 'Jan', 'jan': 'Jan', 'february': 'Feb', 'feb': 'Feb',
        'march': 'Mar', 'mar': 'Mar', 'april': 'Apr', 'apr': 'Apr', 'may': 'May',
        'june': 'Jun', 'jun': 'Jun', 'july': 'Jul', 'jul': 'Jul',
        'august': 'Aug', 'aug': 'Aug', 'september': 'Sep', 'sept': 'Sep', 'sep': 'Sep',
        'october': 'Oct', 'oct': 'Oct', 'november': 'Nov', 'nov': 'Nov',
        'december': 'Dec', 'dec': 'Dec'
    }
    norm_month = month_map.get(month.lower(), month.capitalize()[:3])
    return f"{day} {norm_month} {year}"

router = Router(name="admin")


# ---------------------------------------------------------------------------
# Outer middleware — admin-only gate for EVERY update that hits this router.
# ---------------------------------------------------------------------------

@router.message.middleware()
async def _admin_gate_msg(handler, event: Message, data: dict):
    if not config.is_admin(event.chat.id):
        await event.answer("\U0001F6AB This command is admin-only.")
        return
        
    # Clear any pending FSM state if the admin sends a new command
    text = event.text or ""
    if text.startswith("/"):
        state = data.get("state")
        if state:
            current_state = await state.get_state()
            if current_state:
                await state.clear()
                
    return await handler(event, data)


@router.callback_query.middleware()
async def _admin_gate_cb(handler, event: CallbackQuery, data: dict):
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
async def cmd_setexception(message: Message, state: FSMContext, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    
    if arg:
        import re
        date_pattern = r'\d{1,2}\s+[a-zA-Z]{3,10}\s+\d{4}'
        range_match = re.match(fr'^({date_pattern})\s+to\s+({date_pattern})(?:\s+(.*))?$', arg, re.IGNORECASE)
        single_match = re.match(fr'^({date_pattern})(?:\s+(.*))?$', arg, re.IGNORECASE)
        
        start_str_raw, end_str_raw, reason = None, None, None
        
        if range_match:
            start_str_raw = range_match.group(1).strip()
            end_str_raw = range_match.group(2).strip()
            reason = range_match.group(3)
        elif single_match:
            start_str_raw = single_match.group(1).strip()
            end_str_raw = start_str_raw
            reason = single_match.group(2)
            
        if start_str_raw and end_str_raw:
            reason = reason.strip() if reason else ""
            if not reason:
                await message.answer("\u26A0\uFE0F Reason is required when using the inline command.")
                return
            
            try:
                start_d = datetime.strptime(normalize_date_str(start_str_raw), "%d %b %Y")
                if start_d.date() < routine._now().date():
                    await message.answer("\u26A0\uFE0F Start date cannot be in the past.")
                    return
            except ValueError:
                await message.answer(f"\u26A0\uFE0F Could not parse date: '{start_str_raw}' — please check the month name.")
                return
                
            try:
                end_d = datetime.strptime(normalize_date_str(end_str_raw), "%d %b %Y")
            except ValueError:
                await message.answer(f"\u26A0\uFE0F Could not parse date: '{end_str_raw}' — please check the month name.")
                return
                
            if end_d < start_d:
                await message.answer("\u26A0\uFE0F End date cannot be before start date.")
                return
                
            date_key = f"{start_d.strftime('%d %b %Y')} to {end_d.strftime('%d %b %Y')}" if start_d != end_d else start_d.strftime('%d %b %Y')
            start_str = start_d.strftime('%d %b %Y')
            end_str = end_d.strftime('%d %b %Y')
                
            # Process setting exception directly
            current_d = start_d
            while current_d <= end_d:
                storage.set_exception(current_d.strftime("%d %b %Y"), reason, set_by=message.chat.id)
                current_d += timedelta(days=1)
                
            # Broadcast
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
            return
            
        else:
            await message.answer(
                "\u26A0\uFE0F Could not parse inline command.\n"
                "Format: <code>/setexception DD Mon YYYY [to DD Mon YYYY] Reason</code>\n"
                "Or simply send <code>/setexception</code> to use the interactive menu."
            )
            return

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
    import time
    await state.update_data(timestamp=time.time())
    await state.set_state(SetException.waiting_for_date)
    await message.answer(
        "\U0001F6A7 <b>Set Class-Off Exception</b>\n\n"
        "Pick the date when classes are off:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(SetException.waiting_for_date, F.data.startswith("excdate:"))
async def on_exc_date(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    import time
    if time.time() - data.get("timestamp", 0) > 180:
        await state.clear()
        await callback.message.edit_text("⏱️ Session expired due to inactivity. Please run the command again.")
        return
        
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

    import time
    await state.update_data(exc_date=date_key, start_str=date_key, end_str=date_key, timestamp=time.time())
    await state.set_state(SetException.waiting_for_reason)
    await callback.message.edit_text(
        f"Date: <b>{date_key}</b>\n\nNow send the <b>reason</b> as free text "
        f"(e.g. <i>Heavy rainfall — college closed</i>):"
    )
    await callback.answer()


@router.message(SetException.waiting_for_date)
async def on_exc_date_typed(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    import time
    if time.time() - data.get("timestamp", 0) > 180:
        await state.clear()
        await message.answer("⏱️ Session expired due to inactivity. Please run the command again.")
        return
        
    text = (message.text or "").strip()
    import re
    parts = re.split(r'(?i)\s+to\s+', text)
    if len(parts) > 2:
        await message.answer("\u26A0\uFE0F Invalid format. Use <b>DD Mon YYYY</b> or <b>DD Mon YYYY to DD Mon YYYY</b>:")
        return

    try:
        start_str_raw = parts[0].strip()
        start_d = datetime.strptime(normalize_date_str(start_str_raw), "%d %b %Y")
        if start_d.date() < routine._now().date():
            await message.answer("\u26A0\uFE0F Start date cannot be in the past. Try again:")
            return
            
        if len(parts) == 2:
            end_str_raw = parts[1].strip()
            try:
                end_d = datetime.strptime(normalize_date_str(end_str_raw), "%d %b %Y")
            except ValueError:
                await message.answer(f"\u26A0\uFE0F Could not parse date: '{end_str_raw}' — please check the month name.")
                return
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
        await message.answer(f"\u26A0\uFE0F Could not parse date: '{start_str_raw}' — please check the month name.")
        return
        
    import time
    await state.update_data(exc_date=date_key, start_str=start_str, end_str=end_str, timestamp=time.time())
    await state.set_state(SetException.waiting_for_reason)
    await message.answer(
        f"Date: <b>{date_key}</b>\n\nNow send the <b>reason</b> as free text "
        f"(e.g. <i>Heavy rainfall — college closed</i>):"
    )


@router.message(SetException.waiting_for_reason)
async def on_exc_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    import time
    if time.time() - data.get("timestamp", 0) > 180:
        await state.clear()
        await message.answer("⏱️ Session expired due to inactivity. Please run the command again.")
        return
        
    reason = (message.text or "").strip()
    if not reason:
        await message.answer("Reason can't be empty. Try again:")
        return
        
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
# Helper for Exceptions
# ---------------------------------------------------------------------------

def _get_grouped_exceptions():
    storage.prune_past_exceptions()
    excs = storage.load_exceptions()
    if not excs:
        return []
    def _sort_key(item):
        try:
            return datetime.strptime(item[0], "%d %b %Y")
        except ValueError:
            return datetime.max
    sorted_keys = sorted(excs.keys(), key=_sort_key)
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
    return grouped


# ---------------------------------------------------------------------------
# /clearexception <date> (or interactive)
# ---------------------------------------------------------------------------

class ClearException(StatesGroup):
    waiting_for_selection = State()
    waiting_for_confirmation = State()


@router.message(Command("clearexception"))
async def cmd_clearexception(message: Message, command: CommandObject, state: FSMContext) -> None:
    arg = (command.args or "").strip()
    if not arg:
        grouped = _get_grouped_exceptions()
        if not grouped:
            await message.answer("\u2139\uFE0F No upcoming exceptions to clear.")
            return
            
        buttons = []
        emoji_nums = ["1\ufe0f\u20e3", "2\ufe0f\u20e3", "3\ufe0f\u20e3", "4\ufe0f\u20e3", "5\ufe0f\u20e3", "6\ufe0f\u20e3", "7\ufe0f\u20e3", "8\ufe0f\u20e3", "9\ufe0f\u20e3", "\ud83d\udd1f"]
        
        lines = ["\U0001F5D1 <b>Select Exception to Clear:</b>\n"]
        grouped_data = []
        
        for i, g in enumerate(grouped):
            start_str = g["start"].strftime("%d %b %Y")
            end_str = g["end"].strftime("%d %b %Y")
            lbl = f"{start_str}" if start_str == end_str else f"{start_str} to {end_str}"
            prefix = emoji_nums[i] if i < 10 else f"{i+1}."
            
            lines.append(f"{prefix} <b>{lbl}</b> — {g['reason']}")
            buttons.append([InlineKeyboardButton(text=f"{prefix} {lbl}", callback_data=f"clear_select:{i}")])
            
            grouped_data.append({
                "start": g["start"].strftime("%Y-%m-%d"),
                "end": g["end"].strftime("%Y-%m-%d"),
                "lbl": lbl,
                "reason": g["reason"]
            })
            
        buttons.append([InlineKeyboardButton(text="\u274C Cancel", callback_data="clear_select:cancel")])
        
        await state.update_data(grouped_excs=grouped_data)
        await state.set_state(ClearException.waiting_for_selection)
        await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        return
        
    import re
    parts = re.split(r'(?i)\s+to\s+', arg)
    if len(parts) > 2:
        await message.answer("\u26A0\uFE0F Invalid format. Use <b>DD Mon YYYY</b> or <b>DD Mon YYYY to DD Mon YYYY</b>.")
        return
        
    try:
        start_str_raw = parts[0].strip()
        start_d = datetime.strptime(normalize_date_str(start_str_raw), "%d %b %Y")
        if len(parts) == 2:
            end_str_raw = parts[1].strip()
            try:
                end_d = datetime.strptime(normalize_date_str(end_str_raw), "%d %b %Y")
            except ValueError:
                await message.answer(f"\u26A0\uFE0F Could not parse date: '{end_str_raw}' — please check the month name.")
                return
            if end_d < start_d:
                await message.answer("\u26A0\uFE0F End date cannot be before start date.")
                return
        else:
            end_d = start_d
    except ValueError:
        await message.answer(f"\u26A0\uFE0F Could not parse date: '{start_str_raw}' — please check the month name.")
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


@router.callback_query(ClearException.waiting_for_selection, F.data.startswith("clear_select:"))
async def on_clear_select(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(":", 1)[1]
    if choice == "cancel":
        await state.clear()
        await callback.message.edit_text("\u274C Clear operation cancelled.")
        return
        
    idx = int(choice)
    data = await state.get_data()
    grouped = data.get("grouped_excs", [])
    if idx < 0 or idx >= len(grouped):
        await callback.answer("Invalid selection.")
        return
        
    selected = grouped[idx]
    await state.update_data(selected_exc=selected)
    
    buttons = [
        [
            InlineKeyboardButton(text="\u2705 Yes, clear it", callback_data="clear_confirm:yes"),
            InlineKeyboardButton(text="\u274C No, cancel", callback_data="clear_confirm:no"),
        ]
    ]
    await state.set_state(ClearException.waiting_for_confirmation)
    await callback.message.edit_text(
        f"\u26A0\uFE0F <b>Are you sure you want to clear this exception?</b>\n\n"
        f"<b>Date(s):</b> {selected['lbl']}\n"
        f"<b>Reason:</b> {selected['reason']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(ClearException.waiting_for_confirmation, F.data.startswith("clear_confirm:"))
async def on_clear_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(":", 1)[1]
    if choice == "no":
        await state.clear()
        await callback.message.edit_text("\u274C Clear operation cancelled.")
        return
        
    data = await state.get_data()
    selected = data.get("selected_exc")
    await state.clear()
    
    if not selected:
        await callback.message.edit_text("\u26A0\uFE0F Session expired.")
        return
        
    start_d = datetime.strptime(selected["start"], "%Y-%m-%d")
    end_d = datetime.strptime(selected["end"], "%Y-%m-%d")
    
    removed_count = 0
    current_d = start_d
    while current_d <= end_d:
        if storage.clear_exception(current_d.strftime("%d %b %Y")):
            removed_count += 1
        current_d += timedelta(days=1)
        
    if removed_count > 0:
        await callback.message.edit_text(f"\u2705 Successfully cleared exception for <b>{selected['lbl']}</b>. Normal calendar/routine logic resumes.")
    else:
        await callback.message.edit_text(f"\u2139\uFE0F Could not find or clear exception for <b>{selected['lbl']}</b>.")


# ---------------------------------------------------------------------------
# /listexceptions
# ---------------------------------------------------------------------------

@router.message(Command("listexceptions"))
async def cmd_listexceptions(message: Message) -> None:
    grouped = _get_grouped_exceptions()
    if not grouped:
        await message.answer("\u2139\uFE0F No upcoming exceptions set.")
        return
        
    lines = ["\U0001F4CB <b>Upcoming Exceptions</b>\n"]
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


# ---------------------------------------------------------------------------
# Admin management: /addadmin, /removeadmin, /listadmins
# ---------------------------------------------------------------------------

@router.message(Command("addadmin"))
async def cmd_addadmin(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer("Usage: <code>/addadmin [user_id or @username]</code>")
        return
        
    users = storage.load_users()
    target_id = None
    target_name = None
    
    if arg.startswith("@"):
        uname = arg[1:].lower()
        for cid, rec in users.items():
            if rec.get("username", "").lower() == uname:
                target_id = int(cid)
                target_name = rec.get("fullName", uname)
                break
    elif arg.lstrip("-").isdigit():
        target_id = int(arg)
        rec = users.get(str(target_id))
        target_name = rec.get("fullName", str(target_id)) if rec else str(target_id)
        
    if not target_id:
        await message.answer(f"\u26A0\uFE0F User <b>{arg}</b> not found in database. Make sure they have started the bot.")
        return
        
    if config.is_admin(target_id):
        await message.answer("Already an admin.")
        return
        
    if storage.add_dynamic_admin(target_id):
        await message.answer(f"\u2705 User <b>{target_name}</b> (<code>{target_id}</code>) added as admin.")
    else:
        await message.answer("Already an admin.")


@router.message(Command("removeadmin"))
async def cmd_removeadmin(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer("Usage: <code>/removeadmin [user_id or @username]</code>")
        return
        
    users = storage.load_users()
    target_id = None
    target_name = None
    
    if arg.startswith("@"):
        uname = arg[1:].lower()
        for cid, rec in users.items():
            if rec.get("username", "").lower() == uname:
                target_id = int(cid)
                target_name = rec.get("fullName", uname)
                break
    elif arg.lstrip("-").isdigit():
        target_id = int(arg)
        rec = users.get(str(target_id))
        target_name = rec.get("fullName", str(target_id)) if rec else str(target_id)
        
    if not target_id:
        if arg.lstrip("-").isdigit():
            target_id = int(arg)
            target_name = str(target_id)
        else:
            await message.answer(f"\u26A0\uFE0F User <b>{arg}</b> not found.")
            return
            
    if not config.is_admin(target_id):
        await message.answer("\u26A0\uFE0F That user is not an admin.")
        return
        
    dynamic_admins = storage.load_dynamic_admins()
    env_admins = config.ADMIN_CHAT_IDS
    total_admins = len(dynamic_admins.union(env_admins))
    
    if total_admins <= 1:
        await message.answer("\u26A0\uFE0F Cannot remove the last remaining admin.")
        return
        
    if target_id in env_admins:
        await message.answer("\u26A0\uFE0F Cannot remove this admin from the bot. They are hardcoded in the environment variable.")
        return
        
    if storage.remove_dynamic_admin(target_id):
        await message.answer(f"\u2705 User <b>{target_name}</b> (<code>{target_id}</code>) removed from admin list.")
    else:
        await message.answer("\u26A0\uFE0F That user is not an admin.")


@router.message(Command("listadmins"))
async def cmd_listadmins(message: Message) -> None:
    dynamic_admins = storage.load_dynamic_admins()
    env_admins = config.ADMIN_CHAT_IDS
    all_admins = dynamic_admins.union(env_admins)
    
    if not all_admins:
        await message.answer("No admins configured.")
        return
        
    users = storage.load_users()
    lines = ["\U0001F46E <b>Bot Admins</b>\n"]
    
    for admin_id in all_admins:
        rec = users.get(str(admin_id))
        if rec:
            name = rec.get("fullName", "Unknown")
            uname = f" (@{rec['username']})" if rec.get("username") else ""
            lines.append(f"\U0001F539 <b>{name}</b>{uname} [<code>{admin_id}</code>]")
        else:
            lines.append(f"\U0001F539 [<code>{admin_id}</code>] (Not registered)")
            
    await message.answer("\n".join(lines))

# ---------------------------------------------------------------------------
# /broadcast - Send a custom message to all subscribers
# ---------------------------------------------------------------------------

class BroadcastCmd(StatesGroup):
    waiting_for_confirmation = State()

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject, state: FSMContext) -> None:
    arg = (command.args or "").strip()
    if not arg:
        await message.answer("Please provide a message to broadcast. Usage: <code>/broadcast <your message></code>")
        return
        
    total, on = storage.subscriber_count()
    
    await state.update_data(broadcast_text=arg)
    
    buttons = [
        [
            InlineKeyboardButton(text="\u2705 Yes", callback_data="bc_confirm:yes"),
            InlineKeyboardButton(text="\u274C No", callback_data="bc_confirm:no"),
        ]
    ]
    
    await state.set_state(BroadcastCmd.waiting_for_confirmation)
    await message.answer(
        f"\U0001F4E2 <b>Broadcast Preview:</b>\n\n{arg}\n\nSend to {on} subscribers?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@router.callback_query(BroadcastCmd.waiting_for_confirmation, F.data.startswith("bc_confirm:"))
async def on_bc_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(":", 1)[1]
    if choice == "no":
        await state.clear()
        await callback.message.edit_text("\u274C Broadcast cancelled.")
        return
        
    data = await state.get_data()
    text = data.get("broadcast_text")
    await state.clear()
    
    if not text:
        await callback.message.edit_text("\u26A0\uFE0F Session expired.")
        return
        
    await callback.message.edit_text("\u23F3 Broadcasting... Please wait.")
    
    result = await broadcast.broadcast_text(callback.bot, text)
    
    await callback.message.edit_text(f"\u2705 Broadcast sent to {result['sent']}/{result['total']} subscribers.")
