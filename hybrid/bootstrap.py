"""
Register all tools from registry metadata and action handlers.
Add a built-in capability by updating _build_handlers() and _TOOL_META.
"""

from __future__ import annotations

from typing import Any, Callable

from hybrid.guards import allow_screen_process
from hybrid.orchestrator import Orchestrator
from hybrid.registry import ToolRegistry
from hybrid.types import ExecutionContext
import sys

# Mirrors main._SLOW_TOOLS — used for UI progress only
_SLOW_TOOLS = frozenset({
    "web_search", "download_control", "youtube_video", "agent_task", "file_processor",
    "flight_finder", "screen_process",
    "send_email", "browser_control", "file_controller", "calendar_control",
    "notes_control", "organizer_control", "document_tools", "list_manager",
    "screen_act", "weather_report", "discuss_project", "search_docs",
})

# agent routing + categories
_TOOL_META: dict[str, dict[str, Any]] = {
    "open_app": {"agent": "system", "category": "system", "fast": True, "description": "Opens any application on the computer. Use this whenever the user asks to open, launch, or start any app, website, or program. Always call this tool \u2014 never just say you opened it.", "parameters": {"type": "OBJECT", "properties": {"app_name": {"type": "STRING", "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"}}, "required": ["app_name"]}},
    "system_control": {"agent": "system", "category": "system", "fast": True, "description": "ALWAYS use for volume and screen brightness: louder, quieter, mute, brighter, dimmer. Works on Mac without extra setup for volume. Commands: volume_up, volume_down, mute, brightness_up, brightness_down.", "parameters": {"type": "OBJECT", "properties": {"command": {"type": "STRING", "description": "volume_up | volume_down | mute | brightness_up | brightness_down"}, "description": {"type": "STRING", "description": "Optional \u2014 natural language if command omitted"}}, "required": []}},
    "computer_settings": {"agent": "system", "category": "system", "fast": True, "description": "Computer settings only: dark mode, WiFi, Bluetooth, lock screen, sleep display, and empty trash. Never use for closing tabs, windows, or applications; use window_control.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "lock_screen | sleep_display | dark_mode | toggle_wifi | toggle_bluetooth | empty_trash"}, "description": {"type": "STRING", "description": "Natural language description of what to do"}}, "required": ["action"]}},
    "window_control": {"agent": "system", "category": "system", "fast": True, "description": "Safely closes a specific browser tab, one application window, or an application. Uses native target-aware controls and protects NEO from closing itself. For requests like 'close the YouTube tab', call close_tab once with target='YouTube'; do not use agent_task or browser_control. It searches tab titles and closes nothing if the target is absent.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "close_tab | close_window | close_app"}, "app": {"type": "STRING", "description": "Browser/application name such as Chrome, Notepad, or Spotify. Required for close_app; optional for close_tab."}, "target": {"type": "STRING", "description": "For close_tab, the requested tab title/site, such as YouTube, Gmail, or GitHub."}}, "required": ["action"]}},
    "project_starter": {"agent": "system", "category": "project", "fast": False, "description": "Creates a new project under ~/Projects, builds a minimal maintainable scaffold, and researches comparable products, open-source projects, standards, and reusable libraries for inspiration. Use whenever the user asks to start, create, bootstrap, or begin a new software/project idea. Never overwrite an existing folder.", "parameters": {"type": "OBJECT", "properties": {"name": {"type": "STRING", "description": "Short project name"}, "description": {"type": "STRING", "description": "What the project should do"}, "stack": {"type": "STRING", "description": "Requested stack such as Python, TypeScript, React, or generic"}, "parent": {"type": "STRING", "description": "Optional parent folder; defaults to ~/Projects"}}, "required": ["name"]}},
    "mind_palace": {"agent": "system", "category": "system", "fast": True, "description": "Manage NEO's Deep Knowledge Graph (Mind Palace). Use this to memorize entities, connect them, and recall connections.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "memorize_entity | connect_entities | recall_connections"}, "name": {"type": "STRING", "description": "Name of the entity"}, "label": {"type": "STRING", "description": "Entity label (e.g. Person, Concept, Project)"}, "attributes": {"type": "STRING", "description": "JSON string of entity attributes"}, "source": {"type": "STRING", "description": "Source entity name for connecting"}, "target": {"type": "STRING", "description": "Target entity name for connecting"}, "relationship": {"type": "STRING", "description": "Relationship between source and target (e.g. LIKES)"}}, "required": ["action"]}},
    "desktop_control": {"agent": "system", "category": "system", "fast": True, "description": "Controls the desktop: wallpaper, organize, clean (needs confirm), list, stats.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"}, "path": {"type": "STRING", "description": "Image path for wallpaper"}, "url": {"type": "STRING", "description": "Image URL for wallpaper_url"}, "mode": {"type": "STRING", "description": "by_type or by_date for organize"}, "task": {"type": "STRING", "description": "Natural language desktop task"}, "confirm": {"type": "BOOLEAN", "description": "true ONLY after user confirms clean"}, "cancel": {"type": "BOOLEAN", "description": "true if user declined"}}, "required": ["action"]}},
    "computer_control": {
        "agent": "system", 
        "category": "system", 
        "fast": False,
        "description": "Run terminal commands, execute bash/powershell scripts, or focus windows. Pass action='run_command' and command='...'",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action to perform: run_command, wait, focus_window"},
                "command": {"type": "string", "description": "The bash or powershell command to execute (if action=run_command)"}
            },
            "required": ["action"]
        }
    },
    "gui_control": {
        "agent": "system",
        "category": "system",
        "fast": False,
        "description": "Smart cross-platform GUI automation. Try 'inspect_window' FIRST on ANY OS to get element IDs, then use 'click_element' for instant UI control. Only if inspect_window returns 'No active window' or fails should you fall back to screen_analyze to get [y, x] and then use 'click_at' here. To type text globally, use action 'type_text' and omit element_id.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "inspect_window | click_element | click_at | type_text | press_key"},
                "title": {"type": "string", "description": "Window title to inspect (optional)"},
                "focus_title": {"type": "string", "description": "Window title to bring to foreground before action"},
                "element_id": {"type": "string", "description": "ID or exact Name of the element (for click_element)"},
                "x": {"type": "number", "description": "Normalized X coordinate (0-1000) for click_at"},
                "y": {"type": "number", "description": "Normalized Y coordinate (0-1000) for click_at"},
                "text": {"type": "string", "description": "The FULL text to type."},
                "keys": {"type": "string", "description": "Keys to press (for press_key)"},
                "button": {"type": "string", "description": "left | right | double (for click_element)"}
            },
            "required": ["action"]
        }
    },
    "file_controller": {"agent": "system", "category": "files", "fast": False, "description": "Manages files and folders. Paths support shortcuts + subfolders: desktop/Images, desktop/poster. move_all moves every file from source into one folder (use for 'move all desktop files to X'). distribute_files splits N files per destination; with one destination and no count, moves all files. merge_folders moves all contents safely. delete and merge_folders (remove source) need confirm true after user says yes. open recent screenshot: action open + recent true.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "list | move_all | distribute_files | merge_folders | move | copy | delete | open | find | ..."}, "path": {"type": "STRING", "description": "Path or shortcut (desktop, downloads). Use desktop/FolderName for subfolders."}, "source": {"type": "STRING", "description": "Source folder (merge_folders, distribute_files, move)"}, "destination": {"type": "STRING", "description": "Destination folder"}, "destination2": {"type": "STRING", "description": "Second destination for distribute_files"}, "count": {"type": "INTEGER", "description": "distribute_files: per-destination file count (one dest, omit = all). largest: max results."}, "include_folders": {"type": "BOOLEAN", "description": "move_all: also move subfolders (default false, files only)"}, "remove_source": {"type": "BOOLEAN", "description": "merge_folders: remove source only when empty after merge"}, "recent": {"type": "BOOLEAN", "description": "open: open most recent screenshot"}, "new_name": {"type": "STRING", "description": "New name for rename"}, "content": {"type": "STRING", "description": "Content for create_file/write"}, "name": {"type": "STRING", "description": "File or folder name"}, "app": {"type": "STRING", "description": "App to open file with (open action)"}, "extension": {"type": "STRING", "description": "File extension to search (e.g. .pdf)"}, "confirm": {"type": "BOOLEAN", "description": "true ONLY after user confirms delete/merge"}, "cancel": {"type": "BOOLEAN", "description": "true if user declined"}}, "required": ["action"]}},
    "file_processor": {"agent": "system", "category": "files", "fast": False, "description": "Processes any file that the user has uploaded or dropped onto the interface. Use this when the user refers to an uploaded file and wants an action on it. Supports: images (describe/ocr/resize/compress/convert), PDFs (summarize/extract_text/to_word), Word docs & text files (summarize/fix/reformat/translate), CSV/Excel (analyze/stats/filter/sort/convert), JSON/XML (validate/format/analyze), code files (explain/review/fix/optimize/run/document/test), audio (transcribe/trim/convert/info), video (trim/extract_audio/extract_frame/compress/transcribe/info), archives (list/extract), presentations (summarize/extract_text). ALWAYS call this tool when a file has been uploaded and the user gives a command about it. If the user's command is ambiguous, pick the most logical action for that file type.", "parameters": {"type": "OBJECT", "properties": {"file_path": {"type": "STRING", "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."}, "action": {"type": "STRING", "description": "What to do with the file. Examples by type:\nimage: describe | ocr | resize | compress | convert | info\npdf: summarize | extract_text | memorize | to_word | info\ndocx/txt: memorize | summarize | fix | reformat | translate_hint | word_count | to_bullet\ncsv/excel: analyze | stats | filter | sort | convert | info\njson: validate | format | analyze | to_csv\ncode: explain | review | fix | optimize | run | document | test\naudio: transcribe | trim | convert | info\nvideo: trim | extract_audio | extract_frame | compress | transcribe | info | convert\narchive: list | extract\npptx: summarize | extract_text | analyze"}, "instruction": {"type": "STRING", "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"}, "format": {"type": "STRING", "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"}, "width": {"type": "INTEGER", "description": "Target width for image resize"}, "height": {"type": "INTEGER", "description": "Target height for image resize"}, "scale": {"type": "NUMBER", "description": "Scale factor for image resize (e.g. 0.5)"}, "quality": {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"}, "start": {"type": "STRING", "description": "Start time for trim: seconds or HH:MM:SS"}, "end": {"type": "STRING", "description": "End time for trim: seconds or HH:MM:SS"}, "timestamp": {"type": "STRING", "description": "Timestamp for video frame extraction HH:MM:SS"}, "column": {"type": "STRING", "description": "Column name for CSV filter/sort"}, "value": {"type": "STRING", "description": "Filter value for CSV filter"}, "condition": {"type": "STRING", "description": "Filter condition: equals|contains|gt|lt"}, "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"}, "save": {"type": "BOOLEAN", "description": "Save result to file (default: true)"}, "destination": {"type": "STRING", "description": "Output folder for archive extract"}}, "required": []}},
    "organizer_control": {"agent": "system", "category": "files", "fast": False, "description": "Organize folders and bulk-rename files. preview shows plan without changes. organize sorts files by type or date into subfolders. bulk_rename: prefix | suffix | replace | numbered. Always use desktop/ prefix for folder paths. Needs confirm before moving/renaming.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "preview | organize | bulk_rename | list_types"}, "path": {"type": "STRING", "description": "Folder: desktop, downloads, desktop/Images"}, "organize_mode": {"type": "STRING", "description": "by_type | by_date (for organize/preview)"}, "rename_mode": {"type": "STRING", "description": "prefix | suffix | replace | numbered"}, "prefix": {"type": "STRING", "description": "Prefix for prefix/numbered rename"}, "suffix": {"type": "STRING", "description": "Suffix for suffix rename"}, "find": {"type": "STRING", "description": "Text to find (replace mode)"}, "replace": {"type": "STRING", "description": "Replacement text (replace mode)"}, "start": {"type": "INTEGER", "description": "Start number for numbered rename (default 1)"}, "extension": {"type": "STRING", "description": "Only rename files with this extension e.g. .jpg"}, "confirm": {"type": "BOOLEAN", "description": "true ONLY after user confirms"}, "cancel": {"type": "BOOLEAN", "description": "true if user declined"}}, "required": ["action"]}},
    "document_tools": {"agent": "system", "category": "files", "fast": False, "description": "Merge PDFs and compress files. merge_pdf: combine PDFs in a folder or listed files. compress_pdf / compress_image shrink file size. zip: compress folder to .zip. merge and zip need confirm before running.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "merge_pdf | compress_pdf | compress_image | zip | info"}, "path": {"type": "STRING", "description": "Folder path e.g. desktop, downloads"}, "name": {"type": "STRING", "description": "Single file name for compress"}, "files": {"type": "STRING", "description": "Comma/space-separated PDF names to merge"}, "output": {"type": "STRING", "description": "Output filename (merged.pdf or archive.zip)"}, "destination": {"type": "STRING", "description": "Where to save merged PDF"}, "quality": {"type": "STRING", "description": "low | medium | high for PDF compress"}, "confirm": {"type": "BOOLEAN", "description": "true ONLY after user confirms merge/zip"}, "cancel": {"type": "BOOLEAN", "description": "true if user declined"}}, "required": ["action"]}},
    "list_manager": {"agent": "system", "category": "files", "fast": False, "description": "Shopping list and todo list. list | add | remove | check (mark done) | uncheck | clear. Lists: shopping (groceries) | todos. Add multiple items comma-separated.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "list | add | remove | check | uncheck | clear | clear_done"}, "list": {"type": "STRING", "description": "shopping | todos (default shopping)"}, "item": {"type": "STRING", "description": "Single item text"}, "items": {"type": "STRING", "description": "Multiple items comma-separated"}, "query": {"type": "STRING", "description": "Item to find for remove/check"}, "pending_only": {"type": "BOOLEAN", "description": "list: show only unchecked items"}}, "required": []}},
    "notes_control": {"agent": "system", "category": "productivity", "fast": False, "description": "Apple Notes on Mac (local files fallback elsewhere). list | create | read | append | search | open.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "list | create | read | append | search | open"}, "title": {"type": "STRING", "description": "Note title (create/read/append)"}, "body": {"type": "STRING", "description": "Note body text (create/append)"}, "query": {"type": "STRING", "description": "Search text (search/read)"}}, "required": []}},
    "calendar_control": {"agent": "system", "category": "productivity", "fast": False, "description": "Apple Calendar on Mac. list_today, list_tomorrow, list_week, add event, open app. For add: title, date YYYY-MM-DD, start_time HH:MM, optional end_time or duration_minutes.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "list_today | list_tomorrow | list_week | add | open | list_calendars"}, "title": {"type": "STRING", "description": "Event title (add)"}, "date": {"type": "STRING", "description": "YYYY-MM-DD (add)"}, "start_time": {"type": "STRING", "description": "HH:MM 24h (add)"}, "end_time": {"type": "STRING", "description": "HH:MM end (add)"}, "duration_minutes": {"type": "INTEGER", "description": "Duration if no end_time"}, "calendar": {"type": "STRING", "description": "Calendar name e.g. Home, Work"}, "notes": {"type": "STRING", "description": "Event notes"}, "location": {"type": "STRING", "description": "Event location"}}, "required": []}},
    "reminder": {"agent": "system", "category": "productivity", "fast": False, "description": "Reminders and alarms. set: timed notification (date+time OR in_minutes/in_hours + message). alarm: same but with sound. store reminders_app saves to Apple Reminders (Mac). list | cancel | open (Reminders or Clock app).", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "set | alarm | list | cancel | open"}, "message": {"type": "STRING", "description": "Reminder text"}, "date": {"type": "STRING", "description": "YYYY-MM-DD (set/alarm)"}, "time": {"type": "STRING", "description": "HH:MM 24h (set/alarm)"}, "in_minutes": {"type": "INTEGER", "description": "Relative: minutes from now (e.g. 30)"}, "in_hours": {"type": "INTEGER", "description": "Relative: hours from now"}, "store": {"type": "STRING", "description": "notification (default) | reminders_app"}, "list": {"type": "STRING", "description": "Reminders list name (default Reminders)"}, "id": {"type": "STRING", "description": "Job id to cancel (from list)"}, "app": {"type": "STRING", "description": "open: Reminders | Clock"}}, "required": []}},
    "browser_control": {"agent": "system", "category": "browser", "fast": False, "description": "Controls browser content for navigation, search, and page interaction. Never use to close user tabs, windows, or applications; use window_control. Use agent_task for multi-step web interaction.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | screenshot | back | forward | reload | switch | list_browsers"}, "browser": {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the active browser."}, "url": {"type": "STRING", "description": "URL for go_to / new_tab action"}, "query": {"type": "STRING", "description": "Search query for search action"}, "engine": {"type": "STRING", "description": "google | bing | duckduckgo | yandex"}, "selector": {"type": "STRING", "description": "CSS selector for click/type"}, "text": {"type": "STRING", "description": "Text to click or type"}, "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"}, "direction": {"type": "STRING", "description": "up | down for scroll"}, "amount": {"type": "INTEGER", "description": "Scroll amount in pixels"}, "key": {"type": "STRING", "description": "Key name for press action"}, "path": {"type": "STRING", "description": "Save path for screenshot"}, "incognito": {"type": "BOOLEAN", "description": "Open in private/incognito mode"}, "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing"}}, "required": ["action"]}},
    "web_search": {"agent": "research", "category": "research", "fast": False, "description": "Searches the web for information, news, product recommendations, prices, laptops, phones, TVs, and comparisons. Also use when user asks to SEE or SHOW product images \u2014 pass product names in the query. NEVER use screen_process for product or search questions.", "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "Search query"}, "mode": {"type": "STRING", "description": "search (default) or compare"}, "items": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"}, "aspect": {"type": "STRING", "description": "price | specs | reviews"}}, "required": ["query"]}},
    "download_control": {"agent": "system", "category": "files", "fast": False, "description": "Terminal-first download and installation. Use install when the user explicitly asks NEO to install an app; it selects the native package manager and always asks permission before running. Use url for a direct file URL; it prefers curl/wget and asks permission. Use google only as fallback when no safe terminal method exists or a confirmed terminal install fails. Do NOT call this tool when the user only asks for a command, instructions, or steps; answer with a fenced code block instead.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "install | auto | url | youtube | cli | google (fallback only)"}, "query": {"type": "STRING", "description": "Package/app name or download search terms"}, "url": {"type": "STRING", "description": "Direct HTTPS download link"}, "destination": {"type": "STRING", "description": "Folder shortcut: downloads (default), desktop, etc."}, "tool": {"type": "STRING", "description": "cli action: curl | wget | yt-dlp"}, "args": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "cli action: arguments after tool name"}, "confirm": {"type": "BOOLEAN", "description": "true only after the user approves the staged terminal command"}, "cancel": {"type": "BOOLEAN", "description": "true when the user declines; the command will be shown instead"}}, "required": []}},
    "youtube_video": {"agent": "research", "category": "research", "fast": False, "description": "Controls YouTube. Use for: playing videos, summarizing a video's content, getting video info, or showing trending videos.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"}, "query": {"type": "STRING", "description": "Search query for play action"}, "save": {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"}, "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"}, "url": {"type": "STRING", "description": "Video URL for get_info action"}}, "required": []}},
    "flight_finder": {"agent": "research", "category": "research", "fast": True, "description": "Searches Google Flights and speaks the best options.", "parameters": {"type": "OBJECT", "properties": {"origin": {"type": "STRING", "description": "Departure city or airport code"}, "destination": {"type": "STRING", "description": "Arrival city or airport code"}, "date": {"type": "STRING", "description": "Departure date (any format)"}, "return_date": {"type": "STRING", "description": "Return date for round trips"}, "passengers": {"type": "INTEGER", "description": "Number of passengers (default: 1)"}, "cabin": {"type": "STRING", "description": "economy | premium | business | first"}, "save": {"type": "BOOLEAN", "description": "Save results to Notepad"}}, "required": ["origin", "destination", "date"]}},
    "screen_process": {"agent": "research", "category": "vision", "fast": False, "description": "Captures and analyzes the user's screen or webcam, then answers their question. Set angle=camera for webcam \u2014 live preview with local YOLO labels, DeepFace for remember/who. Remember person: user says remember this as [name]. Fully offline if local_vision_offline is true in config.", "parameters": {"type": "OBJECT", "properties": {"angle": {"type": "STRING", "description": "'camera' for webcam/object ID (opens preview), 'screen' for display. Default: screen"}, "text": {"type": "STRING", "description": "The question or instruction about the captured image"}}, "required": ["text"]}},
    "screen_act": {"agent": "research", "category": "vision", "fast": False, "description": "Captures the screen or camera, describes what is visible, and can click/type. Use for 'what's on my screen', reading the display, or acting on UI elements.", "parameters": {"type": "OBJECT", "properties": {"question": {"type": "STRING", "description": "What to look for or do on screen"}, "text": {"type": "STRING", "description": "Same as question"}, "mode": {"type": "STRING", "description": "explain (default) | act | click"}, "execute": {"type": "BOOLEAN", "description": "true to perform suggested click/type/open after explaining"}, "angle": {"type": "STRING", "description": "screen | camera (default screen)"}}, "required": []}},
    "weather_report": {"agent": "research", "category": "research", "fast": True, "description": "Speaks the weather forecast aloud for a city. Use for any weather question. Never open a browser or Google \u2014 answer verbally.", "parameters": {"type": "OBJECT", "properties": {"city": {"type": "STRING", "description": "City name"}}, "required": ["city"]}},
    "send_message": {"agent": "system", "category": "comms", "fast": False, "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.", "parameters": {"type": "OBJECT", "properties": {"receiver": {"type": "STRING", "description": "Recipient contact name"}, "message_text": {"type": "STRING", "description": "The message to send"}, "platform": {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}}, "required": ["receiver", "message_text", "platform"]}},
    "send_email": {"agent": "system", "category": "comms", "fast": False, "description": "Gmail in real Chrome. compose (default): draft + confirm before send. read/inbox: recent emails. search: find emails by query/from. Use contact name in 'to' \u2014 resolves via contact_manager. confirm_send true only after user says yes. cancel true to abort.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "compose | read | inbox | search"}, "to": {"type": "STRING", "description": "Email or contact name (compose)"}, "subject": {"type": "STRING", "description": "Email subject (compose)"}, "body": {"type": "STRING", "description": "Email body (compose)"}, "query": {"type": "STRING", "description": "Search query (read/search)"}, "from": {"type": "STRING", "description": "Filter by sender (read)"}, "count": {"type": "INTEGER", "description": "Emails to list (default 5)"}, "browser": {"type": "STRING", "description": "chrome | safari"}, "confirm_send": {"type": "BOOLEAN", "description": "true ONLY after user confirms send"}, "cancel": {"type": "BOOLEAN", "description": "true if user declines"}}, "required": []}},
    "contact_manager": {"agent": "memory", "category": "memory", "fast": False, "description": "Look up or save contacts (email/phone). Use before emailing someone by name. Checks ARIA memory and macOS Contacts. save when user gives a new email.", "parameters": {"type": "OBJECT", "properties": {"action": {"type": "STRING", "description": "lookup | save | list"}, "name": {"type": "STRING", "description": "Contact name"}, "email": {"type": "STRING", "description": "Email (save)"}, "phone": {"type": "STRING", "description": "Phone (save)"}, "notes": {"type": "STRING", "description": "Notes (save)"}}, "required": []}},
    "save_memory": {"agent": "memory", "category": "memory", "internal": True, "fast": False, "description": "Save an important personal fact about the user to long-term memory. Call this silently whenever the user reveals something worth remembering: name, age, city, job, preferences, hobbies, relationships, projects, or future plans. Do NOT call for: weather, reminders, searches, or one-time commands. Do NOT announce that you are saving \u2014 just call it silently. Values must be in English regardless of the conversation language.", "parameters": {"type": "OBJECT", "properties": {"category": {"type": "STRING", "description": "identity \u2014 name, age, birthday, city, job, language, nationality | preferences \u2014 favorite food/color/music/film/game/sport, hobbies | contacts \u2014 name, email, phone for people they email or call | projects \u2014 active projects, goals, things being built | relationships \u2014 friends, family, partner, colleagues | wishes \u2014 future plans, things to buy, travel dreams | notes \u2014 habits, schedule, anything else worth remembering"}, "key": {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"}, "value": {"type": "STRING", "description": "Concise value in English (e.g. AP, pizza, older sister)"}}, "required": ["category", "key", "value"]}},
    "agent_task": {"agent": "tool", "category": "agent", "fast": False, "description": "Executes complex multi-step tasks requiring multiple different tools chained together. Use for: multi-step web navigation like searching a site, clicking links, or adding items to a cart, research then save to file, organize many files. DO NOT use for a single email, single file op, or opening a single website. ALWAYS use this instead of browser_control for multi-step browser interactions.", "parameters": {"type": "OBJECT", "properties": {"goal": {"type": "STRING", "description": "Complete description of what to accomplish"}, "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}}, "required": ["goal"]}},
    "spawn_agent": {"agent": "tool", "category": "agent", "fast": False},
    "shutdown_neo": {"agent": "system", "category": "system", "internal": True, "fast": False},
    "memory_tool": {"agent": "memory", "category": "memory", "fast": True},
    "apply_skill": {"agent": "memory", "category": "memory", "fast": False},
    "screen_analyze": {
        "agent": "research", 
        "category": "vision", 
        "fast": False,
        "description": "Takes a screenshot and analyzes it. Use action='find_element' to get normalized [y, x] coordinates for a specific element. Use action='describe' for a general description.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "find_element | describe"},
                "query": {"type": "string", "description": "What to look for (e.g. 'the chat input box')"}
            },
            "required": ["action", "query"]
        }
    },
    "discuss_project": {"agent": "research", "category": "discussion", "fast": False},
    "search_docs": {"agent": "research", "category": "discussion", "fast": False},
    "exa_search": {"agent": "research", "category": "research", "fast": False},
}


def _wrap_action(
    fn: Callable,
    *,
    speak: bool = False,
    use_response: bool = False,
    use_session_memory: bool = False,
) -> Callable[[dict, ExecutionContext], str]:
    def handler(args: dict, ctx: ExecutionContext) -> str:
        kwargs: dict = {"parameters": args, "player": ctx.ui}
        if speak and ctx.speak:
            kwargs["speak"] = ctx.speak
        if use_response:
            kwargs["response"] = None
        if use_session_memory:
            kwargs["session_memory"] = ctx.session_memory
        out = fn(**kwargs)
        return out if out is not None else "Done."

    return handler


def _build_handlers() -> dict[str, Callable]:
    from actions.browser_control import browser_control
    from actions.calendar import calendar_control
    from actions.computer_control import computer_control
    from actions.computer_settings import computer_settings
    from actions.contacts import contact_manager
    from actions.desktop import desktop_control
    from actions.document_tools import document_tools
    from actions.file_controller import file_controller
    from actions.file_processor import file_processor
    from actions.flight_finder import flight_finder
    from actions.list_manager import list_manager
    from actions.notes import notes_control
    from actions.open_app import open_app
    from actions.organizer import organizer_control
    from actions.reminder import reminder
    from actions.send_email import send_email
    from actions.send_message import send_message
    from actions.system_control import system_control
    from actions.weather_report import weather_action
    from actions.window_control import window_control
    from actions.project_starter import project_starter
    from actions.download_control import download_control
    from actions.discuss_project import discuss_project
    from actions.search_docs import search_docs
    from actions.web_search import web_search as web_search_action
    from actions.youtube_video import youtube_video
    from actions.create_presentation import create_presentation
    from actions.screen_analyze import screen_analyze
    from actions.fast_fetch import fast_fetch
    from actions.exa_search import exa_search
    from actions.gui_control import gui_control
    from actions.mind_palace import mind_palace

    return {
        "open_app": _wrap_action(open_app, use_response=True, use_session_memory=True),
        "gui_control": _wrap_action(gui_control),
        "mind_palace": _wrap_action(mind_palace),
        "weather_report": _wrap_action(weather_action),
        "browser_control": _wrap_action(browser_control),
        "file_controller": _wrap_action(file_controller),
        "send_message": _wrap_action(send_message, use_response=True, use_session_memory=True),
        "send_email": _wrap_action(send_email),
        "contact_manager": _wrap_action(contact_manager),
        "calendar_control": _wrap_action(calendar_control),
        "reminder": _wrap_action(reminder, use_response=True),
        "notes_control": _wrap_action(notes_control),
        "organizer_control": _wrap_action(organizer_control),
        "document_tools": _wrap_action(document_tools),
        "list_manager": _wrap_action(list_manager),
        "youtube_video": _wrap_action(youtube_video, use_response=True),
        "system_control": _wrap_action(system_control),
        "computer_settings": _wrap_action(computer_settings, use_response=True),
        "window_control": _wrap_action(window_control),
        "project_starter": _wrap_action(project_starter),
        "desktop_control": _wrap_action(desktop_control),
        "agent_task": _agent_task_handler,
        "web_search": _wrap_action(web_search_action),
        "fast_fetch": _wrap_action(fast_fetch),
        "download_control": _wrap_action(download_control),
        "file_processor": _file_processor_handler,
        "computer_control": _wrap_action(computer_control),
        "flight_finder": _wrap_action(flight_finder),
        "create_presentation": _wrap_action(create_presentation),
        "spawn_agent": _spawn_agent_handler,
        "memory_tool": _memory_tool_handler,
        "apply_skill": _apply_skill_handler,
        "screen_analyze": _wrap_action(screen_analyze, use_response=True),
        "discuss_project": _wrap_action(discuss_project, speak=True, use_response=True),
        "search_docs": _wrap_action(search_docs, speak=True, use_response=True),
        "exa_search": _wrap_action(exa_search),
        "shutdown_neo": lambda _a, _c: "Goodbye.",
    }


def _memory_tool_handler(args: dict, ctx: ExecutionContext) -> str:
    from core.memory_rag import store_memory, retrieve_relevant_memory
    action = args.get("action", "")
    content = args.get("content", "")
    category = args.get("category", "general")
    
    if action == "store":
        if store_memory(category, content):
            return f"Successfully stored memory in category '{category}'"
        return "Failed to store memory."
    elif action == "retrieve":
        memories = retrieve_relevant_memory(content, top_k=3, category=category if category != "general" else None)
        if not memories:
            return "No relevant memories found."
        out = "Memories found:\n"
        for m in memories:
            out += f"- [{m['metadata'].get('category', 'general')}]: {m['content']}\n"
        return out
    return "Invalid action. Use 'store' or 'retrieve'."


def _apply_skill_handler(args: dict, ctx: ExecutionContext) -> str:
    from actions.skill_loader import apply_skill_by_name

    name = args.get("skill_name") or args.get("name") or ""
    query = args.get("query") or ""
    return apply_skill_by_name(name, query)


def _file_processor_handler(args: dict, ctx: ExecutionContext) -> str:
    from actions.file_processor import file_processor

    if not args.get("file_path") and ctx.ui and getattr(ctx.ui, "current_file", None):
        args = dict(args)
        args["file_path"] = ctx.ui.current_file
    return _wrap_action(file_processor, speak=True)(args, ctx)


def _save_memory_handler(args: dict, ctx: ExecutionContext) -> str:
    from core.memory_ext import store_memory_smart

    category = args.get("category", "notes")
    key = args.get("key", "")
    value = args.get("value", "")
    if key and value:
        stored = store_memory_smart(category, value)
        if stored:
            print(f"[Memory] 💾 {category}/{key} = {value[:60]}")
    return "ok"


def _is_complex_goal(goal: str) -> bool:
    """Return True if the goal needs multiple agent types (→ swarm)."""
    keywords = (
        "research and", "compare and",
        "both", "multiple", "also",
    )
    return any(kw in goal.lower() for kw in keywords)


def _agent_task_handler(args: dict, ctx: ExecutionContext) -> str:
    goal = (args.get("goal") or "").strip()
    if not goal:
        return "No goal provided for agent_task."

    # Autonomous mode (opt-in): the model reasons over real tool results in a loop
    # instead of pre-planning fixed steps. Falls back to the planner on any error.
    try:
        from config import get_config

        if get_config().get("autonomous_mode", True):
            # Try GoalDispatcher first — splits multi-task input into parallel goals.
            # Falls back to swarm for complex single goals, then single agent.
            from core.goal_dispatcher import get_dispatcher, split_goals

            goals = split_goals(goal)
            if len(goals) >= 2:
                print(f"[GoalDispatcher] Detected {len(goals)} independent goals, dispatching in parallel")
                result = get_dispatcher().dispatch(goal, ctx)
                return result.summary

            if _is_complex_goal(goal):
                from core.agent_swarm import run_swarm_task

                return run_swarm_task(goal, ctx)
            from core.agent_loop import run_agent

            result = run_agent(
                goal, ctx,
                on_step=lambda s: print(f"[agent] {s.tool}({s.args}) -> {s.result[:80]}"),
            )
            return result.answer
    except Exception as e:
        print(f"[agent_task] autonomous mode error, using planner fallback: {e}")

    orch = ctx.get("orchestrator")
    if orch and hasattr(orch, "run_planned_sync"):
        return orch.run_planned_sync(goal, ctx)
    # No task queue available — fall back to telling the user the goal was noted
    return f"Goal received: '{goal}'. I'll work on it."


def _spawn_agent_handler(args: dict, ctx: ExecutionContext) -> str:
    agent_type = args.get("agent_type")
    goal = args.get("goal")
    if not agent_type or not goal:
        return "Error: agent_type and goal are required."
    
    from core.sub_agents import BUILT_IN_AGENTS, run_sub_agent
    
    spec = BUILT_IN_AGENTS.get(agent_type)
    if not spec:
        return f"Error: Unknown agent type '{agent_type}'. Available: {', '.join(BUILT_IN_AGENTS.keys())}"
        
    try:
        result = run_sub_agent(spec, goal, ctx, on_step=lambda s: print(f"[SubAgent:{agent_type}] {s.tool}() -> {s.ok}"))
        return f"Sub-agent '{agent_type}' finished. Result:\n{result.answer}"
    except Exception as e:
        return f"Sub-agent '{agent_type}' crashed: {e}"


def register_all_tools(registry: ToolRegistry | None = None) -> ToolRegistry:
    registry = registry or ToolRegistry.instance()
    handlers = _build_handlers()

    # Dynamic Tool Discovery: Auto-load any new actions in the actions/ folder
    import importlib.util
    from pathlib import Path
    
    actions_dir = Path(__file__).parent.parent / "actions"
    if actions_dir.exists():
        for py_file in actions_dir.glob("*.py"):
            if py_file.name.startswith("_") or py_file.name == "dev_run.py":
                continue
            
            module_name = py_file.stem
            if module_name not in handlers:
                try:
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = mod
                        spec.loader.exec_module(mod)
                        if hasattr(mod, "TOOL_DECLARATION") and hasattr(mod, module_name):
                            decl = mod.TOOL_DECLARATION
                            # Declarations now live in ToolRegistry. Preserve metadata
                            # supplied by a dynamically discovered action instead of
                            # appending to the removed declarations.py list.
                            if isinstance(decl, dict):
                                declared_name = decl.get("name") or module_name
                                if declared_name != module_name:
                                    print(
                                        f"[ToolRegistry] Ignoring declaration name "
                                        f"'{declared_name}' for module '{module_name}'"
                                    )
                                    continue
                                _TOOL_META.setdefault(module_name, {}).update({
                                    "description": decl.get("description", module_name),
                                    "parameters": decl.get("parameters", {}),
                                })
                            handlers[module_name] = _wrap_action(getattr(mod, module_name))
                            print(f"[ToolRegistry] Dynamically loaded {module_name}")
                except Exception as e:
                    print(f"[ToolRegistry] Failed to dynamic load {module_name}: {e}")

    # Declarative fast-path patterns — moved here from router.py
    # To add a new fast-path tool: add patterns here, not in router.py.
    _FAST_PATH_PATTERNS: dict[str, list[tuple[str, dict]]] = {
        "system_control": [
            (r"(?:increase|turn up|raise)\s+(?:the\s+)?volume|volume up|louder",
             {"action": "volume", "direction": "up"}),
            (r"(?:decrease|turn down|lower)\s+(?:the\s+)?volume|volume down|quieter",
             {"action": "volume", "direction": "down"}),
            (r"(?:mute|silence)\s+(?:the\s+)?(?:volume|sound)|\bmute\b",
             {"action": "volume", "direction": "mute"}),
            (r"(?:increase|turn up|raise)\s+(?:the\s+)?brightness|brighter",
             {"action": "brightness", "direction": "up"}),
            (r"(?:decrease|turn down|lower|dim)\s+(?:the\s+)?brightness|dimmer",
             {"action": "brightness", "direction": "down"}),
        ],
    }

    for name, handler in handlers.items():
        if name == "save_memory":
            handler = _save_memory_handler
        meta = _TOOL_META.get(name, {})
        guard = None
        if name == "screen_process":
            guard = allow_screen_process

        registry.register(
            name=name,
            description=meta.get("description", name),
            parameters=meta.get("parameters", {}),
            handler=handler,
            category=meta.get("category", "general"),
            agent=meta.get("agent", "tool"),
            fast_eligible=meta.get("fast", True),
            slow=name in _SLOW_TOOLS,
            internal=meta.get("internal", False),
            guard=guard,
            fast_path_patterns=_FAST_PATH_PATTERNS.get(name),
        )

    # Boot and register MCP tools
    try:
        from core.mcp_client import get_mcp_manager, execute_mcp_tool
        mcp = get_mcp_manager()
        mcp.start()
        for tool_name, schema in mcp.get_tools().items():
            registry.register(
                name=tool_name,
                description=schema.get("description", "MCP Tool"),
                parameters=schema.get("parameters", {}),
                handler=lambda args, ctx, tn=tool_name: execute_mcp_tool(tn, **args),
                category=schema.get("category", "mcp"),
                agent=schema.get("agent", "tool"),
                fast_eligible=schema.get("fast", False),
                slow=True,
                internal=False,
            )
    except Exception as e:
        print(f"[MCP] Failed to register MCP tools: {e}")

    from actions.skill_loader import load_all_skills
    load_all_skills()

    print(f"[ToolRegistry] Registered {len(registry.names())} tools")
    return registry


_orchestrator: Orchestrator | None = None


def init_hybrid_system() -> Orchestrator:
    global _orchestrator
    registry = register_all_tools()
    _orchestrator = Orchestrator(registry=registry)
    
    from hybrid.observer import ContinuousLearningObserver
    from hybrid.task_bus import get_task_bus
    _observer = ContinuousLearningObserver(get_task_bus())
    
    return _orchestrator


def get_orchestrator() -> Orchestrator:
    if _orchestrator is None:
        return init_hybrid_system()
    return _orchestrator
