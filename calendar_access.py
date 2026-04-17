"""
SHADOW Calendar Access — cross-platform calendar integration.

macOS: Apple Calendar via AppleScript.
Windows: Outlook COM automation via win32com, or graceful fallback.
"""

import asyncio
import logging
import os
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

from platform_utils import IS_WINDOWS, IS_MACOS

log = logging.getLogger("shadow.calendar")

_calendar_accounts_env = os.getenv("CALENDAR_ACCOUNTS", "")
USER_CALENDARS: list[str] = [
    a.strip() for a in _calendar_accounts_env.split(",") if a.strip()
] if _calendar_accounts_env.strip() else []

_auto_discovered = False
_event_cache: list[dict] = []
_cache_time: float = 0
_calendar_launched = False
_outlook_available = False

if IS_WINDOWS:
    try:
        import win32com.client
        _outlook_available = True
    except ImportError:
        log.info("pywin32 not installed — Outlook calendar unavailable")


# --- Windows: Outlook COM ---

async def _fetch_outlook_events() -> list[dict]:
    if not _outlook_available:
        return []
    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch_outlook_events_sync)
    except Exception as e:
        log.warning(f"Outlook calendar fetch failed: {e}")
        return []

def _fetch_outlook_events_sync() -> list[dict]:
    import win32com.client, pythoncom
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        ns = outlook.GetNamespace("MAPI")
        cal = ns.GetDefaultFolder(9)
        items = cal.Items
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=23, minute=59, second=59, microsecond=0)
        items.Sort("[Start]")
        items.IncludeRecurrences = True
        filt = items.Restrict(
            f"[Start] >= '{start.strftime('%m/%d/%Y')}' AND [Start] <= '{end.strftime('%m/%d/%Y 11:59 PM')}'"
        )
        events = []
        for item in filt:
            try:
                sdt = datetime(item.Start.year, item.Start.month, item.Start.day, item.Start.hour, item.Start.minute)
                ad = item.AllDayEvent
                events.append({"calendar": "Outlook", "title": item.Subject or "(No Subject)",
                               "start": "ALL_DAY" if ad else sdt.strftime("%I:%M %p").lstrip("0"),
                               "start_dt": sdt, "all_day": ad})
            except Exception:
                continue
        return events
    except Exception as e:
        log.warning(f"Outlook COM error: {e}")
        return []
    finally:
        pythoncom.CoUninitialize()


# --- macOS: AppleScript ---

_BULK_SCRIPT = '''tell application "Calendar"
    set cal to calendar "{cal_name}"
    set dateList to start date of every event of cal
    set summaryList to summary of every event of cal
    set allDayList to allday event of every event of cal
    set output to ""
    repeat with i from 1 to count of dateList
        set output to output & ((item i of dateList) as string) & "|||" & (item i of summaryList) & "|||" & (item i of allDayList) & linefeed
    end repeat
    return output
end tell'''

