# ARIA vision & camera privacy

## When the camera is used

- The webcam turns on **only** when you ask for camera vision (e.g. “what do you see on the camera”, “what am I holding”, “remember this as …”).
- A small preview window shows the live feed so you can see that the camera is active.
- The camera is **released** when the request finishes.

## What stays on your Mac (offline)

| Feature | Where data goes |
|--------|------------------|
| **YOLO labels** | Runs on your CPU/GPU. No upload. |
| **Face memory (DeepFace)** | Photos saved under `memory/face_db/` only on this computer. |
| **Face registry** | Names/metadata in `memory/face_registry.json` (local). |

## What may use the internet

- **Gemini** (optional): If `local_vision_offline` is `false` in `config/api_keys.json`, a snapshot may be sent to Google’s API for a richer spoken description. Local YOLO/face results can be included as hints only.
- Set `"local_vision_offline": true` to use **only** local vision for camera/screen describe (no cloud image upload).

## Safety tips

1. Grant **Camera** permission only to the app you use to run ARIA (Terminal, Cursor, etc.).
2. Review saved faces in `memory/face_db/` — delete folders to remove someone.
3. Do not use face remember on others without their consent.
4. First run downloads YOLO/DeepFace models from the internet once; after that, labeling and face match work offline.

## Removing face data

Delete folders under `memory/face_db/` and edit `memory/face_registry.json`, or remove the file to clear all remembered people.

