"""Local offline vision: YOLO object labels + DeepFace people memory."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

_BASE = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _BASE / "config" / "api_keys.json"
_FACE_DB = _BASE / "memory" / "face_db"
_REGISTRY_PATH = _BASE / "memory" / "face_registry.json"

_yolo_model = None
_yolo_lock = __import__("threading").Lock()

_REMEMBER_FACE_RE = re.compile(
    r"\bremember\s+(?:this\s+)?(?:person|face)?\s*(?:as\s+)?(.+?)(?:\.|$)",
    re.I,
)
_WHO_FACE_RE = re.compile(
    r"\b(who(?:'s| is)?\s+this|who am i|recognize (?:me|this face)|do you know me)\b",
    re.I,
)


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def vision_config() -> dict:
    cfg = _load_config()
    return {
        "enabled": bool(cfg.get("local_vision", True)),
        "offline_only": bool(cfg.get("local_vision_offline", False)),
        "yolo": bool(cfg.get("yolo_enabled", True)),
        "faces": bool(cfg.get("face_recognition_enabled", True)),
        "yolo_model": str(cfg.get("yolo_model", "yolov8n.pt")),
        "yolo_conf": float(cfg.get("yolo_confidence", 0.42)),
        "yolo_infer_size": int(cfg.get("yolo_infer_size", 480)),
        "preload": bool(cfg.get("vision_preload", True)),
    }


_FACE_QUESTION_RE = re.compile(
    r"\b(who|person|people|face|faces|recognize|know me|remember|someone)\b",
    re.I,
)


def _needs_face_pass(question: str, intent: str) -> bool:
    if intent in ("who", "remember", "list"):
        return True
    return bool(_FACE_QUESTION_RE.search(question or ""))


def _resize_for_yolo(frame: np.ndarray, max_side: int) -> tuple[np.ndarray, float]:
    h, w = frame.shape[:2]
    if max(h, w) <= max_side:
        return frame, 1.0
    scale = max_side / float(max(h, w))
    import cv2

    nw = max(1, int(w * scale))
    nh = max(1, int(h * scale))
    small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    return small, scale


def preload_vision_models(background: bool = True) -> None:
    """Load YOLO in the background so the first camera ask is not blocked."""
    cfg = vision_config()
    if not cfg.get("enabled") or not cfg.get("preload") or not cfg.get("yolo"):
        return
    if not local_vision_available().get("yolo"):
        return

    def _load() -> None:
        try:
            _get_yolo()
            print("[VisionLocal] YOLO preloaded")
        except Exception as e:
            print(f"[VisionLocal] YOLO preload skipped: {e}")

    if background:
        threading.Thread(target=_load, daemon=True, name="VisionPreload").start()
    else:
        _load()


def camera_preview_config() -> dict:
    """Live preview capture/display tuning (see config/api_keys.json)."""
    cfg = _load_config()
    return {
        "width": int(cfg.get("camera_width", 1280)),
        "height": int(cfg.get("camera_height", 720)),
        "fps": int(cfg.get("camera_fps", 30)),
        "preview_fps": max(12, min(60, int(cfg.get("camera_preview_fps", 24)))),
        "yolo_live": bool(cfg.get("yolo_live_preview", False)),
        "yolo_interval_sec": max(0.35, float(cfg.get("yolo_preview_interval_sec", 0.65))),
    }


def apply_camera_capture_settings(cap) -> None:
    """Set resolution/FPS/buffer on an OpenCV VideoCapture."""
    if cap is None:
        return
    try:
        import cv2
    except ImportError:
        return
    cfg = camera_preview_config()
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["height"])
    cap.set(cv2.CAP_PROP_FPS, cfg["fps"])
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass


def local_vision_available() -> dict[str, bool]:
    out = {"yolo": False, "deepface": False}
    if vision_config()["yolo"]:
        try:
            import ultralytics  # noqa: F401
            out["yolo"] = True
        except ImportError:
            pass
    if vision_config()["faces"]:
        try:
            import deepface  # noqa: F401
            out["deepface"] = True
        except ImportError:
            pass
    return out


def _safe_person_name(name: str) -> str:
    return re.sub(r"[^\w\- ]", "", (name or "unknown").strip())[:40].replace(" ", "_")


def _load_registry() -> dict:
    if not _REGISTRY_PATH.exists():
        return {"people": {}}
    try:
        data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"people": {}}
    except Exception:
        return {"people": {}}


def _save_registry(data: dict) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def list_known_people() -> list[str]:
    reg = _load_registry()
    people = reg.get("people") or {}
    return sorted(people.keys())


def parse_face_intent(question: str) -> tuple[str, str]:
    """Return (intent, name): intent = remember | who | none."""
    q = (question or "").strip()
    m = _REMEMBER_FACE_RE.search(q)
    if m:
        return "remember", m.group(1).strip().rstrip(".")
    if _WHO_FACE_RE.search(q):
        return "who", ""
    if re.search(r"\blist (?:known )?faces\b|\bwho do you know\b", q, re.I):
        return "list", ""
    return "none", ""


def _get_yolo():
    global _yolo_model
    with _yolo_lock:
        if _yolo_model is not None:
            return _yolo_model
        from ultralytics import YOLO

        model_name = vision_config()["yolo_model"]
        print(f"[VisionLocal] Loading YOLO {model_name}…")
        _yolo_model = YOLO(model_name)
        return _yolo_model


def detect_objects(frame: np.ndarray) -> list[dict[str, Any]]:
    """Return detections: label, confidence, bbox [x1,y1,x2,y2]."""
    cfg = vision_config()
    if not cfg["enabled"] or not cfg["yolo"]:
        return []
    if frame is None or frame.size == 0:
        return []

    try:
        model = _get_yolo()
        conf = cfg["yolo_conf"]
        imgsz = int(cfg.get("yolo_infer_size", 480))
        small, scale = _resize_for_yolo(frame, imgsz)
        results = model(small, verbose=False, conf=conf, imgsz=imgsz)
        detections: list[dict[str, Any]] = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                cls_id = int(box.cls[0])
                label = model.names.get(cls_id, str(cls_id))
                score = float(box.conf[0])
                x1, y1, x2, y2 = [int(v / scale) for v in box.xyxy[0].tolist()]
                detections.append({
                    "label": label,
                    "confidence": round(score, 2),
                    "bbox": [x1, y1, x2, y2],
                })
        return detections
    except Exception as e:
        print(f"[VisionLocal] YOLO error: {e}")
        return []


def draw_detections(frame: np.ndarray, detections: list[dict]) -> np.ndarray:
    import cv2

    out = frame.copy()
    for d in detections:
        if d.get("type") == "face":
            continue
        bbox = d.get("bbox") or [0, 0, 0, 0]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        x1, y1, x2, y2 = bbox
        label = d["label"]
        conf = d.get("confidence", 0)
        text = f"{label} {conf:.0%}" if conf else label
        cv2.rectangle(out, (x1, y1), (x2, y2), (80, 220, 120), 2)
        cv2.putText(
            out, text, (x1, max(y1 - 6, 14)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 255, 140), 1, cv2.LINE_AA,
        )
    for f in detections:
        if f.get("type") != "face":
            continue
        x1, y1, x2, y2 = f["bbox"]
        name = f.get("label", "person")
        cv2.rectangle(out, (x1, y1), (x2, y2), (255, 180, 80), 2)
        cv2.putText(
            out, name, (x1, max(y1 - 6, 14)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 200, 100), 2, cv2.LINE_AA,
        )
    return out


def _frame_to_temp_jpg(frame: np.ndarray) -> str:
    import cv2

    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="aria_face_")
    import os
    os.close(fd)
    cv2.imwrite(path, frame)
    return path


def remember_person(frame: np.ndarray, name: str) -> str:
    cfg = vision_config()
    if not cfg["faces"]:
        return "Face memory is disabled in config."

    safe = _safe_person_name(name)
    if not safe:
        return "Please say a name to remember, like remember this as AP."

    avail = local_vision_available()
    if not avail["deepface"]:
        return "DeepFace not installed. Run: pip install deepface"

    import cv2
    from deepface import DeepFace

    person_dir = _FACE_DB / safe
    person_dir.mkdir(parents=True, exist_ok=True)
    img_path = person_dir / f"{int(time.time())}.jpg"
    cv2.imwrite(str(img_path), frame)

    try:
        DeepFace.represent(img_path=str(img_path), model_name="Facenet", enforce_detection=False)
    except Exception as e:
        print(f"[VisionLocal] DeepFace represent warning: {e}")

    reg = _load_registry()
    reg.setdefault("people", {})[safe] = {
        "display_name": name.strip(),
        "photos": (reg.get("people", {}).get(safe, {}).get("photos") or []) + [str(img_path)],
        "updated": time.strftime("%Y-%m-%d"),
    }
    reg["people"][safe]["photos"] = reg["people"][safe]["photos"][-5:]
    _save_registry(reg)

    try:
        from memory.memory_manager import update_memory

        safe = _safe_person_name(name)
        update_memory({"relationships": {safe: f"Recognized face — {name.strip()}"}})
    except Exception:
        pass

    return f"Got it — I'll remember {name.strip()} locally on this Mac. Face data stays in {person_dir.parent} only."


def recognize_people(frame: np.ndarray) -> list[dict[str, Any]]:
    cfg = vision_config()
    if not cfg["faces"] or not local_vision_available()["deepface"]:
        return []

    if not _FACE_DB.exists() or not any(_FACE_DB.iterdir()):
        return []

    import cv2
    from deepface import DeepFace

    tmp = _frame_to_temp_jpg(frame)
    try:
        dfs = DeepFace.find(
            img_path=tmp,
            db_path=str(_FACE_DB),
            model_name="Facenet",
            enforce_detection=False,
            silent=True,
        )
    except Exception as e:
        print(f"[VisionLocal] Face find error: {e}")
        return []
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass

    reg = _load_registry()
    known = reg.get("people") or {}
    matches: list[dict[str, Any]] = []

    if isinstance(dfs, list) and dfs and not dfs[0].empty:
        df = dfs[0]
        for _, row in df.head(3).iterrows():
            identity = str(row.get("identity", ""))
            dist = float(row.get("Facenet_cosine", row.get("distance", 1.0)))
            folder = Path(identity).parent.name
            display = known.get(folder, {}).get("display_name", folder.replace("_", " "))
            conf = max(0.0, min(1.0, 1.0 - dist))
            if conf < 0.35:
                continue
            matches.append({
                "name": display,
                "folder": folder,
                "confidence": round(conf, 2),
                "type": "face",
                "label": display,
                "bbox": [0, 0, 0, 0],
            })

    if not matches:
        return []

    return matches


def _summarize_objects(detections: list[dict]) -> str:
    if not detections:
        return ""
    counts: dict[str, int] = {}
    for d in detections:
        if d.get("type") == "face":
            continue
        lbl = d.get("label", "object")
        counts[lbl] = counts.get(lbl, 0) + 1
    parts = [f"{n} {lbl}{'s' if n > 1 else ''}" for lbl, n in sorted(counts.items(), key=lambda x: -x[1])]
    return "I see " + ", ".join(parts[:12]) + "."


def _summarize_faces(faces: list[dict], *, face_scan_ran: bool = False) -> str:
    """Summarize face matches. Empty list only speaks about faces if a scan actually ran."""
    if not faces:
        if not face_scan_ran:
            return ""
        known = list_known_people()
        if known:
            return (
                "I see a face but don't recognize them yet. "
                f"Known people: {', '.join(known)}. Say remember this as [name] to add someone."
            )
        return "I see a face but no one is saved yet. Say remember this as [name] to remember them."
    names = []
    for f in faces:
        n = f.get("name", "someone")
        c = f.get("confidence", 0)
        if c and c > 0.35:
            names.append(f"{n} ({int(c * 100)}% sure)")
        else:
            names.append(n)
    return "People I recognize: " + ", ".join(names) + "."


def analyze_frame(
    frame: np.ndarray,
    question: str = "",
    *,
    run_yolo: bool = True,
    run_faces: bool = True,
) -> dict[str, Any]:
    """Full local pass — objects, faces, optional remember/who."""
    cfg = vision_config()
    intent, face_name = parse_face_intent(question)

    result: dict[str, Any] = {
        "objects": [],
        "faces": [],
        "detections": [],
        "summary": "",
        "local_only_text": "",
        "face_action_result": "",
    }

    if not cfg["enabled"]:
        return result

    need_faces = (
        run_faces
        and cfg["faces"]
        and _needs_face_pass(question, intent)
    )
    face_scan_ran = False

    if run_yolo and cfg["yolo"] and need_faces and intent not in ("remember", "list"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            yo = pool.submit(detect_objects, frame)
            fc = pool.submit(recognize_people, frame)
            result["objects"] = yo.result()
            result["faces"] = fc.result()
            face_scan_ran = True
        result["detections"] = list(result["objects"]) + list(result["faces"])
    elif run_yolo and cfg["yolo"]:
        result["objects"] = detect_objects(frame)
        result["detections"] = list(result["objects"])

    if intent == "remember" and face_name:
        result["face_action_result"] = remember_person(frame, face_name)
        result["local_only_text"] = result["face_action_result"]
        return result

    if intent == "list":
        known = list_known_people()
        result["local_only_text"] = (
            "Known people: " + ", ".join(known) + "."
            if known
            else "No faces saved yet. Say remember this as [name] while looking at the camera."
        )
        return result

    if need_faces and not result["faces"] and intent in ("who", "none"):
        result["faces"] = recognize_people(frame)
        face_scan_ran = True
        result["detections"] = list(result.get("detections") or []) + list(result["faces"])

    obj_text = _summarize_objects(result["objects"])
    face_text = (
        _summarize_faces(result["faces"], face_scan_ran=face_scan_ran)
        if cfg["faces"]
        else ""
    )
    parts = [p for p in (obj_text, face_text) if p]
    result["summary"] = " ".join(parts)
    result["local_only_text"] = result["summary"]
    return result


def bytes_to_bgr(image_bytes: bytes) -> np.ndarray | None:
    import cv2

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return frame


def local_hints_for_gemini(analysis: dict) -> str:
    """Short hint block prepended to cloud vision prompt."""
    parts = []
    if analysis.get("objects"):
        parts.append("Local YOLO detected: " + ", ".join(
            f"{d['label']}({d['confidence']})" for d in analysis["objects"][:15]
        ))
    if analysis.get("faces"):
        parts.append("Local face match: " + ", ".join(
            f"{f['name']}({f.get('confidence', 0)})" for f in analysis["faces"]
        ))
    if analysis.get("face_action_result"):
        parts.append(analysis["face_action_result"])
    return "\n".join(parts) if parts else ""