async def _ensure_calendar_running():
    global _calendar_launched
    if _calendar_launched or IS_WINDOWS:
        return
    try:
        proc = await asyncio.create_subprocess_exec("open", "-a", "Calendar", "-g",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(proc.communicate(), timeout=5)
        await asyncio.sleep(2)
        _calendar_launched = True
    except Exception as e:
        log.warning(f"Failed to launch Calendar: {e}")

async def _fetch_calendar_events(cal_name: str, timeout: float = 12.0) -> list[dict]:
    script = _BULK_SCRIPT.replace("{cal_name}", cal_name)
    try:
        proc = await asyncio.create_subprocess_exec("osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return []
        raw = stdout.decode().strip()
        if not raw:
            return []
        now = datetime.now()
        today_date = now.date()
        events = []
        for line in raw.split("\n"):
            parts = line.strip().split("|||")
            if len(parts) < 3:
                continue
            try:
                parsed = _parse_applescript_date(parts[0].strip())
                if parsed and parsed.date() == today_date:
                    ad = parts[2].strip().lower() == "true"
                    events.append({"calendar": cal_name, "title": parts[1].strip(),
                        "start": "ALL_DAY" if ad else parsed.strftime("%-I:%M %p"),
                        "start_dt": parsed, "all_day": ad})
            except Exception:
                continue
        return events
    except asyncio.TimeoutError:
        return []
    except Exception:
        return []

def _parse_applescript_date(s: str) -> datetime | None:
    if ", " in s:
        s = s.split(", ", 1)[1]
    for fmt in ["%B %d, %Y at %I:%M:%S %p", "%B %d, %Y at %H:%M:%S"]:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


# --- Public API ---

async def refresh_cache():
    global _event_cache, _cache_time, USER_CALENDARS, _auto_discovered
    if IS_WINDOWS:
        events = await _fetch_outlook_events() if _outlook_available else []
        events.sort(key=lambda e: (not e["all_day"], e.get("start_dt") or datetime.max))
        _event_cache = events
        _cache_time = _time.time()
        log.info(f"Calendar cache refreshed: {len(events)} events today")
        return
    await _ensure_calendar_running()
    if not USER_CALENDARS and not _auto_discovered:
        _auto_discovered = True
        discovered = await get_calendar_names()
        if discovered:
            USER_CALENDARS = discovered
            log.info(f"Auto-discovered calendars: {USER_CALENDARS}")
        else:
            return
    if not USER_CALENDARS:
        return
    all_events = []
    for i in range(0, len(USER_CALENDARS), 2):
        batch = USER_CALENDARS[i:i+2]
        results = await asyncio.gather(*[_fetch_calendar_events(c) for c in batch], return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                all_events.extend(r)
    all_events.sort(key=lambda e: (not e["all_day"], e.get("start_dt") or datetime.max))
    _event_cache = all_events
    _cache_time = _time.time()

async def get_todays_events() -> list[dict]:
    if not _event_cache and _cache_time == 0:
        await refresh_cache()
    return _event_cache

async def get_upcoming_events(hours: int = 4) -> list[dict]:
    events = await get_todays_events()
    now = datetime.now()
    cutoff = now + timedelta(hours=hours)
    return [e for e in events if not e["all_day"] and e.get("start_dt") and now <= e["start_dt"] <= cutoff]

async def get_next_event() -> dict | None:
    events = await get_upcoming_events(hours=24)
    return events[0] if events else None

async def get_calendar_names() -> list[str]:
    if IS_WINDOWS:
        return ["Outlook"] if _outlook_available else []
    await _ensure_calendar_running()
    try:
        proc = await asyncio.create_subprocess_exec("osascript", "-e",
            'tell application "Calendar" to return name of every calendar',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            return [c.strip() for c in stdout.decode().strip().split(",") if c.strip()]
    except Exception:
        pass
    return []

def format_events_for_context(events: list[dict]) -> str:
    if not events:
        return "No events scheduled today."
    lines = []
    for evt in events:
        entry = f"  All day — {evt['title']}" if evt.get("all_day") else f"  {evt['start']} — {evt['title']}"
        if evt.get("calendar"):
            entry += f" [{evt['calendar']}]"
        lines.append(entry)
    return "\n".join(lines)

def format_schedule_summary(events: list[dict]) -> str:
    if not events:
        return "Your schedule is clear today, sir."
    count = len(events)
    if count == 1:
        evt = events[0]
        if evt.get("all_day"):
            return f"You have one all-day event: {evt['title']}."
        return f"You have one event: {evt['title']} at {evt['start']}."
    summaries = []
    for evt in events[:5]:
        summaries.append(f"{evt['title']} all day" if evt.get("all_day") else f"{evt['title']} at {evt['start']}")
    result = f"You have {count} events today. " + ". ".join(summaries[:3])
    if count > 3:
        result += f". And {count - 3} more."
    return result
