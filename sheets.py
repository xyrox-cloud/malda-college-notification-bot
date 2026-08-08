"""
Google Apps Script sheet fetcher + parser + in-memory cache.

Three sources (configured in config.py):
  - Odd Semester Routine   -> rows of {sem, course, sub, day, time, room, teacher}
  - Even Semester Routine  -> same shape
  - Academic Calendar      -> rows of {date, day, cod/hld, cld/ued, remarks}

The Apps Script web apps may return either:
  (a) a JSON array of row objects / a DataTable-style payload, OR
  (b) an HTML page with a <table> (as seen in the reference screenshots).

This module auto-detects the format and normalizes to the shapes above.
On startup it loads the on-disk cache (if any) so the bot works offline,
then refreshes from the network in the background.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

import config
import storage


# ---------------------------------------------------------------------------
# UG paper-type tag -> which chosen subject it ties to (see classes_for_subjects)
# ---------------------------------------------------------------------------
UG_TIE_TO_MJ = {"MJ", "TUT", "SEC", "IAPC", "REM", "VEC", "PRT", "PRT-MJ"}
UG_TIE_TO_MN = {"MN", "PRT-MN"}
UG_MDC_TAGS = {"MDC"}


# ---------------------------------------------------------------------------
# Date normalization
# ---------------------------------------------------------------------------

# Strips an optional weekday prefix ("Sun, " / "Sunday, ") from a date string.
_WEEKDAY_PREFIX_RE = re.compile(
    r"^(?:Sun|Mon|Tue|Wed|Thu|Fri|Sat)[a-z]*,?\s*",
    re.IGNORECASE,
)


def _normalize_calendar_date(raw: str) -> str:
    """
    Normalize a calendar date string to "DD Mon YYYY" (e.g. "15 Nov 2026").

    Handles:
      - "Sun, 15 Nov 2026"  -> "15 Nov 2026"
      - "Sunday, 15 Nov 2026" -> "15 Nov 2026"
      - "15 Nov 2026"       -> "15 Nov 2026"  (already clean)
      - "15 November 2026"  -> "15 Nov 2026"
      - "15/08/2026"        -> "15 Aug 2026"
      - "2026-08-15"        -> "15 Aug 2026"

    Returns the original string if no format matches (so the caller can debug).
    """
    if not raw:
        return ""
    s = raw.strip()
    # Strip weekday prefix if present
    s = _WEEKDAY_PREFIX_RE.sub("", s).strip()
    # Normalize non-standard month spellings seen in the college's sheets
    # (e.g. "Sept" instead of "Sep") so strptime("%d %b %Y") can parse them.
    s = re.sub(r"\bSept\b", "Sep", s, flags=re.IGNORECASE)
    # Try textual month formats
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    # Try numeric formats
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    return s

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

class SheetsCache:
    """Holds the parsed contents of all three sources, plus timestamps."""

    def __init__(self) -> None:
        self.odd_routine: list[dict] = []
        self.even_routine: list[dict] = []
        self.calendar: list[dict] = []
        self.last_refresh: datetime | None = None
        self._lock = asyncio.Lock()

    # --- accessors --------------------------------------------------------

    def routine_for_sem(self, sem: int | str | None) -> list[dict]:
        """Pick the odd or even sheet based on semester parity."""
        if sem is None or str(sem).strip() == "":
            return []
        try:
            sem_int = int(sem)
        except (TypeError, ValueError):
            return []
        rows = self.odd_routine if sem_int % 2 == 1 else self.even_routine
        return [r for r in rows if str(r.get("sem")) == str(sem_int)]

    def available_courses(self, sem: int | str) -> list[str]:
        """Unique COURSE values for a semester, sorted."""
        seen: set[str] = set()
        for r in self.routine_for_sem(sem):
            c = str(r.get("course", "")).strip()
            if c:
                seen.add(c)
        return sorted(seen)

    def available_subjects(self, sem: int | str, course: str) -> list[str]:
        """Unique SUB values for a semester + course, sorted."""
        seen: set[str] = set()
        for r in self.routine_for_sem(sem):
            if str(r.get("course", "")).strip() == course:
                s = str(r.get("sub", "")).strip()
                if s:
                    seen.add(s)
        return sorted(seen)

    def available_categories(self, sem: int | str) -> list[str]:
        """
        Unique COURSE-tag values for a semester, sorted — e.g. MJ, MN, MDC,
        TUT, SEC, IAPC, PRT, PRT-MJ, PRT-MN, REM, VEC, PG1/PG2/PG4/...

        NOTE: despite the field name, "course" in this sheet is a paper-type
        tag, not a degree/programme; "sub" is the actual subject/department
        (e.g. PHYSICS, HISTORY). See classes_for_subjects() for how tags map
        to a student's MJ/MN/MDC/PG subject choices.
        """
        seen: set[str] = set()
        for r in self.routine_for_sem(sem):
            c = str(r.get("course", "")).strip()
            if c:
                seen.add(c)
        return sorted(seen)

    def classes_for(self, sem, course, sub, day: str | None = None) -> list[dict]:
        """Filtered routine rows; optionally restricted to a weekday (MON..SAT)."""
        out: list[dict] = []
        for r in self.routine_for_sem(sem):
            if str(r.get("course", "")).strip() != str(course).strip():
                continue
            if str(r.get("sub", "")).strip() != str(sub).strip():
                continue
            if day is not None and str(r.get("day", "")).strip().upper() != day.upper():
                continue
            out.append(r)
        # Sort by parsed start time (hour)
        out.sort(key=lambda r: _parse_hour(r.get("time", "")))
        return out

    def all_classes_for_sem(self, sem, day: str | None = None) -> list[dict]:
        """Full, unfiltered routine for a semester — used as the fallback for
        users who haven't completed subject selection yet."""
        rows = self.routine_for_sem(sem)
        if day is not None:
            rows = [r for r in rows if str(r.get("day", "")).strip().upper() == day.upper()]
        rows = list(rows)
        rows.sort(key=lambda r: _parse_hour(r.get("time", "")))
        return rows

    def classes_for_subjects(
        self, sem, track: str, subjects: dict, day: str | None = None
    ) -> list[dict]:
        """
        Filtered routine rows for a student's MJ/MN/MDC (UG) or PG-subject
        selection. See module docstring in the MJ/MN/MDC addendum for the
        category -> subject tie-mapping rationale.

        UG tie-mapping (paper-type tag -> which chosen subject it belongs to):
          MJ, TUT, SEC, IAPC, REM, VEC, PRT, PRT-MJ  -> student's MJ subject
          MN, PRT-MN                                  -> student's MN subject
          MDC                                         -> any of student's MDC subject(s)
          any other/unrecognized tag                  -> defaults to MJ subject
            (so a newly-appearing tag on the sheet doesn't silently vanish;
            adjust UG_TIE_TO_MJ/UG_TIE_TO_MN below if a college admin says
            it should tie elsewhere)

        PG: subjects is {pg_category_tag: subject}, e.g. {"PG2": "PG HISTORY"}.
        """
        track = (track or "").upper()
        subjects = subjects or {}
        out: list[dict] = []
        for r in self.routine_for_sem(sem):
            if day is not None and str(r.get("day", "")).strip().upper() != day.upper():
                continue
            cat = str(r.get("course", "")).strip().upper()
            sub = str(r.get("sub", "")).strip()

            if track == "PG":
                wanted = subjects.get(cat)
                if wanted and sub == wanted:
                    out.append(r)
                continue

            # UG
            mj = subjects.get("MJ")
            mn = subjects.get("MN")
            mdc_list = subjects.get("MDC") or []
            if cat in UG_TIE_TO_MN:
                if mn and sub == mn:
                    out.append(r)
            elif cat in UG_MDC_TAGS:
                if sub in mdc_list:
                    out.append(r)
            else:
                # MJ, and any tag not explicitly mapped above (TUT, SEC, IAPC,
                # REM, VEC, PRT, PRT-MJ, or anything new on the sheet).
                if mj and sub == mj:
                    out.append(r)
        out.sort(key=lambda r: _parse_hour(r.get("time", "")))
        return out

    def calendar_for_date(self, date_key: str) -> dict | None:
        """Find the calendar row matching "DD Mon YYYY"."""
        for r in self.calendar:
            if str(r.get("date", "")).strip() == date_key:
                return r
        return None

    def is_empty(self) -> bool:
        return not (self.odd_routine or self.even_routine or self.calendar)


