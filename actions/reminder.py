"""Timed reminders and alarms — system notifications, launchd, and Apple Reminders."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

_OSASCRIPT = "/usr/bin/osascript"
_MAC_SOUNDS = [
    "/System/Library/Sounds/Ping.aiff",
    "/System/Library/Sounds/Glass.aiff",
    "/System/Library/Sounds/Submarine.aiff",
]


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _get_os() -> str:
    from config import get_os
    return get_os()


def _scripts_dir() -> Path:
    d = Path.home() / ".aria" / "reminders"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jobs_meta_path() -> Path:
    return _scripts_dir() / "jobs.json"


def _load_jobs() -> list[dict]:
    path = _jobs_meta_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_jobs(jobs: list[dict]) -> None:
    _jobs_meta_path().write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _add_job(job_id: str, target_dt: datetime, message: str, job_type: str) -> None:
    jobs = _load_jobs()
    jobs.append(
        {
            "id": job_id,
            "when": target_dt.isoformat(timespec="minutes"),
            "message": message,
            "type": job_type,
        }
    )
    _save_jobs(jobs)


def _remove_job(job_id: str) -> bool:
    jobs = _load_jobs()
    kept = [j for j in jobs if j.get("id") != job_id]
    if len(kept) == len(jobs):
        return False
    _save_jobs(kept)
    return True


def _sanitise(text: str, max_len: int = 200) -> str:
    return (
        text.replace("\\", "")
        .replace('"', "")
        .replace("'", "")
        .replace("\n", " ")
        .replace("\r", "")
        .strip()
    )[:max_len]


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


def _apple_date(dt: datetime) -> str:
    return dt.strftime("%A, %B %d, %Y %I:%M:%S %p")


def _resolve_target_time(params: dict) -> tuple[datetime | None, str]:
    """Return (target_dt, error_message)."""
    date_str = (params.get("date") or "").strip()
    time_str = (params.get("time") or "").strip()
    in_minutes = params.get("in_minutes")
    in_hours = params.get("in_hours")

    if in_minutes is not None or in_hours is not None:
        try:
            mins = int(in_minutes or 0) + int(in_hours or 0) * 60
        except (TypeError, ValueError):
            return None, "in_minutes and in_hours must be numbers."
        if mins <= 0:
            return None, "Reminder time must be in the future."
        return datetime.now() + timedelta(minutes=mins), ""

    if not date_str or not time_str:
        return None, "I need a date and time, or in_minutes / in_hours."

    try:
        target_dt = datetime.strptime(f"{date_str} {time_str[:5]}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None, "Use date YYYY-MM-DD and time HH:MM (24h), or in_minutes."

    if target_dt <= datetime.now():
        return None, "That time has already passed — pick a future time."

    return target_dt, ""


def _write_notify_script(
    task_name: str, message: str, os_name: str, alarm: bool = False
) -> Path:
    script_path = _scripts_dir() / f"{task_name}.py"
    msg_literal = json.dumps(message)

    alarm_block = ""
    if alarm and os_name == "mac":
        sound = next((s for s in _MAC_SOUNDS if Path(s).exists()), "")
        if sound:
            alarm_block = f"""
try:
    import subprocess, time
    for _ in range(3):
        subprocess.run(["afplay", {json.dumps(sound)}], check=False)
        time.sleep(0.35)
except Exception:
    pass
"""
    elif alarm and os_name == "windows":
        alarm_block = """
try:
    import winsound, time
    for freq in [800, 1000, 1200, 1000, 800]:
        winsound.Beep(freq, 220)
        time.sleep(0.08)
except Exception:
    pass
"""

    if os_name == "windows":
        notify_block = f"""
message = {msg_literal}
notified = False

try:
    from plyer import notification
    notification.notify(title="ARIA Reminder", message=message, timeout=15)
    notified = True
except Exception:
    pass

if not notified:
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast("ARIA Reminder", message, duration=15, threaded=False)
        notified = True
    except Exception:
        pass

if not notified:
    try:
        import subprocess
        subprocess.run(["msg", "*", "/TIME:30", message], check=False)
    except Exception:
        pass
{alarm_block}
"""

    elif os_name == "mac":
        title = "ARIA Alarm" if alarm else "ARIA Reminder"
        notify_block = f"""
