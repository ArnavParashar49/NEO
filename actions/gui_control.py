"""GUI Control - Smart UI Automation cross-platform adapter."""

import os
import json
import time

# Global cache to map simple string IDs (like "1-2-1") to navigation paths
# Format: { "1-2": [1, 2] }
_UI_CACHE = {}

def _get_os() -> str:
    try:
        from config import get_os
        return get_os()
    except Exception:
        import platform
        sys_os = platform.system().lower()
        if sys_os == "darwin": return "mac"
        if sys_os == "windows": return "windows"
        return "linux"

def gui_control(parameters: dict, response=None, player=None, session_memory=None) -> str:
    """
    Dispatch table for smart GUI control actions.
    Actions: inspect_window, click_element, type_text, press_key
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    
    if not action:
        return "No action specified."
        
    os_name = _get_os()
    
    if "focus_title" in params and params["focus_title"]:
        title = params["focus_title"]
        if os_name == "windows":
            # Just inspect_window to focus it, or custom logic
            _focus_window_windows(title)
            
    if action == "inspect_window":
        title = params.get("title", "")
        if os_name == "windows":
            return _inspect_window_windows(title)
        elif os_name == "mac":
            return _inspect_window_mac(title)
        elif os_name == "linux":
            return _inspect_window_linux(title)
        return f"inspect_window is not supported on {os_name}."

    if action == "click_element":
        element_id = str(params.get("element_id", ""))
        button = params.get("button", "left").lower()
        if os_name == "windows":
            return _click_element_windows(element_id, button)
        elif os_name == "mac":
            return _click_element_mac(element_id, button)
        elif os_name == "linux":
            return _click_element_linux(element_id, button)
        return f"click_element is not supported on {os_name}."

    if action == "click_at":
        x = params.get("x")
        y = params.get("y")
        if x is not None and y is not None:
            return _click_at_crossplatform(x, y)
        return "click_at requires 'x' and 'y' normalized coordinates (0-1000)."

    if action == "type_text":
        element_id = str(params.get("element_id", ""))
        text = params.get("text", "")
        if os_name == "windows" and element_id:
            # If we have a specific Windows element ID, use native injection
            return _type_text_windows(element_id, text)
        # Otherwise use universal PyAutoGUI typing (avoids Electron dropped keys)
        return _type_text_crossplatform(text)

    if action == "press_key":
        keys = params.get("keys", "")
        # ALWAYS use PyAutoGUI for key presses. uiautomation drops keys in Electron apps.
        return _press_key_crossplatform(keys)

    return f"Unknown GUI action: {action}"

# --- WINDOWS BACKEND ---

def _ensure_uiautomation():
    try:
        import uiautomation as auto
        return auto
    except ImportError:
        import sys
        import subprocess
        print("[GUI Control] Installing uiautomation...")
        subprocess.run([sys.executable, "-m", "pip", "install", "uiautomation"], capture_output=True)
        import uiautomation as auto
        return auto

def _get_target_window_windows(title: str):
    auto = _ensure_uiautomation()
    if title:
        win = auto.WindowControl(searchDepth=1, Name=title)
        if win.Exists(0.1): return win
        win = auto.WindowControl(searchDepth=2, SubName=title)
        if win.Exists(0.1): return win
        return None
    else:
        # Just grab the first WindowControl
        for w in auto.GetRootControl().GetChildren():
            if w.ControlTypeName == "WindowControl" and w.Name and w.Name != "Program Manager":
                return w
    return None

def _focus_window_windows(title: str) -> None:
    auto = _ensure_uiautomation()
    win = _get_target_window_windows(title)
    if win:
        win.SetFocus()
        time.sleep(0.5)

def _inspect_window_windows(title: str) -> str:
    auto = _ensure_uiautomation()

    win = _get_target_window_windows(title)
    if not win:
        return f"Could not find any window matching title '{title}'"
        
    win.SetFocus()
    time.sleep(0.5)

    global _UI_CACHE
    _UI_CACHE.clear()
    _UI_CACHE["_root_handle"] = win.NativeWindowHandle
    
    lines = [f"Window: {win.Name} (Class: {win.ClassName})"]
    lines.append("Interactive Elements:")
    lines.append("-" * 40)
    
    # We only care about interactive/useful elements
    INTERACTIVE_TYPES = {
        "ButtonControl", "EditControl", "MenuItemControl", "TabItemControl", 
        "CheckBoxControl", "ComboBoxControl", "ListItemControl", "HyperlinkControl",
        "DocumentControl"
    }

    def walk(control, path_indices, depth=0):
        if depth > 15: return # Prevent infinite/massive trees, but allow deep MS Office apps
        
        children = control.GetChildren()
        for idx, child in enumerate(children):
            current_path = path_indices + [idx]
            path_str = "-".join(map(str, current_path))
            
            # Is it interactive?
            c_type = child.ControlTypeName
            name = child.Name
            
            is_explicit = c_type in INTERACTIVE_TYPES
            is_named_container = name and c_type in ("TextControl", "PaneControl", "GroupControl", "CustomControl") and len(name) > 2
            
            if is_explicit or is_named_container:
                _UI_CACHE[path_str] = current_path
                indent = "  " * depth
                safe_name = (name or "<unnamed>").replace('\n', ' ')[:60]
                lines.append(f"{indent}[{path_str}] {c_type}: '{safe_name}'")
                
            walk(child, current_path, depth + 1)
            
    # Always set root to empty path []
    _UI_CACHE["root"] = []
    walk(win, [])
    
    lines.append("-" * 40)
    lines.append("To interact, use 'click_element' or 'type_text' with the ID (e.g. '0-1').")
    
    return "\n".join(lines)

def _resolve_element_windows(element_id: str):
    if not element_id:
        return None, "No element_id provided"
        
    auto = _ensure_uiautomation()
    if element_id in _UI_CACHE:
        path = _UI_CACHE[element_id]
        
        win = None
        handle = _UI_CACHE.get("_root_handle")
        if handle:
            win = auto.ControlFromHandle(handle)
            
        if not win:
            for w in auto.GetRootControl().GetChildren():
                if w.ControlTypeName == "WindowControl" and w.Name and w.Name != "Program Manager":
                    win = w
                    break
                
        if not win:
            return None, "No active windows found."
            
        curr = win
        try:
            for idx in path:
                curr = curr.GetChildren()[idx]
            return curr, None
        except Exception as e:
            return None, f"Failed to traverse path {path}: {e}. UI might have changed. Re-inspect."
    
    # If not a cached ID, try to find by Name directly on the focused window
    focused = auto.GetFocusedControl()
    if focused:
        # Go up to the window
        win = focused
        while win and win.ControlTypeName != "WindowControl":
            win = win.GetParentControl()
        if win:
            found = win.Control(Name=element_id)
            if found.Exists(0.1):
                return found, None
    
    return None, f"Element ID or Name '{element_id}' not found."

def _click_element_windows(element_id: str, button: str) -> str:
    element, err = _resolve_element_windows(element_id)
    if err: return err
    
    try:
        if button == "right":
            element.RightClick(simulateMove=False)
        elif button == "double":
            element.DoubleClick(simulateMove=False)
        else:
            element.Click(simulateMove=False)
        return f"Clicked element [{element_id}] ({element.ControlTypeName})"
    except Exception as e:
        return f"Failed to click: {e}"

def _safe_send_keys(auto, text: str) -> None:
    # uiautomation SendKeys treats +, ^, %, ~, (, ), {, and } as special commands.
    # Process char-by-char to avoid double-escaping.
    escaped = ""
    for ch in text:
        if ch in "+^%~(){}":
            escaped += f"{{{ch}}}"
        elif ch == '\n':
            escaped += '{Enter}'
        else:
            escaped += ch
    # We use the default charMode (True) to prevent input method interference.
    auto.SendKeys(escaped)

def _type_text_windows(element_id: str, text: str) -> str:
    auto = _ensure_uiautomation()
    
    if not element_id:
        # Type directly into the active window
        try:
            _safe_send_keys(auto, text)
            return f"Typed '{text}' globally."
        except Exception as e:
            return f"Failed to type globally: {e}"

    element, err = _resolve_element_windows(element_id)
    if err: return err
    
    try:
        element.SetFocus()
        time.sleep(0.1)
        # Clear existing text if it's an edit control
        if element.ControlTypeName == "EditControl":
            element.SendKeys('{Ctrl}a{Delete}')
        _safe_send_keys(auto, text)
        return f"Typed '{text}' into element [{element_id}]"
    except Exception as e:
        return f"Failed to type: {e}"

def _press_key_windows(keys: str) -> str:
    """Press a key combination on Windows. Automatically normalizes pyautogui-style keys
    (e.g., 'control+s', 'win+r') to SendKeys syntax ('{Ctrl}s', '{Win}r')."""
    auto = _ensure_uiautomation()
    
    if '{' in keys and '}' in keys:
        final_keys = keys # Already SendKeys format
    else:
        parts = [p.strip().lower() for p in keys.split('+')]
        mapped = []
        mod_map = {
            'ctrl': '{Ctrl}', 'control': '{Ctrl}',
            'alt': '{Alt}',
            'shift': '{Shift}',
            'win': '{Win}', 'windows': '{Win}', 'cmd': '{Win}', 'command': '{Win}'
        }
        key_map = {
            'enter': '{Enter}', 'return': '{Enter}',
            'esc': '{Esc}', 'escape': '{Esc}',
            'tab': '{Tab}', 'space': '{Space}', ' ': '{Space}',
            'backspace': '{Backspace}', 'delete': '{Delete}', 'del': '{Delete}',
            'up': '{Up}', 'down': '{Down}', 'left': '{Left}', 'right': '{Right}',
            'home': '{Home}', 'end': '{End}', 'pageup': '{PageUp}', 'pagedown': '{PageDown}'
        }
        for p in parts:
            if p in mod_map:
                mapped.append(mod_map[p])
            elif p in key_map:
                mapped.append(key_map[p])
            elif len(p) == 1:
                mapped.append(p)
            else:
                mapped.append(f"{{{p.capitalize()}}}")
        final_keys = "".join(mapped)
        
    try:
        auto.SendKeys(final_keys)
        return f"Pressed keys: {final_keys} (from {keys})"
    except Exception as e:
        return f"Failed to press keys: {e}"

# --- CROSS-PLATFORM BACKEND (macOS / Linux via pyautogui) ---

def _ensure_pyautogui():
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        import sys
        import subprocess
        print("[GUI Control] Installing pyautogui...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyautogui"], capture_output=True)
        import pyautogui
        return pyautogui

def _type_text_crossplatform(text: str) -> str:
    """Type text on macOS/Linux using pyautogui."""
    try:
        pag = _ensure_pyautogui()
        # pyautogui.write doesn't handle newlines well; split on newlines
        for i, line in enumerate(text.split('\n')):
            if i > 0:
                pag.press('enter')
            if line:
                pag.write(line, interval=0.03)
        return f"Typed text globally (cross-platform)."
    except Exception as e:
        return f"Failed to type (cross-platform): {e}"

def _press_key_crossplatform(keys: str) -> str:
    """Press keys on macOS/Linux using pyautogui. Correctly handles 'ctrl+s' using hotkey()."""
    try:
        pag = _ensure_pyautogui()
        # Map common key names
        key_map = {
            "return": "enter",
            "esc": "escape",
            "cmd": "command", "win": "command", "windows": "command"
        }
        
        parts = [p.strip().lower() for p in keys.split('+')]
        mapped_parts = [key_map.get(p, p) for p in parts]
        
        if len(mapped_parts) > 1:
            pag.hotkey(*mapped_parts)
        else:
            pag.press(mapped_parts[0])
            
        return f"Pressed key(s): {'+'.join(mapped_parts)} (cross-platform)"
    except Exception as e:
        return f"Failed to press key (cross-platform): {e}"

def _click_at_crossplatform(x: float, y: float) -> str:
    """Click at normalized coordinates (0-1000 scale)."""
    try:
        pag = _ensure_pyautogui()
        pag.FAILSAFE = False
        screen_w, screen_h = pag.size()
        
        # Ensure values are within 0-1000
        x = max(0.0, min(1000.0, float(x)))
        y = max(0.0, min(1000.0, float(y)))
        
        real_x = int((x / 1000.0) * screen_w)
        real_y = int((y / 1000.0) * screen_h)
        
        # Use a slight duration to make the movement visible
        pag.moveTo(real_x, real_y, duration=0.5)
        pag.click()
        return f"Clicked at normalized [{x}, {y}] -> absolute [{real_x}, {real_y}]"
    except Exception as e:
        return f"Failed to click at coordinates: {e}"

# --- macOS BACKEND (Native via AppleScript/System Events) ---

def _get_frontmost_mac_process() -> str:
    import subprocess
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.stdout.strip()
    except Exception:
        return ""

def _inspect_window_mac(title: str) -> str:
    import subprocess
    if title:
        # focus it first
        script_focus = f'tell application "System Events" to set frontmost of (first process whose name contains "{title}") to true'
        subprocess.run(["osascript", "-e", script_focus])
        
    proc_name = _get_frontmost_mac_process()
    if not proc_name:
        return "Failed to get active Mac application."

    # Use AppleScript to get the 'entire contents' of the front window, and extract roles/names.
    script = f'''
    tell application "System Events"
        tell process "{proc_name}"
            if not (exists window 1) then return "No windows open."
            set output to ""
            set all_elems to entire contents of window 1
            set i to 1
            repeat with elem in all_elems
                try
                    set elem_role to role of elem
                    set elem_name to name of elem
                    set elem_desc to description of elem
                    if elem_name is missing value then set elem_name to "<unnamed>"
                    if elem_desc is missing value then set elem_desc to ""
                    if elem_role is not "AXUnknown" and elem_role is not "AXGroup" then
                        set output to output & "[" & i & "] " & elem_role & ": '" & elem_name & "' (" & elem_desc & ")\\n"
                    end if
                end try
                set i to i + 1
            end repeat
            return output
        end tell
    end tell
    '''
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if r.returncode != 0:
            return f"Failed to inspect Mac window: {r.stderr.strip()}"
            
        global _UI_CACHE
        _UI_CACHE.clear()
        _UI_CACHE["_mac_process"] = proc_name
        
        lines = [f"Window: {proc_name}"]
        lines.append("-" * 40)
        lines.append(r.stdout.strip())
        lines.append("-" * 40)
        lines.append("To interact, use 'click_element' with the ID (e.g. '1').")
        return "\n".join(lines)
    except Exception as e:
        return f"Mac UI Inspect error: {e}"

def _click_element_mac(element_id: str, button: str) -> str:
    import subprocess
    proc_name = _UI_CACHE.get("_mac_process")
    if not proc_name:
        proc_name = _get_frontmost_mac_process()
        
    script = f'''
    tell application "System Events"
        tell process "{proc_name}"
            set all_elems to entire contents of window 1
            try
                set target to item {element_id} of all_elems
                click target
                return "Clicked element " & {element_id}
            on error errMsg
                return "Error: " & errMsg
            end try
        end tell
    end tell
    '''
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.stdout.strip()
    except Exception as e:
        return f"Mac UI Click error: {e}"

# --- LINUX BACKEND (Native via AT-SPI) ---

def _ensure_pyatspi():
    try:
        import pyatspi
        return pyatspi
    except ImportError:
        import sys
        import subprocess
        print("[GUI Control] Installing pyatspi...")
        # Note: on Linux pyatspi usually requires system packages (python3-pyatspi), pip install might fail.
        subprocess.run([sys.executable, "-m", "pip", "install", "pyatspi"], capture_output=True)
        import pyatspi
        return pyatspi

def _inspect_window_linux(title: str) -> str:
    try:
        pyatspi = _ensure_pyatspi()
    except Exception:
        return "pyatspi not found. Please install python3-pyatspi via your package manager."
        
    desktop = pyatspi.Registry.getDesktop(0)
    active_window = None
    
    # Simple heuristic to find active/frontmost window or match title
    for app in desktop:
        for win in app:
            if not win: continue
            state = win.getState()
            if state.contains(pyatspi.STATE_ACTIVE) or (title and title.lower() in win.name.lower()):
                active_window = win
                break
        if active_window: break
        
    if not active_window:
        return "No active window found via AT-SPI."
        
    global _UI_CACHE
    _UI_CACHE.clear()
    _UI_CACHE["_linux_window"] = active_window
    
    lines = [f"Window: {active_window.name}"]
    lines.append("-" * 40)
    
    def walk(obj, path, depth=0):
        if depth > 10: return
        for i, child in enumerate(obj):
            if not child: continue
            cur_path = path + [i]
            path_str = "-".join(map(str, cur_path))
            
            role = child.getRoleName()
            name = child.name or ""
            if role not in ('unknown', 'panel', 'filler'):
                _UI_CACHE[path_str] = child
                lines.append(f"{'  '*depth}[{path_str}] {role}: '{name}'")
                
            walk(child, cur_path, depth + 1)
            
    walk(active_window, [])
    lines.append("-" * 40)
    lines.append("To interact, use 'click_element' with the ID (e.g. '0-1').")
    return "\n".join(lines)

def _click_element_linux(element_id: str, button: str) -> str:
    try:
        pyatspi = _ensure_pyatspi()
    except Exception:
        return "pyatspi not found."
        
    child = _UI_CACHE.get(element_id)
    if not child:
        return f"Element ID {element_id} not found in cache. Run inspect_window first."
        
    try:
        action = child.queryAction()
        for i in range(action.nActions):
            if action.getName(i) in ('click', 'press', 'toggle'):
                action.doAction(i)
                return f"Clicked element {element_id} ({child.getRoleName()})"
        return f"Element {element_id} does not support click action."
    except Exception as e:
        return f"Linux UI Click error: {e}"