CACHE = SheetsCache()


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------

async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    """
    Apps Script exec URLs typically follow redirects to googleusercontent.com.
    We let aiohttp follow redirects and return the body as text.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/37.36"
        ),
    }
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
        resp.raise_for_status()
        return await resp.text()


# ---------------------------------------------------------------------------
# Format detection + parsing
# ---------------------------------------------------------------------------

def _looks_like_json(text: str) -> bool:
    s = text.lstrip()
    return s.startswith("[") or s.startswith("{")


def _parse_routine_payload(text: str) -> list[dict]:
    """
    Normalize an Apps Script payload (JSON or HTML) into routine rows.
    Output row shape: {sem, course, sub, day, time, room, teacher}
    """
    if _looks_like_json(text):
        rows = _parse_routine_json(text)
        if rows:
            return rows
    # fall through to HTML scraping
    return _parse_routine_html(text)


def _parse_calendar_payload(text: str) -> list[dict]:
    if _looks_like_json(text):
        rows = _parse_calendar_json(text)
        if rows:
            return rows
    return _parse_calendar_html(text)


# --- JSON variants --------------------------------------------------------

def _parse_routine_json(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    # Accept either a list of rows, or {"rows": [...]} / {"data": [...]}
    if isinstance(data, dict):
        rows = data.get("rows") or data.get("data") or data.get("values") or []
    elif isinstance(data, list):
        rows = data
    else:
        return []
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, (dict, list)):
            continue
        if isinstance(r, list):
            # positional: [sem, course, sub, day, time, room, teacher]
            if len(r) < 5:
                continue
            out.append({
                "sem": str(r[0]).strip(),
                "course": str(r[1]).strip(),
                "sub": str(r[2]).strip(),
                "day": str(r[3]).strip().upper(),
                "time": str(r[4]).strip(),
                "room": str(r[5]).strip() if len(r) > 5 else "",
                "teacher": str(r[6]).strip() if len(r) > 6 else "",
            })
        else:
            out.append({
                "sem": str(r.get("sem") or r.get("SEM") or r.get("Semester") or "").strip(),
                "course": str(r.get("course") or r.get("C") or r.get("COURSE") or r.get("Course") or "").strip(),
                "sub": str(r.get("sub") or r.get("SUB") or r.get("Subject") or r.get("subject") or "").strip(),
                "day": str(r.get("day") or r.get("DAY") or r.get("Day") or "").strip().upper(),
                "time": str(r.get("time") or r.get("TIME") or r.get("Time") or "").strip(),
                "room": str(r.get("room") or r.get("R") or r.get("Room") or "").strip(),
                "teacher": str(r.get("teacher") or r.get("T") or r.get("Teacher") or "").strip(),
            })
    return [r for r in out if r["sem"] and r["day"] and r["time"]]


def _parse_calendar_json(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        rows = data.get("rows") or data.get("data") or data.get("values") or []
    elif isinstance(data, list):
        rows = data
    else:
        return []
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, (dict, list)):
            continue
        if isinstance(r, list):
            if len(r) < 3:
                continue
            out.append({
                "date": _normalize_calendar_date(str(r[0])),
                "open_holiday": str(r[1]).strip().upper(),
                "class_exam": str(r[2]).strip().upper(),
                "remarks": str(r[3]).strip() if len(r) > 3 else "",
            })
        else:
            out.append({
                "date": _normalize_calendar_date(str(r.get("date") or r.get("DATE") or r.get("Date") or "")),
                "open_holiday": str(r.get("open_holiday") or r.get("cod_hld") or r.get("COD_HLD") or r.get("status") or "").strip().upper(),
                "class_exam": str(r.get("class_exam") or r.get("cld_ued") or r.get("CLD_UED") or "").strip().upper(),
                "remarks": str(r.get("remarks") or r.get("REMARKS") or r.get("Remarks") or "").strip(),
            })
    return [r for r in out if r["date"]]


# --- HTML variants (matches the column layout visible in the reference PDFs) ----

_ROUTINE_HEADERS = {"sem", "c", "course", "sub", "day", "time", "r", "t"}
_CALENDAR_HEADERS = {"date", "cod", "hld", "cld", "ued", "remarks"}


def _parse_routine_html(text: str) -> list[dict]:
    soup = BeautifulSoup(text, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    rows = table.find_all("tr")
    if not rows:
        return []
    # Locate the header row
    header_cells = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
    # Map header -> index. Headers in screenshots: SEM | C | SUB | DAY | TIME | R | T
    idx = {}
    for i, h in enumerate(header_cells):
        if h in _ROUTINE_HEADERS:
            # Prefer the first occurrence of each semantic column
            key = "course" if h in ("c", "course") else \
                  "room" if h == "r" else \
                  "teacher" if h == "t" else h
            idx.setdefault(key, i)
    out: list[dict] = []
    for tr in rows[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 5:
            continue
        rec = {
            "sem": cells[idx["sem"]] if "sem" in idx and idx["sem"] < len(cells) else "",
            "course": cells[idx["course"]] if "course" in idx and idx["course"] < len(cells) else "",
            "sub": cells[idx["sub"]] if "sub" in idx and idx["sub"] < len(cells) else "",
            "day": (cells[idx["day"]] if "day" in idx and idx["day"] < len(cells) else "").upper(),
            "time": cells[idx["time"]] if "time" in idx and idx["time"] < len(cells) else "",
            "room": cells[idx["room"]] if "room" in idx and idx["room"] < len(cells) else "",
            "teacher": cells[idx["teacher"]] if "teacher" in idx and idx["teacher"] < len(cells) else "",
        }
        if rec["sem"] and rec["day"] and rec["time"]:
            out.append(rec)
    return out


def _parse_calendar_html(text: str) -> list[dict]:
    soup = BeautifulSoup(text, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    rows = table.find_all("tr")
    if not rows:
        return []
    header_cells = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
    idx: dict[str, int] = {}
    for i, h in enumerate(header_cells):
        # Header in screenshots: DATE | COD/HLD | CLD/UED | REMARKS
        # The split headers may appear as single cells like "College Open (COD) & Holidays(HLD)"
        if "date" in h and "date" not in idx:
            idx["date"] = i
        elif ("cod" in h or "hld" in h or "holiday" in h or "open" in h) and "open_holiday" not in idx:
            idx["open_holiday"] = i
        elif ("cld" in h or "ued" in h or "exam" in h or "class day" in h) and "class_exam" not in idx:
            idx["class_exam"] = i
        elif "remark" in h and "remarks" not in idx:
            idx["remarks"] = i
    out: list[dict] = []
    for tr in rows[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if not cells:
            continue
        raw_date = cells[idx["date"]] if "date" in idx and idx["date"] < len(cells) else cells[0]
        rec = {
            "date": _normalize_calendar_date(raw_date),
            "open_holiday": (cells[idx["open_holiday"]] if "open_holiday" in idx and idx["open_holiday"] < len(cells) else "").upper(),
            "class_exam": (cells[idx["class_exam"]] if "class_exam" in idx and idx["class_exam"] < len(cells) else "").upper(),
            "remarks": cells[idx["remarks"]] if "remarks" in idx and idx["remarks"] < len(cells) else "",
        }
        if rec["date"]:
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

def _parse_hour(time_str: str) -> int:
    """
    Parse a time string like "11 AM", "12 PM", "2 PM", "09:00" into a 24h int.
    Used for sorting /r output and resolving /r now & /r next.
    """
    s = (time_str or "").strip().upper()
    if not s:
        return -1
    # 12-hour with AM/PM
    m = re.match(r"^(\d{1,2})\s*(AM|PM)$", s)
    if m:
        h = int(m.group(1)) % 12
        if m.group(2) == "PM":
            h += 12
        return h
    # 24-hour HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        return int(m.group(1))
    # bare hour
    m = re.match(r"^(\d{1,2})$", s)
    if m:
        return int(m.group(1))
    return -1


def time_to_hour_minute(time_str: str) -> tuple[int, int]:
    """Return (hour24, minute) for a routine TIME value."""
    s = (time_str or "").strip().upper()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?$", s)
    if not m:
        return (-1, 0)
    h = int(m.group(1)) % 12
    minute = int(m.group(2) or 0)
    suffix = m.group(3)
    if suffix == "PM":
        h += 12
    # If no AM/PM and hour looks 12-hour (<=7), assume PM for college afternoon classes
    if suffix is None and 1 <= h <= 7:
        # ambiguous; leave as-is
        pass
    return (h, minute)


# ---------------------------------------------------------------------------
# Refresh + on-disk fallback
# ---------------------------------------------------------------------------

async def _refresh_one(session: aiohttp.ClientSession, url: str, kind: str) -> list[dict]:
    if not url:
        config.logger.warning("No URL configured for %s — skipping.", kind)
        return []
    try:
        text = await _fetch_text(session, url)
    except Exception as exc:  # noqa: BLE001
        config.logger.error("Fetch failed for %s: %s", kind, exc)
        return []
    if kind == "calendar":
        return _parse_calendar_payload(text)
    return _parse_routine_payload(text)


def _load_disk_cache() -> None:
    """Populate the in-memory cache from on-disk JSON (best-effort)."""
    if config.ODD_CACHE_FILE.exists():
        try:
            CACHE.odd_routine = json.loads(config.ODD_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    if config.EVEN_CACHE_FILE.exists():
        try:
            CACHE.even_routine = json.loads(config.EVEN_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    if config.CALENDAR_CACHE_FILE.exists():
        try:
            CACHE.calendar = json.loads(config.CALENDAR_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    config.logger.info(
        "Loaded on-disk cache: odd=%d even=%d calendar=%d",
        len(CACHE.odd_routine), len(CACHE.even_routine), len(CACHE.calendar),
    )


def _persist_cache() -> None:
    try:
        config.ODD_CACHE_FILE.write_text(json.dumps(CACHE.odd_routine, ensure_ascii=False), encoding="utf-8")
        config.EVEN_CACHE_FILE.write_text(json.dumps(CACHE.even_routine, ensure_ascii=False), encoding="utf-8")
        config.CALENDAR_CACHE_FILE.write_text(json.dumps(CACHE.calendar, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        config.logger.error("Failed to persist cache: %s", exc)


async def refresh_all(force: bool = False) -> dict[str, int]:
    """
    Re-fetch all three sources from the network and update the cache.
    Returns a small summary dict {odd, even, calendar}.
    """
    async with CACHE._lock:
        async with aiohttp.ClientSession() as session:
            odd, even, cal = await asyncio.gather(
                _refresh_one(session, config.ODD_ROUTINE_URL, "odd_routine"),
                _refresh_one(session, config.EVEN_ROUTINE_URL, "even_routine"),
                _refresh_one(session, config.CALENDAR_URL, "calendar"),
            )
        if odd:
            CACHE.odd_routine = odd
        if even:
            CACHE.even_routine = even
        if cal:
            CACHE.calendar = cal
        CACHE.last_refresh = datetime.now(timezone.utc)
        _persist_cache()
        # Also prune past exceptions while we're here
        pruned = storage.prune_past_exceptions()
        if pruned:
            config.logger.info("Pruned %d past exception(s).", pruned)
        config.logger.info(
            "Sheet refresh complete: odd=%d even=%d calendar=%d",
            len(CACHE.odd_routine), len(CACHE.even_routine), len(CACHE.calendar),
        )
        return {
            "odd": len(CACHE.odd_routine),
            "even": len(CACHE.even_routine),
            "calendar": len(CACHE.calendar),
        }


def init_from_disk_or_empty() -> None:
    """Called on startup — load disk cache so the bot has data even offline."""
    _load_disk_cache()


async def auto_refresh_loop() -> None:
    """Background task: refresh sheet data every SHEET_REFRESH_HOURS."""
    if config.SHEET_REFRESH_HOURS <= 0:
        return
    interval = config.SHEET_REFRESH_HOURS * 3600
    while True:
        try:
            await asyncio.sleep(interval)
            await refresh_all()
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001
            config.logger.error("Auto-refresh error: %s", exc)
            await asyncio.sleep(60)
