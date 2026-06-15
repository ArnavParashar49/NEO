#!/usr/bin/env bash
set -e

REPO_URL="https://github.com/ArnavParashar49/ARIA.git"
INSTALL_DIR="$HOME/.aria"

echo "======================================"
echo "    ARIA Background AI Installer      "
echo "======================================"
echo "Installing to $INSTALL_DIR..."

if ! command -v git &> /dev/null; then
    echo "Error: git is not installed. Please install git first."
    exit 1
fi

if [ -d "$INSTALL_DIR" ]; then
    echo "Updating existing ARIA installation..."
    cd "$INSTALL_DIR"
    git pull origin main
else
    echo "Cloning ARIA repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

echo "Setting up Python environment..."
bash scripts/setup_venv.sh

echo "======================================"
echo "         Installation Complete!       "
echo "======================================"

if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Building macOS Menu Bar App and adding to Login Items..."
    bash scripts/build_macos_app.sh --login
    echo "Starting ARIA..."
    open ARIA.app
else
    echo "Starting ARIA background process..."
    nohup .venv/bin/python main.py > aria.log 2>&1 &
    echo "ARIA is now running in the background. Logs at $INSTALL_DIR/aria.log"
fi