message = {msg_literal}
notified = False

try:
    from plyer import notification
    notification.notify(title={json.dumps(title)}, message=message, timeout=15)
    notified = True
except Exception:
    pass

if not notified:
    try:
        import subprocess
        script = 'display notification "{{}}" with title {json.dumps(title)}'.format(
            message.replace('"', '')
        )
        subprocess.run(["osascript", "-e", script], check=False)
    except Exception:
        pass
{alarm_block}
"""

    else:
        notify_block = f"""
message = {msg_literal}
notified = False

try:
    from plyer import notification
    notification.notify(title="ARIA Reminder", message=message, timeout=15)
    notified = True
except Exception:
    pass

if not notified:
    try:
        import subprocess
        subprocess.run(
            ["notify-send", "--urgency=normal", "--expire-time=15000",
             "ARIA Reminder", message],
            check=False
        )
    except Exception:
        pass
"""

    script_body = f"""# Auto-generated by ARIA reminder — do not edit
import sys, os, pathlib
{notify_block}
try:
    pathlib.Path(__file__).unlink(missing_ok=True)
except Exception:
    pass
"""
    script_path.write_text(script_body, encoding="utf-8")
    script_path.chmod(0o600)
    return script_path


def _schedule_windows(
    target_dt: datetime, task_name: str, script_path: Path, message: str
) -> str:
    python_exe = Path(sys.executable)
    pythonw = python_exe.parent / "pythonw.exe"
    if pythonw.exists():
        python_exe = pythonw

    xml_path = _scripts_dir() / f"{task_name}.xml"
    xml_content = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <RegistrationInfo><Description>ARIA Reminder</Description></RegistrationInfo>\n'
        '  <Triggers><TimeTrigger>\n'
        f'    <StartBoundary>{target_dt.strftime("%Y-%m-%dT%H:%M:%S")}</StartBoundary>\n'
        '    <Enabled>true</Enabled>\n'
        '  </TimeTrigger></Triggers>\n'
        '  <Actions><Exec>\n'
        f'    <Command>{python_exe}</Command>\n'
        f'    <Arguments>"{script_path}"</Arguments>\n'
        '  </Exec></Actions>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <StartWhenAvailable>true</StartWhenAvailable>\n'
        '    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>\n'
        '    <Enabled>true</Enabled>\n'
        '  </Settings>\n'
        '  <Principals><Principal>\n'
        '    <LogonType>InteractiveToken</LogonType>\n'
        '    <RunLevel>LeastPrivilege</RunLevel>\n'
        '  </Principal></Principals>\n'
        '</Task>'
    )

    xml_path.write_text(xml_content, encoding="utf-16")

    result = subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
        capture_output=True,
        text=True,
    )

    try:
        xml_path.unlink(missing_ok=True)
    except Exception:
        pass

    if result.returncode != 0:
        script_path.unlink(missing_ok=True)
        err = (result.stderr or result.stdout).strip()
        print(f"[Reminder] ❌ schtasks: {err}")
        return ""

    return task_name


def _schedule_mac(target_dt: datetime, task_name: str, script_path: Path) -> str:
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    label = f"com.aria.reminder.{task_name}"
    plist_path = agents_dir / f"{label}.plist"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{script_path}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Year</key>   <integer>{target_dt.year}</integer>
    <key>Month</key>  <integer>{target_dt.month}</integer>
    <key>Day</key>    <integer>{target_dt.day}</integer>
    <key>Hour</key>   <integer>{target_dt.hour}</integer>
    <key>Minute</key> <integer>{target_dt.minute}</integer>
  </dict>
  <key>RunAtLoad</key>         <false/>
  <key>StandardOutPath</key>   <string>/dev/null</string>
  <key>StandardErrorPath</key> <string>/dev/null</string>
</dict>
</plist>
"""
    plist_path.write_text(plist_content, encoding="utf-8")
    plist_path.chmod(0o644)

    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        plist_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)
        print(f"[Reminder] ❌ launchctl: {result.stderr.strip()}")
        return ""

    return label


