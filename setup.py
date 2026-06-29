import subprocess
import sys

if sys.version_info[:2] not in {(3, 11), (3, 12)}:
    raise SystemExit(
        "NEO requires Python 3.11 or 3.12. "
        f"Current interpreter: {sys.version.split()[0]}"
    )

print("Installing requirements...")
subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)

print("Installing Playwright browsers...")
subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)

print("\n✅ Setup complete! Run 'python main.py' to start NEO.")


