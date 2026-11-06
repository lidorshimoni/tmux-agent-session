#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(command -v python3 || command -v python)"

echo "==> Running tmux-agent-session test suite..."
"$PYTHON_BIN" "$SCRIPT_DIR/test_resolver.py" -v
echo "==> All tests passed successfully!"
