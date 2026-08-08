"""
JSON-on-disk persistence with file locking.

Three stores, each a separate JSON file under DATA_DIR:
  - users.json         -> { "<chatId>": {
        chatId, sem, track ("UG"|"PG"),
        subjects: {"MJ": "...", "MN": "...", "MDC": [...]}  (UG)
               or  {"<PG tag e.g. PG2>": "..."}              (PG),
        subjectsHintShown, notificationsEnabled, registeredAt
    } }
    Older records may still carry legacy `course`/`sub` fields (single
    paper-type + subject) from before the MJ/MN/MDC subject-selection
    feature — these are ignored by routine lookups once `subjects`+`track`
    are set, and are cleared out the next time the user completes
    registration.
  - exceptions.json    -> { "<DD Mon YYYY>": { reason, setBy, setAt } }
  - seen_notices.json  -> { titles: [...], updated: "<iso>" }

All writes go through a single FileLock per file so the async broadcast loop
and the command handlers can safely mutate state concurrently.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

import config


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        config.logger.error("Failed to read %s: %s", path, exc)
        return default


def _write_json(path: Path, data: Any) -> None:
    lock = FileLock(str(path) + ".lock", timeout=10)
    try:
        with lock:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        config.logger.error("Failed to write %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Users store
# ---------------------------------------------------------------------------

def load_users() -> dict[str, dict]:
    """
    Load the users map. Runs a one-time migration: any user record missing
    `notificationsEnabled` gets it defaulted to True (so existing single-user
    data from the old bot keeps receiving notices after upgrade).
    """
    data = _read_json(config.USERS_FILE, {})
    if not isinstance(data, dict):
        return {}
    changed = False
    for chat_id, rec in data.items():
        if not isinstance(rec, dict):
            continue
        if "notificationsEnabled" not in rec:
            rec["notificationsEnabled"] = True
            changed = True
        if "chatId" not in rec:
            rec["chatId"] = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
            changed = True
    if changed:
        _write_json(config.USERS_FILE, data)
    return data


def save_users(users: dict[str, dict]) -> None:
    _write_json(config.USERS_FILE, users)


def get_user(chat_id: int) -> dict | None:
    return load_users().get(str(chat_id))


def upsert_user(chat_id: int, **fields) -> dict:
    """
    Insert or update a user record. Only the supplied fields are touched —
    existing fields (e.g. notificationsEnabled) are preserved unless overridden.
    """
    users = load_users()
    key = str(chat_id)
    rec = users.get(key, {})
    if not rec:
        rec = {
            "chatId": chat_id,
            "notificationsEnabled": True,
            "registeredAt": datetime.now(timezone.utc).isoformat(),
        }
    rec.update(fields)
    users[key] = rec
    save_users(users)
    return rec


def set_notification_pref(chat_id: int, enabled: bool) -> None:
    upsert_user(chat_id, notificationsEnabled=bool(enabled))


def subscriber_count() -> tuple[int, int]:
    """Return (total_users, users_with_notifications_on)."""
    users = load_users()
    total = len(users)
    on = sum(1 for u in users.values() if u.get("notificationsEnabled"))
    return total, on


def subscribers_with_notifications() -> list[int]:
    users = load_users()
    return [
        int(rec["chatId"])
        for rec in users.values()
        if rec.get("notificationsEnabled") and rec.get("chatId") is not None
    ]


# ---------------------------------------------------------------------------
# Exceptions store (admin-set class-off days)
# ---------------------------------------------------------------------------

def load_exceptions() -> dict[str, dict]:
    data = _read_json(config.EXCEPTIONS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_exceptions(excs: dict[str, dict]) -> None:
    _write_json(config.EXCEPTIONS_FILE, excs)


def set_exception(date_key: str, reason: str, set_by: int) -> None:
    excs = load_exceptions()
    excs[date_key] = {
        "reason": reason,
        "setBy": set_by,
        "setAt": datetime.now(timezone.utc).isoformat(),
    }
    save_exceptions(excs)


def clear_exception(date_key: str) -> bool:
    excs = load_exceptions()
    if date_key in excs:
        excs.pop(date_key)
        save_exceptions(excs)
        return True
    return False


def get_exception(date_key: str) -> dict | None:
    return load_exceptions().get(date_key)


def prune_past_exceptions() -> int:
    """
    Drop exception entries whose date is in the past. Returns the count removed.
    Dates are stored in "DD Mon YYYY" format (e.g. "11 Aug 2026").
    """
    excs = load_exceptions()
    today = datetime.now().date()
    removed = 0
    for key in list(excs.keys()):
        try:
            d = datetime.strptime(key, "%d %b %Y").date()
        except ValueError:
            continue
        if d < today:
            excs.pop(key, None)
            removed += 1
    if removed:
        save_exceptions(excs)
    return removed


# ---------------------------------------------------------------------------
# Seen-notices store (so we don't re-broadcast old notices)
# ---------------------------------------------------------------------------

def load_seen() -> set[str]:
    data = _read_json(config.SEEN_FILE, {})
    return set(data.get("titles", [])) if isinstance(data, dict) else set()


def save_seen(seen: set[str]) -> None:
    _write_json(
        config.SEEN_FILE,
        {
            "titles": sorted(seen),
            "updated": datetime.now(timezone.utc).isoformat(),
        },
    )
