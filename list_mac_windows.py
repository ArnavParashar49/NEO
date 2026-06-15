import Quartz
import sys

window_list = Quartz.CGWindowListCopyWindowInfo(
    Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)

for window in window_list:
    name = window.get("kCGWindowName", "")
    owner = window.get("kCGWindowOwnerName", "")
    bounds = window.get("kCGWindowBounds", {})
    layer = window.get("kCGWindowLayer", 0)
    if "python" in owner.lower() or "aria" in owner.lower() or "antigravity" in owner.lower():
        print(f"Owner: {owner} | Name: {name} | Bounds: {bounds} | Layer: {layer}")
