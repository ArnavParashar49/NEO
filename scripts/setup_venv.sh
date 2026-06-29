#!/usr/bin/env bash
# Recreate a clean virtual environment and install dependencies for ARIA on macOS

set -e

# Remove existing venv if any
rm -rf .venv

# Prefer Python 3.12, with 3.11 as the supported fallback.
PYTHON=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    major=${ver%%.*}
    minor=${ver#*.}
    if [ "$major" -eq 3 ] && { [ "$minor" -eq 11 ] || [ "$minor" -eq 12 ]; }; then
      PYTHON=$candidate
      break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Python 3.11 or 3.12 required. Install from https://www.python.org/downloads/ or: brew install python@3.12"
  exit 1
fi
echo "Using $PYTHON ($($PYTHON --version))"
"$PYTHON" -m venv .venv

# Activate venv
source .venv/bin/activate

# Upgrade pip, setuptools, wheel, and install certifi first
pip install --upgrade pip setuptools wheel certifi

# Install project dependencies
pip install -r requirements.txt

# Pre-download the ChromaDB local embedding model so it's ready without delay
echo "Downloading ChromaDB local embedding model (~80MB)..."
python -c "import chromadb.utils.embedding_functions as ef; ef.DefaultEmbeddingFunction()(['init'])" || true

echo "✅ Virtual environment is set up and dependencies installed."
