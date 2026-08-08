"""
/r — routine lookup.

  /r            -> today's routine
  /r today      -> today's routine
  /r <day>      -> next occurrence of that weekday (mon/tue/wed/thu/fri/sat)
  /r now        -> currently ongoing class
  /r next       -> next upcoming class

Holiday/exam/special-day aware via routine.resolve_day_status().
Admin exceptions take priority over the calendar.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import routine
import storage

router = Router(name="routine_cmd")


def _ensure_registered(message: Message, user):
    """Return an error string if the user isn't registered at all, else None.

    Semester alone is enough to show a routine (subjects unset just falls
    back to the unfiltered semester routine — see routine._classes_for_user).
    """
    if not user:
        return "\u26A0\uFE0F You're not registered yet. Send /start to pick your semester and subjects."
    if not user.get("sem"):
        return (
            "\u26A0\uFE0F Your registration is incomplete.\n"
            "Send /reregister to pick your semester and subjects."
        )
    return None


@router.message(Command("r"))
async def cmd_r(message: Message, command: CommandObject) -> None:
    user = storage.get_user(message.chat.id)
    err = _ensure_registered(message, user)
    if err:
        await message.answer(err)
        return

    arg = (command.args or "").strip().lower()
    if not arg or arg == "today":
        await message.answer(routine.routine_today(user))
        return
    if arg == "now":
        await message.answer(routine.routine_now(user))
        return
    if arg == "next":
        await message.answer(routine.routine_next(user))
        return
    # Treat as weekday
    await message.answer(routine.routine_for_weekday(user, arg))
