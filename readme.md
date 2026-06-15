<div align="center">

<img src="assets/icon.png" width="120" alt="ARIA"/>

# ARIA

**A real-time, voice-first AI companion that lives in your menu bar — hears, sees, remembers, and controls your computer.**

Powered by Gemini Live · runs locally · cross-platform · zero subscriptions

[![Python](https://img.shields.io/badge/python-3.11_|_3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS_|_Windows_|_Linux-22a89c)](#-requirements)
[![UI](https://img.shields.io/badge/UI-PyQt6-41CD52?logo=qt&logoColor=white)](https://pypi.org/project/PyQt6/)
[![License](https://img.shields.io/badge/license-CC_BY--NC_4.0-lightgrey)](https://creativecommons.org/licenses/by-nc/4.0/)

</div>

---

## What is ARIA?

ARIA is a personal AI assistant that sits quietly in your menu bar as a cyan pixel-robot. Simply click it or say **"Hey Aria"** to initiate a seamless, real-time voice conversation. Through natural dialogue, ARIA can manage your files, control your applications, interpret your screen and webcam, search the web, manage emails, and autonomously execute complex multi-step goals.

Operating locally on your machine with your own free Gemini API key, ARIA ensures full privacy with no cloud dependencies, subscriptions, or telemetry.

---

## ✨ Highlights

| | |
|---|---|
| 🤖 **Desktop buddy** | A pixel-robot that lives in the menu bar. Click to summon, click to tuck away — hover and click animations included. |
| 🎙️ **Real-time voice** | Ultra-low-latency spoken conversation via Gemini Live, in any language. |
| 🗣️ **"Hey Aria" wake word** | Fully local wake-word + double-clap detection (Vosk) — no audio leaves your machine to listen. |
| 👁️ **Sees your world** | Reads your screen and webcam on demand. On-device object & face detection (YOLOv8 + DeepFace). |
| 🧠 **Persistent memory** | Remembers your projects, preferences, contacts, and people across sessions. |
| 🛠️ **Controls your computer** | Launch apps, manage files, run commands, control the browser, adjust system settings, generate presentations. |
| 🧩 **Autonomous mode** | Give it a goal; it decides one action at a time, sees the real result, and keeps going until it's done. |
| 📂 **File-aware** | Drag in PDFs, code, or images to summarize, analyze, or edit instantly. |
| ⌨️ **Type or talk** | Seamlessly switch between the chat box and your voice. |
| 🖥️ **Cross-platform** | macOS, Windows, and Linux, with OS auto-detection. |

---

## 🚀 Installation

Run the single-line command for your platform in your terminal to automatically download, install, and start ARIA.

### macOS / Linux
```bash
curl -sSL https://raw.githubusercontent.com/ArnavParashar49/ARIA/main/install.sh | bash
```

### Windows
```powershell
iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/ArnavParashar49/ARIA/main/install.ps1'))
```

> **Note:** On your first launch, ARIA will prompt you to enter your Gemini API key (from [aistudio.google.com](https://aistudio.google.com)). She will also download a small (~40 MB) local speech model for the wake word, and vision weights (`yolov8n.pt`) if missing. 

---

## 🗑️ Uninstallation

To completely remove ARIA and her background services from your system:

### macOS / Linux
```bash
curl -sSL https://raw.githubusercontent.com/ArnavParashar49/ARIA/main/uninstall.sh | bash
```

### Windows
```powershell
iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/ArnavParashar49/ARIA/main/uninstall.ps1'))
```

---

## ⚙️ Configuration

Everything lives in `config/api_keys.json` (gitignored — your key never leaves your machine):

| Key | Default | What it does |
|---|---|---|
| `gemini_api_key` | — | Your free key from [aistudio.google.com](https://aistudio.google.com) |
| `os_system` | `auto` | OS detection — leave on `auto` |
| `autonomous_mode` | `true` | Let ARIA plan & execute multi-step goals on its own ([details](AUTONOMOUS.md)) |
| `local_vision` / `yolo_enabled` | `true` | On-device screen/webcam object detection (YOLOv8) |
| `face_recognition_enabled` | `true` | Recognize people you've introduced ([privacy](VISION_PRIVACY.md)) |
| `siri_bar` | `top-right` | Where the buddy docks (corner + margins) |
| `noise_gate` | `true` | Suppress background noise on the mic |
| `prefer_gemini_lite` | `true` | Use the faster/cheaper model where it suffices |

---

## 🔒 Privacy

- Your API key and personal memory (`config/api_keys.json`, `memory/`) are **gitignored** and stay strictly local.
- Wake-word listening is **fully on-device** — audio is only sent to Gemini *after* you actively summon ARIA.
- Vision runs locally (YOLOv8 + DeepFace). See **[VISION_PRIVACY.md](VISION_PRIVACY.md)** for what's processed and stored.

---

## 📋 Requirements

| | |
|---|---|
| **OS** | macOS 12+, Windows 10/11, or Linux |
| **Python** | 3.11 or 3.12 |
| **Microphone** | Required for voice interaction |
| **Webcam** | Optional, for vision features |
| **API key** | Free Gemini key ([aistudio.google.com](https://aistudio.google.com)) |

> Some OS-specific packages aren't bundled to keep `requirements.txt` light. If you encounter a `ModuleNotFoundError`, install that specific package for your platform with `pip install <module>`.

---

## 🩺 Troubleshooting

- **macOS: `Operation not permitted` reading `.venv`** — This occurs if installed in a TCC-protected folder. **Ensure ARIA is installed outside of `~/Downloads`, `~/Desktop`, and `~/Documents`** (e.g., `~/.aria`).
- **No microphone / "wake word disabled"** — Grant Microphone permission in your OS Settings → Privacy, then relaunch.
- **"Hey Aria" not triggering** — Speak it naturally as one connected phrase. A double-clap serves as a reliable fallback.
- **`playwright` errors** — Run `playwright install` manually if the automated installation failed.

---

## ⚠️ License

Personal and non-commercial use only — **[Creative Commons BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)**.

---

<div align="center">

Built by **AP** — your real-world personal AI assistant.

⭐ **Star the repo if ARIA helps you.**

</div>
