#!/usr/bin/env bash
set -e

INSTALL_DIR="$HOME/.aria"

echo "======================================"
echo "    ARIA Background AI Uninstaller    "
echo "======================================"

echo "Stopping ARIA processes..."
if [[ "$OSTYPE" == "darwin"* ]]; then
    # Stop Mac App
    osascript -e 'quit app "ARIA"' 2>/dev/null || true
    
    # Remove Login Item
    osascript -e 'tell application "System Events" to delete login item "ARIA"' 2>/dev/null || true
fi

# General kill based on folder path
pkill -f "$INSTALL_DIR/.venv/bin/python main.py" || true

echo "Removing ARIA installation from $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"

echo "======================================"
echo "        Uninstallation Complete.      "
echo "======================================"