def _schedule_linux(target_dt: datetime, task_name: str, script_path: Path) -> str:
    if shutil.which("systemd-run"):
        on_calendar = target_dt.strftime("%Y-%m-%d %H:%M:00")
        result = subprocess.run(
            [
                "systemd-run",
                "--user",
                f"--on-calendar={on_calendar}",
                f"--unit={task_name}",
                "--",
                sys.executable,
                str(script_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return task_name
        print(f"[Reminder] ⚠️ systemd-run failed: {result.stderr.strip()}, trying 'at'")

    if shutil.which("at"):
        at_time = target_dt.strftime("%H:%M %Y-%m-%d")
        cmd_str = f"{sys.executable} {script_path}\n"
        result = subprocess.run(
            ["at", at_time],
            input=cmd_str,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return task_name
        print(f"[Reminder] ❌ at: {result.stderr.strip()}")
        return ""

    print("[Reminder] ❌ Neither systemd-run nor at found on this Linux system.")
    return ""


def _schedule_notification(
    target_dt: datetime, message: str, alarm: bool = False
) -> tuple[str, str]:
    os_name = _get_os()
    safe_msg = _sanitise(message)
    task_name = f"ARIAReminder_{target_dt.strftime('%Y%m%d_%H%M%S')}"
    job_type = "alarm" if alarm else "notification"

    try:
        script_path = _write_notify_script(task_name, safe_msg, os_name, alarm=alarm)
    except Exception as e:
        return "", f"Could not prepare the reminder script: {e}"

    try:
        if os_name == "windows":
            job_id = _schedule_windows(target_dt, task_name, script_path, safe_msg)
        elif os_name == "mac":
            job_id = _schedule_mac(target_dt, task_name, script_path)
        else:
            job_id = _schedule_linux(target_dt, task_name, script_path)
    except Exception as e:
        script_path.unlink(missing_ok=True)
        print(f"[Reminder] ❌ Scheduling exception: {e}")
        return "", "Something went wrong while scheduling the reminder."

    if not job_id:
        return "", "I couldn't register the reminder with the system scheduler."

    _add_job(job_id, target_dt, safe_msg, job_type)
    return job_id, ""


def _cancel_scheduled(job_id: str) -> bool:
    os_name = _get_os()
    ok = False

    if os_name == "mac":
        label = job_id if job_id.startswith("com.aria.") else f"com.aria.reminder.{job_id}"
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            plist.unlink(missing_ok=True)
            ok = True
        script = _scripts_dir() / f"{job_id.replace('com.aria.reminder.', '')}.py"
        if script.exists():
            script.unlink(missing_ok=True)

    elif os_name == "windows":
        r = subprocess.run(
            ["schtasks", "/Delete", "/TN", job_id, "/F"],
            capture_output=True,
            text=True,
        )
        ok = r.returncode == 0

    _remove_job(job_id)
    return ok


def _reminders_app_add(title: str, target_dt: datetime, list_name: str = "Reminders") -> str:
    title_safe = title.replace('"', '\\"')[:200]
    list_safe = list_name.replace('"', '\\"')
    script = f"""
tell application "Reminders"
  set targetList to list "{list_safe}"
  make new reminder at end of targetList with properties {{name:"{title_safe}", remind me date:date "{_apple_date(target_dt)}"}}
end tell
"""
    ok, out = _run_applescript(script)
    if not ok:
        return f"FAILED: Could not add to Reminders — {out}"
    return f"Added to Reminders for {target_dt.strftime('%B %d at %I:%M %p')}."


def _reminders_app_list() -> str:
    script = """
set output to ""
tell application "Reminders"
  repeat with r in (reminders whose completed is false)
    set d to ""
    try
      set d to remind me date of r as string
    end try
    set output to output & (name of r) & " | " & d & linefeed
  end repeat
end tell
return output
"""
    ok, out = _run_applescript(script)
    if not ok:
        return f"FAILED: Could not read Reminders — {out}"
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not lines:
        return "No open reminders in the Reminders app."
    return f"Reminders app ({len(lines)}):\n" + "\n".join(
        f"{i}. {ln}" for i, ln in enumerate(lines[:20], 1)
    )


def _reminders_app_complete(title_query: str) -> str:
    q = title_query.replace('"', '\\"').strip()
    if not q:
        return "Tell me which reminder to complete or cancel."
    script = f"""
set matched to 0
tell application "Reminders"
  repeat with r in (reminders whose completed is false)
    if (name of r) contains "{q}" then
      set completed of r to true
      set matched to matched + 1
    end if
  end repeat
end tell
return matched
"""
    ok, out = _run_applescript(script)
    if not ok:
        return f"FAILED: {out}"
    try:
        count = int(out.strip())
    except ValueError:
        count = 0
    if count == 0:
        return f"No open reminder matching '{title_query}'."
    return f"Marked {count} reminder(s) complete."


def _open_app(app_name: str) -> str:
    from core.platform_utils import IS_MAC
    if not IS_MAC:
        return f"Opening the {app_name} app is only available on macOS."
    try:
        r = subprocess.run(["open", "-a", app_name], capture_output=True, text=True, timeout=8)
        if r.returncode == 0:
            return f"Opened {app_name}."
        return f"FAILED: Could not open {app_name} — {(r.stderr or r.stdout).strip()}"
    except Exception as e:
        return f"FAILED: {e}"


def _list_scheduled() -> str:
    jobs = _load_jobs()
    now = datetime.now()
    upcoming = []
    for j in jobs:
        try:
            when = datetime.fromisoformat(j["when"])
        except Exception:
            continue
        if when >= now:
            upcoming.append((when, j))

    upcoming.sort(key=lambda x: x[0])
    if not upcoming:
        return "No upcoming ARIA scheduled reminders."

    lines = []
    for i, (when, j) in enumerate(upcoming[:15], 1):
        kind = j.get("type", "notification")
        msg = j.get("message", "")[:50]
        lines.append(
            f"{i}. {when.strftime('%b %d %I:%M %p')} ({kind}) — {msg} [id: {j.get('id', '')}]"
        )
    return f"Scheduled reminders ({len(lines)}):\n" + "\n".join(lines)


def _set_reminder(params: dict, alarm: bool = False) -> str:
    message = (params.get("message") or params.get("title") or "Reminder").strip()
    store = (params.get("store") or "notification").lower().strip()

    target_dt, err = _resolve_target_time(params)
    if err:
        return err
    assert target_dt is not None

    if store == "reminders_app":
        if _get_os() != "mac" or platform.system() != "Darwin":
            return "Apple Reminders is only available on macOS."
        return _reminders_app_add(message, target_dt, params.get("list", "Reminders"))

    job_id, err = _schedule_notification(target_dt, message, alarm=alarm)
    if err:
        return err

    friendly = target_dt.strftime("%B %d at %I:%M %p")
    label = "Alarm" if alarm else "Reminder"
    return f"{label} set for {friendly}."


def reminder(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    action = (params.get("action") or "set").lower().strip()

    if player:
        player.write_log(f"[reminder] {action}")

    if action in ("set", "add", "alarm"):
        result = _set_reminder(params, alarm=(action == "alarm"))
    elif action == "list":
        parts = [_list_scheduled()]
        if _get_os() == "mac" and platform.system() == "Darwin":
            parts.append(_reminders_app_list())
        result = "\n\n".join(parts)
    elif action in ("cancel", "complete", "delete"):
        job_id = (params.get("id") or params.get("job_id") or "").strip()
        title_q = (params.get("message") or params.get("title") or "").strip()
        if job_id:
            ok = _cancel_scheduled(job_id)
            result = (
                f"Cancelled scheduled reminder {job_id}."
                if ok
                else f"Could not cancel {job_id} — it may have already fired."
            )
        elif title_q and _get_os() == "mac":
            result = _reminders_app_complete(title_q)
        else:
            result = "Give me a reminder title to complete, or an id from list."
    elif action == "open":
        app = (params.get("app") or "Reminders").strip()
        if app.lower() in ("clock", "alarms"):
            result = _open_app("Clock")
        else:
            result = _open_app("Reminders")
    else:
        return (
            f"Unknown action '{action}'. "
            "Use set | alarm | list | cancel | open."
        )

    if player and result and not result.startswith("FAILED"):
        player.write_log(f"[Reminder] {result[:80]}")

    return result
