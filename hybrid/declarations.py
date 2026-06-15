"""Gemini Live function declarations — add new tools here (see hybrid/ARCHITECTURE.md)."""

TOOL_DECLARATIONS = [
    {
        "name": "memory_tool",
        "description": "Store or retrieve long-term memories using the RAG database. Use this to remember user preferences, learned lessons, or past errors. Do NOT announce that you are saving to memory — just call the tool silently.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":   {"type": "STRING", "description": "store | retrieve"},
                "category": {"type": "STRING", "description": "Category of memory (e.g. preference, lesson, code)"},
                "content":  {"type": "STRING", "description": "The memory content to store or search query to retrieve"},
            },
            "required": ["action", "content"]
        }
    },
    {
        "name": "screen_analyze",
        "description": "Takes a screenshot of the user's screen and uses a Vision model to answer questions about what is on it. Use this whenever the user asks 'what is on my screen', 'read this error', or 'what am I looking at'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Specific question about the screen contents (e.g. 'What does the error message say?')"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web for information, news, product recommendations, prices, "
            "laptops, phones, TVs, and comparisons. Also use when user asks to SEE or "
            "laptops, phones, TVs, and comparisons. Also use when user asks to SEE or "
            "SHOW product images — pass product names in the query."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "download_control",
        "description": (
            "Download apps and files. For ANY app (Spotify, Chrome, VLC): use action google "
            "with query=app name — searches Google for '{app} download', opens the site, "
            "clicks Download in the browser (Playwright). auto does the same for app names. "
            "url: direct file link only. youtube: yt-dlp. Never use open_app instead of this "
            "when user asks to download/install an application."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "google (apps) | auto | url | youtube | cli (default auto)"},
                "query":       {"type": "STRING", "description": "What to download (name, song, app) or search terms"},
                "url":         {"type": "STRING", "description": "Direct https:// download link"},
                "destination": {"type": "STRING", "description": "Folder shortcut: downloads (default), desktop, etc."},
                "tool":        {"type": "STRING", "description": "cli action: curl | wget | yt-dlp"},
                "args":        {"type": "ARRAY", "items": {"type": "STRING"}, "description": "cli action: arguments after tool name"},
            },
            "required": []
        }
    },
    {
        "name": "weather_report",
        "description": (
            "Speaks the weather forecast aloud for a city. "
            "Use for any weather question. Never open a browser or Google — answer verbally."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "send_email",
        "description": (
            "Gmail in real Chrome. compose (default): draft + confirm before send. "
            "read/inbox: recent emails. search: find emails by query/from. "
            "Use contact name in 'to' — resolves via contact_manager. "
            "confirm_send true only after user says yes. cancel true to abort."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":       {"type": "STRING", "description": "compose | read | inbox | search"},
                "to":           {"type": "STRING", "description": "Email or contact name (compose)"},
                "subject":      {"type": "STRING", "description": "Email subject (compose)"},
                "body":         {"type": "STRING", "description": "Email body (compose)"},
                "query":        {"type": "STRING", "description": "Search query (read/search)"},
                "from":         {"type": "STRING", "description": "Filter by sender (read)"},
                "count":        {"type": "INTEGER", "description": "Emails to list (default 5)"},
                "browser":      {"type": "STRING", "description": "chrome | safari"},
                "confirm_send": {"type": "BOOLEAN", "description": "true ONLY after user confirms send"},
                "cancel":       {"type": "BOOLEAN", "description": "true if user declines"},
            },
            "required": []
        }
    },
    {
        "name": "contact_manager",
        "description": (
            "Look up or save contacts (email/phone). Use before emailing someone by name. "
            "Checks ARIA memory and macOS Contacts. save when user gives a new email."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "lookup | save | list"},
                "name":   {"type": "STRING", "description": "Contact name"},
                "email":  {"type": "STRING", "description": "Email (save)"},
                "phone":  {"type": "STRING", "description": "Phone (save)"},
                "notes":  {"type": "STRING", "description": "Notes (save)"},
            },
            "required": []
        }
    },
    {
        "name": "calendar_control",
        "description": (
            "Apple Calendar on Mac. list_today, list_tomorrow, list_week, add event, open app. "
            "For add: title, date YYYY-MM-DD, start_time HH:MM, optional end_time or duration_minutes."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":           {"type": "STRING", "description": "list_today | list_tomorrow | list_week | add | open | list_calendars"},
                "title":            {"type": "STRING", "description": "Event title (add)"},
                "date":             {"type": "STRING", "description": "YYYY-MM-DD (add)"},
                "start_time":       {"type": "STRING", "description": "HH:MM 24h (add)"},
                "end_time":         {"type": "STRING", "description": "HH:MM end (add)"},
                "duration_minutes": {"type": "INTEGER", "description": "Duration if no end_time"},
                "calendar":         {"type": "STRING", "description": "Calendar name e.g. Home, Work"},
                "notes":            {"type": "STRING", "description": "Event notes"},
                "location":         {"type": "STRING", "description": "Event location"},
            },
            "required": []
        }
    },
    {
        "name": "reminder",
        "description": (
            "Reminders and alarms. set: timed notification (date+time OR in_minutes/in_hours + message). "
            "alarm: same but with sound. store reminders_app saves to Apple Reminders (Mac). "
            "list | cancel | open (Reminders or Clock app)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":     {"type": "STRING", "description": "set | alarm | list | cancel | open"},
                "message":    {"type": "STRING", "description": "Reminder text"},
                "date":       {"type": "STRING", "description": "YYYY-MM-DD (set/alarm)"},
                "time":       {"type": "STRING", "description": "HH:MM 24h (set/alarm)"},
                "in_minutes": {"type": "INTEGER", "description": "Relative: minutes from now (e.g. 30)"},
                "in_hours":   {"type": "INTEGER", "description": "Relative: hours from now"},
                "store":      {"type": "STRING", "description": "notification (default) | reminders_app"},
                "list":       {"type": "STRING", "description": "Reminders list name (default Reminders)"},
                "id":         {"type": "STRING", "description": "Job id to cancel (from list)"},
                "app":        {"type": "STRING", "description": "open: Reminders | Clock"},
            },
            "required": []
        }
    },
    {
        "name": "notes_control",
        "description": (
            "Apple Notes on Mac (local files fallback elsewhere). "
            "list | create | read | append | search | open."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | create | read | append | search | open"},
                "title":  {"type": "STRING", "description": "Note title (create/read/append)"},
                "body":   {"type": "STRING", "description": "Note body text (create/append)"},
                "query":  {"type": "STRING", "description": "Search text (search/read)"},
            },
            "required": []
        }
    },
    {
        "name": "organizer_control",
        "description": (
            "Organize folders and bulk-rename files. preview shows plan without changes. "
            "organize sorts files by type or date into subfolders. "
            "bulk_rename: prefix | suffix | replace | numbered. "
            "Always use desktop/ prefix for folder paths. Needs confirm before moving/renaming."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":        {"type": "STRING", "description": "preview | organize | bulk_rename | list_types"},
                "path":          {"type": "STRING", "description": "Folder: desktop, downloads, desktop/Images"},
                "organize_mode": {"type": "STRING", "description": "by_type | by_date (for organize/preview)"},
                "rename_mode":   {"type": "STRING", "description": "prefix | suffix | replace | numbered"},
                "prefix":        {"type": "STRING", "description": "Prefix for prefix/numbered rename"},
                "suffix":        {"type": "STRING", "description": "Suffix for suffix rename"},
                "find":          {"type": "STRING", "description": "Text to find (replace mode)"},
                "replace":       {"type": "STRING", "description": "Replacement text (replace mode)"},
                "start":         {"type": "INTEGER", "description": "Start number for numbered rename (default 1)"},
                "extension":     {"type": "STRING", "description": "Only rename files with this extension e.g. .jpg"},
                "confirm":       {"type": "BOOLEAN", "description": "true ONLY after user confirms"},
                "cancel":        {"type": "BOOLEAN", "description": "true if user declined"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "document_tools",
        "description": (
            "Merge PDFs and compress files. merge_pdf: combine PDFs in a folder or listed files. "
            "compress_pdf / compress_image shrink file size. zip: compress folder to .zip. "
            "merge and zip need confirm before running."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "merge_pdf | compress_pdf | compress_image | zip | info"},
                "path":        {"type": "STRING", "description": "Folder path e.g. desktop, downloads"},
                "name":        {"type": "STRING", "description": "Single file name for compress"},
                "files":       {"type": "STRING", "description": "Comma/space-separated PDF names to merge"},
                "output":      {"type": "STRING", "description": "Output filename (merged.pdf or archive.zip)"},
                "destination": {"type": "STRING", "description": "Where to save merged PDF"},
                "quality":     {"type": "STRING", "description": "low | medium | high for PDF compress"},
                "confirm":     {"type": "BOOLEAN", "description": "true ONLY after user confirms merge/zip"},
                "cancel":      {"type": "BOOLEAN", "description": "true if user declined"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "list_manager",
        "description": (
            "Shopping list and todo list. list | add | remove | check (mark done) | uncheck | clear. "
            "Lists: shopping (groceries) | todos. Add multiple items comma-separated."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | add | remove | check | uncheck | clear | clear_done"},
                "list":        {"type": "STRING", "description": "shopping | todos (default shopping)"},
                "item":        {"type": "STRING", "description": "Single item text"},
                "items":       {"type": "STRING", "description": "Multiple items comma-separated"},
                "query":       {"type": "STRING", "description": "Item to find for remove/check"},
                "pending_only":{"type": "BOOLEAN", "description": "list: show only unchecked items"},
            },
            "required": []
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "system_control",
        "description": (
            "ALWAYS use for volume and screen brightness: louder, quieter, mute, brighter, dimmer. "
            "Works on Mac without extra setup for volume. Commands: volume_up, volume_down, mute, "
            "brightness_up, brightness_down."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {
                    "type": "STRING",
                    "description": (
                        "volume_up | volume_down | mute | brightness_up | brightness_down"
                    ),
                },
                "description": {
                    "type": "STRING",
                    "description": "Optional — natural language if command omitted",
                },
            },
            "required": [],
        },
    },
    {
        "name": "computer_settings",
        "description": (
            "Other computer controls: window management, keyboard shortcuts, typing on screen, "
            "close window, fullscreen, dark mode, WiFi, restart, shutdown, scroll, tabs, zoom, "
            "screenshot, lock screen. NOT for volume/brightness — use system_control. "
            "For opening apps use open_app. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": (
            "Manages files and folders. Paths support shortcuts + subfolders: desktop/Images, "
            "desktop/poster. move_all moves every file from source into one folder (use for "
            "'move all desktop files to X'). distribute_files splits N files per destination; "
            "with one destination and no count, moves all files. merge_folders moves all contents safely. "
            "delete and merge_folders (remove source) need confirm true after user says yes. "
            "open recent screenshot: action open + recent true."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":        {"type": "STRING", "description": "list | move_all | distribute_files | merge_folders | move | copy | delete | open | find | ..."},
                "path":          {"type": "STRING", "description": "Path or shortcut (desktop, downloads). Use desktop/FolderName for subfolders."},
                "source":        {"type": "STRING", "description": "Source folder (merge_folders, distribute_files, move)"},
                "destination":   {"type": "STRING", "description": "Destination folder"},
                "destination2":  {"type": "STRING", "description": "Second destination for distribute_files"},
                "count":         {"type": "INTEGER", "description": "distribute_files: per-destination file count (one dest, omit = all). largest: max results."},
                "include_folders": {"type": "BOOLEAN", "description": "move_all: also move subfolders (default false, files only)"},
                "remove_source": {"type": "BOOLEAN", "description": "merge_folders: remove source only when empty after merge"},
                "recent":        {"type": "BOOLEAN", "description": "open: open most recent screenshot"},
                "new_name":      {"type": "STRING", "description": "New name for rename"},
                "content":       {"type": "STRING", "description": "Content for create_file/write"},
                "name":          {"type": "STRING", "description": "File or folder name"},
                "app":           {"type": "STRING", "description": "App to open file with (open action)"},
                "extension":     {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "confirm":       {"type": "BOOLEAN", "description": "true ONLY after user confirms delete/merge"},
                "cancel":        {"type": "BOOLEAN", "description": "true if user declined"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean (needs confirm), list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":    {"type": "STRING", "description": "Image path for wallpaper"},
                "url":     {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":    {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":    {"type": "STRING", "description": "Natural language desktop task"},
                "confirm": {"type": "BOOLEAN", "description": "true ONLY after user confirms clean"},
                "cancel":  {"type": "BOOLEAN", "description": "true if user declined"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | scaffold | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": (
            "Fast multi-file build when the user already gave full details and no Q&A is needed. "
            "For any new project idea (game, site, app, tool), prefer project_builder first."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "project_builder",
        "description": (
            "Autonomously BUILDS any new software — games, websites, apps, APIs, CLI tools. "
            "ARIA writes the real code itself, then installs dependencies and runs it to test, "
            "fixing its own errors until it works (it does NOT hand off to VS Code/Copilot). "
            "ALWAYS use when the user says build/create/make/start a new project. "
            "Call it once with action=start and the description — it builds immediately, "
            "no confirmation needed."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":       {"type": "STRING", "description": "start (build it) | status | cancel"},
                "description":  {"type": "STRING", "description": "What to build (required)"},
                "project_name": {"type": "STRING", "description": "Optional folder name"},
            },
            "required": ["action", "description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools chained together. "
            "Use for: research then save to file, organize many files, browse several sites, "
            "open apps then automate browser + files. "
            "DO NOT use for a single email (use send_email), single file op (file_controller), "
            "or one browser action (browser_control)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: focus window, wait, random data generation.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "wait | focus_window | random_data | user_data"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "type":        {"type": "STRING",  "description": "Data type for random_data (e.g. name, email, city)"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name | email | phone"},
            },
            "required": ["action"]
        }
    },

    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_aria",
        "description": (
            "Shuts down ARIA immediately. Use when the user clearly wants to quit "
            "(quit, exit, goodbye, close ARIA). No confirmation step."
        ),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},

    {
        "name": "create_action",
        "description": (
            "Writes and registers a new ARIA tool dynamically! Use this whenever you are asked to do something "
            "that requires a Python script, an API call, or a capability you don't currently have. "
            "You MUST provide the full, valid Python code. The code MUST contain a dictionary named "
            "`TOOL_DECLARATION` at the top level with 'name', 'description', and 'parameters'. "
            "The file MUST also define a function with the exact same name as 'tool_name' that accepts a `parameters: dict` argument."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tool_name": {"type": "STRING", "description": "Name of the tool (snake_case, e.g. 'get_crypto_price')"},
                "code":      {"type": "STRING", "description": "Full Python code including imports, TOOL_DECLARATION, and the function"}
            },
            "required": ["tool_name", "code"]
        }
    },
    {
        "name": "spawn_agent",
        "description": (
            "Delegates a task to a specialised sub-agent that runs in its own isolated context "
            "with a focused tool set. Use this for complex goals that benefit from specialisation. "
            "Available agent types: researcher (web research, comparisons), coder (write/run code), "
            "system_ops (OS tasks, file management, app control), comms (email, messages, calendar). "
            "The sub-agent runs autonomously and returns the result."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "agent_type": {
                    "type": "STRING",
                    "description": "Type of sub-agent: researcher | coder | system_ops | comms"
                },
                "goal": {
                    "type": "STRING",
                    "description": "Clear, complete description of what this sub-agent should accomplish"
                }
            },
            "required": ["agent_type", "goal"]
        }
    }
,
    {
        "name": "create_presentation",
        "description": "Creates a PowerPoint presentation (.pptx) based on the provided title, subtitle, and slide data. Use this whenever the user asks you to make or create a presentation.",
        "parameters": {
                "type": "OBJECT",
                "properties": {
                        "title": {
                                "type": "STRING",
                                "description": "The title of the presentation."
                        },
                        "subtitle": {
                                "type": "STRING",
                                "description": "An optional subtitle for the presentation."
                        },
                        "slides": {
                                "type": "ARRAY",
                                "description": "A list of slides. Each slide should have a title and a list of bullet points.",
                                "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                                "title": {
                                                        "type": "STRING"
                                                },
                                                "bullets": {
                                                        "type": "ARRAY",
                                                        "items": {
                                                                "type": "STRING"
                                                        }
                                                }
                                        },
                                        "required": [
                                                "title",
                                                "bullets"
                                        ]
                                }
                        }
                },
                "required": [
                        "title",
                        "slides"
                ]
        }
}
]
