"""Screenshot → explain → act: analyze screen with Gemini, optionally click/type/open."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False


def _get_api_key() -> str:
    base = Path(__file__).resolve().parent.parent
    try:
        cfg = json.loads((base / "config" / "api_keys.json").read_text(encoding="utf-8"))
        return cfg.get("gemini_api_key", "")
    except Exception:
        return ""


def _capture(angle: str) -> tuple[bytes, str]:
    from actions.screen_processor import _capture_camera, _capture_screen

    if angle == "camera":
        return _capture_camera()
    return _capture_screen()


def _parse_analysis(text: str) -> dict:
    out = {
        "explanation": "",
        "action": "none",
        "target": "",
        "value": "",
        "url": "",
    }
    if not text:
        return out

    lines = text.strip().splitlines()
    for line in lines:
        upper = line.upper()
        if upper.startswith("EXPLANATION:"):
            out["explanation"] = line.split(":", 1)[1].strip()
        elif upper.startswith("ACTION:"):
            out["action"] = line.split(":", 1)[1].strip().lower()
        elif upper.startswith("TARGET:"):
            out["target"] = line.split(":", 1)[1].strip()
        elif upper.startswith("VALUE:"):
            out["value"] = line.split(":", 1)[1].strip()
        elif upper.startswith("URL:"):
            out["url"] = line.split(":", 1)[1].strip()

    if not out["explanation"]:
        out["explanation"] = text.strip()[:1200]
    return out


def _analyze_screen(
    image_bytes: bytes,
    mime_type: str,
    question: str,
    *,
    want_action: bool,
    is_camera: bool = False,
    local_hints: str = "",
) -> dict:
    api_key = _get_api_key()
    if not api_key:
        return {"error": "No Gemini API key configured."}

    from google import genai
    from google.genai import types as gtypes

    w, h = (1920, 1080)
    if _PYAUTOGUI:
        try:
            w, h = pyautogui.size()
        except Exception:
            pass

    action_block = ""
    if want_action and not is_camera:
        action_block = (
            "\nIf the user wants you to DO something on screen, also output these lines:\n"
            "ACTION: none | click | type | open_url | scroll | hotkey\n"
            "TARGET: UI element to click (for click)\n"
            "VALUE: text to type or hotkey keys like cmd+l (for type/hotkey)\n"
            "URL: full URL (for open_url)\n"
            "Pick the safest single action that fulfills the request."
        )

    hints_block = ""
    if local_hints:
        hints_block = f"\nLocal offline vision (trust if helpful):\n{local_hints}\n"

    if is_camera:
        prompt = (
            "You are analyzing a LIVE WEBCAM photo (not a screen screenshot).\n"
            f"User question: {question}\n"
            f"{hints_block}\n"
            "Identify what you see as specifically as possible:\n"
            "- Name objects, people, animals, food, plants, products, and brands when recognizable\n"
            "- Note colors, materials, and where things are (left / center / right / background)\n"
            "- Read any visible text on labels, screens, books, or packaging\n"
            "- If unsure, say what it most likely is and why\n\n"
            "Reply in this exact format:\n"
            "EXPLANATION: 2-4 spoken sentences — warm, specific, helpful identification. "
            "Lead with the main subject, then notable details."
            f"{action_block}"
        )
        model = "gemini-2.5-flash"
    else:
        prompt = (
            f"Screenshot is {w}x{h} pixels.\n"
            f"User request: {question}\n"
            f"{hints_block}\n"
            "Reply in this exact format:\n"
            "EXPLANATION: 1-3 sentences describing what's on screen and answering the user.\n"
            f"{action_block}"
        )
        model = "gemini-2.5-flash-lite"

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=[
            gtypes.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
    )
    return _parse_analysis((response.text or "").strip())


def _execute_action(parsed: dict, player=None) -> str:
    action = (parsed.get("action") or "none").lower()
    if action in ("none", "", "explain"):
        return ""

    from actions.computer_control import computer_control
    from actions.browser_control import browser_control

    if action == "click":
        target = parsed.get("target") or parsed.get("value")
        if not target:
            return "FAILED: No click target identified."
        return computer_control(
            parameters={"action": "screen_click", "description": target},
            player=player,
        )

    if action == "type":
        text = parsed.get("value") or parsed.get("target")
        if not text:
            return "FAILED: No text to type."
        return computer_control(
            parameters={"action": "smart_type", "text": text},
            player=player,
        )

    if action == "open_url":
        url = parsed.get("url") or parsed.get("value")
        if not url:
            return "FAILED: No URL to open."
        if not url.startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        return browser_control(parameters={"action": "go_to", "url": url}, player=player)

    if action == "scroll":
        direction = "down"
        val = (parsed.get("value") or "").lower()
        if "up" in val:
            direction = "up"
        return computer_control(
            parameters={"action": "scroll", "direction": direction, "amount": 5},
            player=player,
        )

    if action == "hotkey":
        keys = parsed.get("value") or parsed.get("target")
        if not keys:
            return "FAILED: No hotkey specified."
        return computer_control(parameters={"action": "hotkey", "keys": keys}, player=player)

    return f"Skipped unknown action: {action}"


def screen_act(
    parameters: dict | None = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    question = (
        params.get("question")
        or params.get("text")
        or params.get("user_text")
        or ""
    ).strip()
    mode = (params.get("mode") or params.get("action") or "explain").lower().strip()
    angle = (params.get("angle") or "screen").lower().strip()
    execute = bool(params.get("execute") or params.get("do_act") or mode in ("act", "click", "do"))

    if angle == "screen" and question:
        if re.search(
            r"\b(camera|webcam|see me|in front of (?:me|you|the camera)|"
            r"what am i holding|what(?:'s| is) this (?:thing|object)|"
            r"look at me|through the camera)\b",
            question,
            re.I,
        ):
            angle = "camera"

    if not question:
        return "NEEDS_USER: What should I look for on your screen?"

    if player:
        player.write_log(f"[screen_act] {mode}: {question[:60]}")

    show_border = angle != "camera" and player and hasattr(player, "show_screen_border")
    use_camera_preview = angle == "camera" and player and hasattr(player, "show_camera_preview")

    # Screen capture must not include an open camera preview or use the webcam device.
    if show_border and player and hasattr(player, "hide_camera_preview"):
        preview = player.get_camera_preview() if hasattr(player, "get_camera_preview") else None
        if preview and preview.is_active():
            player.hide_camera_preview()

    if show_border:
        player.show_screen_border()

    if use_camera_preview:
        from actions.screen_processor import _cv2_backend, _get_camera_index

        player.show_camera_preview(_get_camera_index(), _cv2_backend())
        preview = player.get_camera_preview() if hasattr(player, "get_camera_preview") else None
        if preview and hasattr(preview, "wait_for_frame"):
            preview.wait_for_frame(timeout=0.35)
        if hasattr(player, "set_camera_status"):
            player.set_camera_status("ARIA is looking…")

    try:
        image_bytes, mime_type = _capture_with_preview(angle, player, use_camera_preview)
    except Exception as e:
        return f"FAILED: Could not capture {angle} — {e}"

    local_analysis: dict = {}
    local_text = ""
    parsed = None
    cfg_vis = {"offline_only": False, "enabled": False}
    try:
        from actions.vision_local import (
            analyze_frame,
            bytes_to_bgr,
            local_hints_for_gemini,
            parse_face_intent,
            vision_config,
        )

        cfg_vis = vision_config()
        frame_bgr = bytes_to_bgr(image_bytes)
        want_action = execute or mode in ("act", "click", "do")
        parallel_cloud = (
            angle == "camera"
            and cfg_vis["enabled"]
            and frame_bgr is not None
            and not cfg_vis.get("offline_only")
        )

        if cfg_vis["enabled"] and frame_bgr is not None:
            if use_camera_preview and hasattr(player, "set_camera_status"):
                player.set_camera_status("Scanning…")
            intent, _ = parse_face_intent(question)

            use_faces = angle == "camera" and cfg_vis["faces"]

            if intent in ("remember", "list"):
                local_analysis = analyze_frame(
                    frame_bgr, question, run_yolo=True, run_faces=use_faces,
                )
                local_text = (
                    local_analysis.get("face_action_result")
                    or local_analysis.get("local_only_text")
                    or ""
                )
                return local_text or "Done."

            def _run_local() -> dict:
                return analyze_frame(
                    frame_bgr,
                    question,
                    run_yolo=True,
                    run_faces=use_faces,
                )

            def _run_cloud(hints: str = "") -> dict:
                if use_camera_preview and hasattr(player, "set_camera_status"):
                    player.set_camera_status("Identifying…")
                return _analyze_screen(
                    image_bytes,
                    mime_type,
                    question,
                    want_action=want_action and angle != "camera",
                    is_camera=(angle == "camera"),
                    local_hints=hints,
                )

            if parallel_cloud:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    local_f = pool.submit(_run_local)
                    cloud_f = pool.submit(_run_cloud, "")
                    local_analysis = local_f.result()
                    parsed = cloud_f.result()
                local_text = (
                    local_analysis.get("face_action_result")
                    or local_analysis.get("local_only_text")
                    or ""
                )
            else:
                local_analysis = _run_local()
                local_text = (
                    local_analysis.get("face_action_result")
                    or local_analysis.get("local_only_text")
                    or ""
                )
                if cfg_vis["offline_only"] and local_text:
                    return (
                        f"{local_text} "
                        "(Offline local vision — nothing was sent to the cloud.)"
                    )
                parsed = None
        else:
            parsed = None
    except Exception as e:
        print(f"[VisionLocal] Local pass skipped: {e}")
        parsed = None

    try:
        if parsed is None:
            if use_camera_preview and hasattr(player, "set_camera_status"):
                player.set_camera_status("Identifying…")
            want_action = execute or mode in ("act", "click", "do")
            hints = local_hints_for_gemini(local_analysis) if local_analysis else ""
            parsed = _analyze_screen(
                image_bytes,
                mime_type,
                question,
                want_action=want_action and angle != "camera",
                is_camera=(angle == "camera"),
                local_hints=hints,
            )
        if parsed.get("error"):
            if local_text:
                return f"{local_text} (Cloud vision unavailable: {parsed['error']})"
            return parsed["error"]

        explanation = parsed.get("explanation") or "I couldn't read the screen clearly."
        parts = []
        if local_text and not cfg_vis.get("offline_only"):
            parts.append(local_text)
        parts.append(explanation)

        if want_action:
            act_result = _execute_action(parsed, player=player)
            if act_result:
                parts.append(f"Action: {act_result}")

        if params.get("save_screenshot"):
            try:
                from actions.computer_control import computer_control
                path = params.get("path") or "desktop/aria_screen.png"
                shot = computer_control(parameters={"action": "screenshot", "path": path}, player=player)
                parts.append(shot)
            except Exception:
                pass

        return " ".join(parts)
    finally:
        if show_border and hasattr(player, "hide_screen_border"):
            player.hide_screen_border()
        if use_camera_preview and hasattr(player, "hide_camera_preview"):
            player.hide_camera_preview()


def _capture_with_preview(angle: str, player, use_preview: bool) -> tuple[bytes, str]:
    if angle == "camera" and use_preview and player and hasattr(player, "get_camera_preview"):
        preview = player.get_camera_preview()
        if preview and preview.is_active():
            if hasattr(preview, "wait_for_frame"):
                preview.wait_for_frame(timeout=0.2)
            shot = preview.capture_jpeg()
            if shot:
                return shot
    return _capture(angle)
