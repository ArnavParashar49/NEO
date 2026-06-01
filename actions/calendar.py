"""Apple Calendar on macOS — list and add events."""

from __future__ import annotations

import platform
import subprocess
from datetime import datetime, timedelta

_OS = platform.system()
_OSASCRIPT = "/usr/bin/osascript"


def _run_applescript(script: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [_OSASCRIPT, "-e", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
        out = (r.stdout or "").strip()
        if r.returncode == 0:
            return True, out
        return False, (r.stderr or out or f"exit {r.returncode}").strip()
    except Exception as e:
        return False, str(e)


def _parse_dt(date_str: str, time_str: str) -> datetime:
    date_str = date_str.strip()
    time_str = (time_str or "09:00").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(f"{date_str} {time_str[:5]}", fmt)
        except ValueError:
            pass
    raise ValueError(f"Bad date/time: {date_str} {time_str}")


def _apple_date(dt: datetime) -> str:
    return dt.strftime("%A, %B %d, %Y %I:%M:%S %p")


def _list_events(days: int = 1) -> str:
    script = f"""
set timeMin to current date
set hours of timeMin to 0
set minutes of timeMin to 0
set seconds of timeMin to 0
set timeMax to timeMin + ({days} * days)
set output to ""
tell application "Calendar"
  repeat with cal in calendars
    set calName to name of cal
    try
      set evts to (every event of cal whose start date >= timeMin and start date < timeMax)
      repeat with e in evts
        set output to output & calName & " | " & (summary of e) & " | " & (start date of e as string)
        try
          set output to output & " - " & (end date of e as string)
        end try
        set output to output & linefeed
      end repeat
    end try
  end repeat
end tell
return output
"""
    ok, out = _run_applescript(script)
    if not ok:
        return f"FAILED: Could not read calendar — {out}"
    if not out:
        span = "today" if days <= 1 else f"the next {days} days"
        return f"No events {span}."
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return f"Events ({len(lines)}):\n" + "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, 1))


def _list_calendars() -> str:
    script = """
tell application "Calendar"
  set names to name of every calendar
  return names as string
end tell
"""
    ok, out = _run_applescript(script)
    if not ok:
        return f"FAILED: {out}"
    return "Calendars: " + out.replace(", ", ", ")


def _add_event(
    title: str,
    date_str: str,
    start_time: str,
    end_time: str = "",
    duration_minutes: int = 60,
    calendar: str = "Home",
    notes: str = "",
    location: str = "",
) -> str:
    start = _parse_dt(date_str, start_time)
    if end_time:
        end = _parse_dt(date_str, end_time)
    else:
        end = start + timedelta(minutes=max(15, int(duration_minutes or 60)))

    title_safe = title.replace('"', '\\"')[:200]
    cal_safe = calendar.replace('"', '\\"')
    notes_safe = notes.replace('"', '\\"')[:500]
    loc_safe = location.replace('"', '\\"')[:200]

    props = [
        f'summary:"{title_safe}"',
        f'start date:date "{_apple_date(start)}"',
        f'end date:date "{_apple_date(end)}"',
    ]
    if notes_safe:
        props.append(f'description:"{notes_safe}"')
    if loc_safe:
        props.append(f'location:"{loc_safe}"')

    script = f"""
tell application "Calendar"
  set targetCal to calendar "{cal_safe}"
  tell targetCal
    make new event with properties {{{", ".join(props)}}}
  end tell
end tell
return "ok"
"""
    ok, out = _run_applescript(script)
    if not ok:
        # Retry default calendar
        script2 = script.replace(f'calendar "{cal_safe}"', "calendar 1")
        ok, out = _run_applescript(script2)
    if not ok:
        return f"FAILED: Could not add event — {out}"
    return (
        f"Added to calendar: {title} on {date_str} at {start_time[:5]} "
        f"until {end.strftime('%H:%M')}."
    )


def _open_calendar_app() -> str:
    try:
        subprocess.run(["open", "-a", "Calendar"], check=False, timeout=5)
        return "Opened Calendar app."
    except Exception as e:
        return f"FAILED: {e}"


def calendar_control(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = (params.get("action") or "list_today").lower().strip()

    if _OS != "Darwin":
        return "Calendar control is only supported on macOS (Apple Calendar)."

    print(f"[Calendar] {action} {params}")

    if action in ("list_today", "today", "list"):
        return _list_events(days=1)

    if action in ("list_tomorrow", "tomorrow"):
        # Shift window to tomorrow only via AppleScript offset
        script = """
set timeMin to (current date) + (1 * days)
set hours of timeMin to 0
set minutes of timeMin to 0
set seconds of timeMin to 0
set timeMax to timeMin + (1 * days)
set output to ""
tell application "Calendar"
  repeat with cal in calendars
    set calName to name of cal
    try
      set evts to (every event of cal whose start date >= timeMin and start date < timeMax)
      repeat with e in evts
        set output to output & calName & " | " & (summary of e) & " | " & (start date of e as string) & linefeed
      end repeat
    end try
  end repeat
end tell
return output
"""
        ok, out = _run_applescript(script)
        if not ok:
            return f"FAILED: {out}"
        if not out.strip():
            return "No events tomorrow."
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return "Tomorrow:\n" + "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, 1))

    if action in ("list_week", "week"):
        return _list_events(days=7)

    if action in ("list_calendars", "calendars"):
        return _list_calendars()

    if action in ("add", "create", "schedule"):
        title = (params.get("title") or params.get("summary") or "").strip()
        if not title:
            return "NEEDS_USER: What should the event be called?"
        date_str = (params.get("date") or "").strip()
        if not date_str:
            return "NEEDS_USER: What date? Use YYYY-MM-DD."
        start_time = (params.get("start_time") or params.get("time") or "09:00").strip()
        return _add_event(
            title=title,
            date_str=date_str,
            start_time=start_time,
            end_time=(params.get("end_time") or "").strip(),
            duration_minutes=int(params.get("duration_minutes") or 60),
            calendar=(params.get("calendar") or "Home").strip(),
            notes=(params.get("notes") or params.get("description") or "").strip(),
            location=(params.get("location") or "").strip(),
        )

    if action == "open":
        return _open_calendar_app()

    return (
        f"Unknown action '{action}'. Use: list_today | list_tomorrow | list_week | "
        "add | list_calendars | open"
    )
