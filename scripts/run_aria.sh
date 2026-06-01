#!/usr/bin/env bash
# Activate the ARIA virtual environment and launch the application

set -e

# Ensure the virtual environment exists
if [ ! -d "./.venv" ]; then
  echo "Virtual environment not found. Please run scripts/setup_venv.sh first."
  exit 1
fi

# Activate the venv
source .venv/bin/activate

# Run the main application
python main.py "$@"
