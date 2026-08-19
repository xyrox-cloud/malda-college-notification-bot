"""
Centralized configuration for the Malda College Bot.

All values come from environment variables (loaded once at import time).
Defaults are sensible for local development; production overrides via .env.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE = DATA_DIR / os.environ.get("USERS_FILE", "users.json")
EXCEPTIONS_FILE = DATA_DIR / "exceptions.json"
SEEN_FILE = DATA_DIR / os.environ.get("SEEN_FILE", "seen_notices.json")
SUGGESTIONS_FILE = DATA_DIR / "suggestions.json"
ADMINS_FILE = DATA_DIR / os.environ.get("ADMINS_FILE", "admins.json")

# On-disk fallback caches for the three Google Sheets sources
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
ODD_CACHE_FILE = CACHE_DIR / "odd_routine.json"
EVEN_CACHE_FILE = CACHE_DIR / "even_routine.json"
CALENDAR_CACHE_FILE = CACHE_DIR / "calendar.json"

# ---------------------------------------------------------------------------
# Telegram / admin
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()

# Allow comma-separated list for multi-admin (future-proof)
ADMIN_CHAT_IDS: set[int] = {
    int(x.strip())
    for x in ADMIN_CHAT_ID.replace(",", " ").split()
    if x.strip().lstrip("-").isdigit()
}

# ---------------------------------------------------------------------------
# Google Apps Script exec URLs (the three sheet sources)
# ---------------------------------------------------------------------------
ODD_ROUTINE_URL = os.environ.get("ODD_ROUTINE_URL", "").strip()
EVEN_ROUTINE_URL = os.environ.get("EVEN_ROUTINE_URL", "").strip()
CALENDAR_URL = os.environ.get("CALENDAR_URL", "").strip()

# ---------------------------------------------------------------------------
# Notice-board scraping
# ---------------------------------------------------------------------------
TARGET_URL = "https://maldacollege.ac.in/home.php"

try:
    POLL_INTERVAL = int(os.environ.get("INTERVAL", "300"))
    if POLL_INTERVAL < 30:
        POLL_INTERVAL = 30
except ValueError:
    POLL_INTERVAL = 300

# ---------------------------------------------------------------------------
# Routine / calendar behaviour
# ---------------------------------------------------------------------------
# Each routine row only stores a start hour (e.g. "12 PM"). Assume a fixed
# slot duration so /r now and /r next can resolve ongoing/upcoming classes.
try:
    SLOT_DURATION_MIN = int(os.environ.get("SLOT_DURATION_MIN", "60"))
    if SLOT_DURATION_MIN < 15:
        SLOT_DURATION_MIN = 60
except ValueError:
    SLOT_DURATION_MIN = 60

# College week — Monday to Saturday (Sunday is holiday per calendar sheet).
COLLEGE_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT"]

# 24-hour auto-refresh for the cached sheet data
try:
    SHEET_REFRESH_HOURS = float(os.environ.get("SHEET_REFRESH_HOURS", "24"))
except ValueError:
    SHEET_REFRESH_HOURS = 24.0

# Broadcast rate-limiting — Telegram allows ~30 msg/sec globally.
# We stay well below at ~25/sec to be safe.
try:
    BROADCAST_DELAY_SEC = float(os.environ.get("BROADCAST_DELAY_SEC", "0.04"))
except ValueError:
    BROADCAST_DELAY_SEC = 0.04

# Timezone for "today" / "now" resolution. Malda College is in IST.
TZ_NAME = os.environ.get("TZ", "Asia/Kolkata")

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("malda_bot")


def validate_startup() -> list[str]:
    """
    Return a list of human-readable problems with the current configuration.
    Empty list means the bot is safe to start.
    """
    problems: list[str] = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN is not set — get it from @BotFather.")
    if not ADMIN_CHAT_IDS:
        problems.append(
            "ADMIN_CHAT_ID is not set — admin commands (/refreshdata, "
            "/setexception, /clearexception, /listexceptions) will be disabled."
        )
    if not ODD_ROUTINE_URL or not EVEN_ROUTINE_URL or not CALENDAR_URL:
        problems.append(
            "One or more sheet URLs are missing (ODD_ROUTINE_URL, "
            "EVEN_ROUTINE_URL, CALENDAR_URL) — routine features will fall "
            "back to on-disk cache only."
        )
    return problems


def get_dynamic_admins() -> set[int]:
    try:
        import json
        if ADMINS_FILE.exists():
            with open(ADMINS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(int(x) for x in data if str(x).lstrip("-").isdigit())
    except Exception:
        pass
    return set()

def is_admin(chat_id: int | str) -> bool:
    """True if the given chat id is in the admin set."""
    try:
        cid = int(chat_id)
        return cid in ADMIN_CHAT_IDS or cid in get_dynamic_admins()
    except (TypeError, ValueError):
        return False

# ---------------------------------------------------------------------
# Notice slide export (always use this real presentation ID — the
# pub-token link format fails on Google's /export/png endpoint).
# ---------------------------------------------------------------------
NOTICE_PRESENTATION_ID = "10bQD2ed1NYAs7lE8Cg8_IfOqLRZr8CSxxR7221LunCE"
