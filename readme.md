# NEO

NEO is a cross-platform, voice-first desktop assistant powered by Gemini Live. It can answer questions, remember useful context, manage files and reminders, control supported applications and browsers, analyze the screen or camera on request, and execute bounded multi-step tasks.

## Requirements

- Python 3.11 or 3.12
- A Gemini API key
- Microphone access for voice use
- Windows 10/11, macOS 12+, or Linux

## Install

### Windows

```powershell
irm https://raw.githubusercontent.com/ArnavParashar49/ARIA/main/install.ps1 | iex
```

### macOS or Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ArnavParashar49/ARIA/main/install.sh | bash
```

The installer creates `~/.aria`, prepares a virtual environment, installs dependencies, and starts NEO.

## Manual setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create `.env` from `.env.example`, set `GEMINI_API_KEY`, then run:

```bash
python main.py
```

Say **“Hey Neo”**, double-clap, click the orb, or type in the chat.

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Architecture notes are in [hybrid/ARCHITECTURE.md](hybrid/ARCHITECTURE.md). Autonomous execution is documented in [AUTONOMOUS.md](AUTONOMOUS.md).

## Privacy

Wake-word detection runs locally. Audio is sent to Gemini only after activation. Screen and camera access occur only when requested. API keys, memory, MCP configuration, and runtime caches remain local and are excluded from Git. See [VISION_PRIVACY.md](VISION_PRIVACY.md).

## Uninstall

```powershell
# Windows
.\uninstall.ps1
```

```bash
# macOS/Linux
./uninstall.sh
```

License: CC BY-NC 4.0.
