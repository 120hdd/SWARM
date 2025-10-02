#!/usr/bin/env bash

# Bootstrap script for SWARM.
# - Ensures Python 3 is available
# - Creates or reuses .venv
# - Installs project dependencies
# - Copies env.example to .env when needed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -n "${PYTHON:-}" ]; then
    PYTHON_BIN="$PYTHON"
else
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    else
        echo "Python 3 is required but was not found. Install Python 3.10+ and re-run." >&2
        exit 1
    fi
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "The specified Python interpreter '$PYTHON_BIN' is not on PATH." >&2
    exit 1
fi

VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if [ -f "$VENV_DIR/bin/activate" ]; then
    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    # shellcheck disable=SC1091
    source "$VENV_DIR/Scripts/activate"
else
    echo "Could not find an activation script in $VENV_DIR." >&2
    exit 1
fi

python -m pip install --upgrade pip

if [ ! -f "requirements.txt" ]; then
    echo "requirements.txt is missing. Cannot continue." >&2
    exit 1
fi

pip install -r requirements.txt

if [ ! -f ".env" ] && [ -f "env.example" ]; then
    cp env.example .env
    echo "Created .env from env.example. Update it with your keys before running SWARM."
fi

echo "Setup complete. Activate the environment with 'source $VENV_DIR/bin/activate' (macOS/Linux) or 'source $VENV_DIR/Scripts/activate' (Git Bash) and update .env."
