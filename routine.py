"""
Routine lookup logic — resolves `/r`, `/r today`, `/r <day>`, `/r now`, `/r next`.

Resolution order (per the spec):
  1. exceptions.json  — admin-set class-off day (college-wide, semester-agnostic)
  2. academic calendar — COD / HLD / UED / SPD status for that date
  3. routine sheet    — filtered by (sem, course, sub) and weekday

All public functions return a ready-to-send HTML string.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import storage
from sheets import CACHE, time_to_hour_minute


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

TZ = ZoneInfo(config.TZ_NAME)

WEEKDAY_ABBR = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
WEEKDAY_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _now() -> datetime:
    return datetime.now(TZ)


def today_key() -> str:
    """'DD Mon YYYY' e.g. '07 Aug 2026' — matches the exceptions.json key format."""
    return _now().strftime("%d %b %Y")


def date_key(d: datetime) -> str:
    return d.strftime("%d %b %Y")


def today_weekday() -> str:
    return WEEKDAY_ABBR[_now().weekday()]


def next_occurrence_of_weekday(target: str) -> datetime:
    """
    Return the datetime of the next occurrence of the given weekday (MON..SAT)
    including today. If today is the target, today is returned.
    """
    target_idx = WEEKDAY_ABBR.index(target.upper())
    today = _now()
    delta = (target_idx - today.weekday()) % 7
    return today + timedelta(days=delta)


# ---------------------------------------------------------------------------
# Status resolution (exception -> calendar)
# ---------------------------------------------------------------------------

def resolve_day_status(date_str: str) -> tuple[str | None, str]:
    """
    Return (status_code, human_note) for a date.
      status_code: 'OFF' (classes off) | 'EXAM' (university exam) | 'HOLIDAY' | None (normal)
      human_note:  the reason / remark to show
    """
    # 1) Admin-set exception wins
    exc = storage.get_exception(date_str)
    if exc:
        return ("OFF", f"Classes Off Today — {exc.get('reason', 'no reason given')}")

    # 2) Academic calendar
    row = CACHE.calendar_for_date(date_str)
    if not row:
        return (None, "")
    open_holiday = (row.get("open_holiday") or "").upper()
    class_exam = (row.get("class_exam") or "").upper()
    remarks = row.get("remarks", "")

    # HLD = holiday
    if "HLD" in open_holiday:
        return ("HOLIDAY", remarks or "Holiday (per academic calendar)")
    # UED = university exam day
    if "UED" in class_exam:
        return ("EXAM", remarks or "University Exam Day")
    # SPD = special day (treat as holiday-ish note)
    if "SPD" in class_exam or "SPD" in open_holiday:
        return ("OFF", remarks or "Special Day — classes may be off")
    return (None, remarks)


# ---------------------------------------------------------------------------
# Subject-filtered lookup (with fallback for users who skipped subject setup)
# ---------------------------------------------------------------------------

def _classes_for_user(user: dict, day: str) -> tuple[list[dict], bool]:
    """
    Return (classes, is_filtered).

    is_filtered=True  -> classes already narrowed to the user's MJ/MN/MDC
                          (or PG) subject selection.
    is_filtered=False -> user hasn't completed subject selection (new
                          /mysubjects feature); we fell back to the full,
                          unfiltered semester routine.
    """
    sem = user.get("sem")
    subjects = user.get("subjects")
    track = user.get("track")
    if subjects and track:
        return CACHE.classes_for_subjects(sem, track, subjects, day=day), True
    return CACHE.all_classes_for_sem(sem, day=day), False


def _subjects_hint(user: dict) -> str:
    """
    One-time nudge shown when we fall back to the unfiltered routine.
    Marks the user record so it isn't repeated on every /r call.
    """
    if user.get("subjectsHintShown"):
        return ""
    chat_id = user.get("chatId")
    if chat_id is not None:
        storage.upsert_user(chat_id, subjectsHintShown=True)
        user["subjectsHintShown"] = True
    return "\n\n\U0001F4A1 Set your subjects with /mysubjects to see only your classes."


# ---------------------------------------------------------------------------
# Routine row formatting
# ---------------------------------------------------------------------------

def _format_class_row(r: dict) -> str:
    time_str = r.get("time", "")
    course = r.get("course", "")
    sub = r.get("sub", "")
    room = r.get("room", "")
    teacher = r.get("teacher", "")
    parts = [f"\U0001F552 <b>{time_str}</b>"]
    if course:
        parts.append(course)
    if sub:
        parts.append(f"<i>{sub}</i>")
    if room:
        parts.append(f"Room {room}")
    if teacher:
        parts.append(f"[{teacher}]")
    return " — ".join(parts)


def _format_day_header(d: datetime) -> str:
    return f"\U0001F4C5 {WEEKDAY_FULL[d.weekday()]}, {date_key(d)}"


def _no_classes_message(d: datetime, reason: str = "") -> str:
    head = _format_day_header(d)
    if reason:
        return f"{head}\n\n\U0001F4ED No classes found. {reason}"
    return f"{head}\n\n\U0001F4ED No classes scheduled for your registration on this day."


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def routine_for_date(user: dict, d: datetime) -> str:
    """
    Full routine for a specific date (used by /r today and /r <day>).
    Honors exceptions and calendar status.
    """
    date_str = date_key(d)
    head = _format_day_header(d)

    status, note = resolve_day_status(date_str)
    if status == "OFF":
        return f"{head}\n\n\U0001F534 {note}"
    if status == "HOLIDAY":
        # Always say "Holiday" explicitly so the message is unambiguous,
        # even when the calendar remarks column is empty.
        holiday_note = f"Holiday — {note}" if note else "Holiday (per academic calendar)"
        return f"{head}\n\n\U0001F389 {holiday_note}"

    classes, filtered = _classes_for_user(user, WEEKDAY_ABBR[d.weekday()])
    hint = "" if filtered else _subjects_hint(user)

    # Exam-day banner is shown regardless of whether classes exist.
    exam_banner = f"\n\n\U0001F4DD <b>Exam Day</b> — {note}" if status == "EXAM" and note else ""

    if not classes:
        no_class_reason = note if status == "EXAM" else ""
        return _no_classes_message(d, no_class_reason) + exam_banner + hint

    body = "\n".join(_format_class_row(r) for r in classes)
    return f"{head}\n\n{body}{exam_banner}{hint}"


def routine_today(user: dict) -> str:
    return routine_for_date(user, _now())


def routine_for_weekday(user: dict, target_day: str) -> str:
    """
    /r <day> — next occurrence of that weekday (including today).
    """
    target = target_day.strip().upper()[:3]
    if target not in WEEKDAY_ABBR:
        return (
            f"\u26A0\uFE0F Unknown day '{target_day}'. "
            f"Use one of: {', '.join(WEEKDAY_ABBR[:6])} (or 'today', 'now', 'next')."
        )
    d = next_occurrence_of_weekday(target)
    return routine_for_date(user, d)


def routine_now(user: dict) -> str:
    """
    /r now — the class currently in progress (start <= now < start + SLOT_DURATION).
    """
    now = _now()
    head = _format_day_header(now)
    status, note = resolve_day_status(date_key(now))
    if status == "OFF":
        return f"{head}\n\n\U0001F534 {note}"
    if status == "HOLIDAY":
        return f"{head}\n\n\U0001F389 {note}"

    today_classes, filtered = _classes_for_user(user, WEEKDAY_ABBR[now.weekday()])
    hint = "" if filtered else _subjects_hint(user)
    now_minutes = now.hour * 60 + now.minute
    for r in today_classes:
        h, m = time_to_hour_minute(r.get("time", ""))
        if h < 0:
            continue
        start = h * 60 + m
        end = start + config.SLOT_DURATION_MIN
        if start <= now_minutes < end:
            return f"{head}\n\n\U0001F7E2 <b>Live now:</b>\n{_format_class_row(r)}{hint}"
    return f"{head}\n\n\U0001F634 No class in progress right now.{hint}"


def routine_next(user: dict) -> str:
    """
    /r next — the next upcoming class today; if none today, the first class
    of the next available college day.
    """
    now = _now()
    head = _format_day_header(now)
    status, note = resolve_day_status(date_key(now))
    if status == "OFF":
        return f"{head}\n\n\U0001F534 {note}"
    if status == "HOLIDAY":
        return f"{head}\n\n\U0001F389 {note}"

    today_classes, filtered = _classes_for_user(user, WEEKDAY_ABBR[now.weekday()])
    hint = "" if filtered else _subjects_hint(user)
    now_minutes = now.hour * 60 + now.minute
    for r in today_classes:
        h, m = time_to_hour_minute(r.get("time", ""))
        if h < 0:
            continue
        start = h * 60 + m
        if start > now_minutes:
            delta = start - now_minutes
            return (
                f"{head}\n\n\U0001F551 <b>Next class</b> (in ~{delta} min):\n"
                f"{_format_class_row(r)}{hint}"
            )
    # Nothing left today — look ahead to the next college day (up to 7 days)
    for offset in range(1, 8):
        d = now + timedelta(days=offset)
        s, _ = resolve_day_status(date_key(d))
        if s in ("OFF", "HOLIDAY"):
            continue
        future_classes, f_filtered = _classes_for_user(user, WEEKDAY_ABBR[d.weekday()])
        if future_classes:
            f_hint = "" if f_filtered else _subjects_hint(user)
            return (
                f"\U0001F4C5 {WEEKDAY_FULL[d.weekday()]}, {date_key(d)}\n\n"
                f"\U0001F551 <b>Next class</b>:\n{_format_class_row(future_classes[0])}{f_hint}"
            )
    return f"{head}\n\n\U0001F4ED No upcoming class found in the next 7 days.{hint}"
