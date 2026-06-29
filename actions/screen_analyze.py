"""
CLI-based Screen Analysis for NEO.
Takes a screenshot using native OS commands and sends it to the vision model.
"""

import time
import subprocess
from pathlib import Path
from PIL import Image

def _take_screenshot() -> Path | None:
    import platform
    _OS = platform.system()
    screenshot_path = Path.home() / "Desktop" / f"neo_debug_{int(time.time())}.png"
    
    try:
        if _OS == "Darwin":
            subprocess.run(["screencapture", "-x", str(screenshot_path)], check=True)
        elif _OS == "Windows":
            script = f"""
            Add-Type -AssemblyName System.Windows.Forms
            Add-Type -AssemblyName System.Drawing
            $Screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
            $Bitmap = New-Object System.Drawing.Bitmap $Screen.Width, $Screen.Height
            $Graphics = [System.Drawing.Graphics]::FromImage($Bitmap)
            $Graphics.CopyFromScreen($Screen.X, $Screen.Y, 0, 0, $Bitmap.Size)
            $Bitmap.Save('{screenshot_path}')
            """
            subprocess.run(["powershell", "-Command", script], check=True)
        else:
            if subprocess.run(["which", "scrot"], capture_output=True).returncode == 0:
                subprocess.run(["scrot", str(screenshot_path)], check=True)
            elif subprocess.run(["which", "import"], capture_output=True).returncode == 0:
                subprocess.run(["import", "-window", "root", str(screenshot_path)], check=True)
            else:
                print("[ScreenAnalyze] ⚠️ No screenshot tool found (need scrot or imagemagick).")
                return None
                
        return screenshot_path
    except Exception as e:
        print(f"[ScreenAnalyze] ⚠️ Screenshot failed: {e}")
        return None

def screen_analyze(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    from core.llm import ask
    
    params = parameters or {}
    action = params.get("action", "describe")
    query = params.get("query", "Describe what is on my screen in detail.")
    
    if player:
        if action == "find_element":
            player.write_log("[ScreenAnalyze] Locating element on screen...")
        else:
            player.write_log("[ScreenAnalyze] Taking screenshot...")
        if hasattr(player, "show_screen_border"):
            player.show_screen_border()
            
    path = _take_screenshot()
    
    if player and hasattr(player, "hide_screen_border"):
        player.hide_screen_border()

    if not path or not path.exists():
        return "Failed to take screenshot."
        
    try:
        img = Image.open(path)

        if player:
            if action == "find_element":
                player.write_log(f"[ScreenAnalyze] Finding: '{query}'")
            else:
                player.write_log(f"[ScreenAnalyze] Analyzing: '{query}'")

        from core.models import VISION

        if action == "find_element":
            sys_prompt = "You are a spatial UI assistant. The user wants to find an element on the screen. Reply with ONLY the [y, x] bounding box coordinates (normalized 0-1000) for the center of the requested element."
            answer = ask(
                prompt=f"Find: {query}",
                images=[img],
                model=VISION,
                system=sys_prompt,
                temperature=0.0
            )
        else:
            answer = ask(
                prompt=query,
                images=[img],
                model=VISION,
            )
        
        # Clean up screenshot
        path.unlink(missing_ok=True)
        
        return answer
    except Exception as e:
        return f"Vision analysis failed: {e}"
