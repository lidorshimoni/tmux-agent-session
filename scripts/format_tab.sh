#!/usr/bin/env bash
# format_tab.sh - Tmux tab formatter for agent harnesses

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)"

PANE_PID="${1:-}"
PANE_CMD="${2:-}"
PANE_PATH="${3:-}"
PANE_TITLE="${4:-}"

# Fast exit if python is not available or PID is empty
if [ -z "$PYTHON_BIN" ] || [ -z "$PANE_PID" ]; then
    printf "%s" "$PANE_CMD"
    exit 0
fi

# Retrieve tmux options or sensible defaults
FMT="$(tmux show-option -gqv @agent_session_format 2>/dev/null)"
if [ -z "$FMT" ]; then
    FMT='#{agent}[#{title}]'
fi

MAX_LEN="$(tmux show-option -gqv @agent_session_max_len 2>/dev/null)"
if [ -z "$MAX_LEN" ]; then
    MAX_LEN="25"
fi

EMPTY_TITLE="$(tmux show-option -gqv @agent_session_empty_title 2>/dev/null)"
if [ -z "$EMPTY_TITLE" ]; then
    EMPTY_TITLE="new session"
fi

FALLBACK="$(tmux show-option -gqv @agent_session_fallback 2>/dev/null)"
if [ -z "$FALLBACK" ]; then
    FALLBACK='#{pane_current_command}'
fi

USE_ICONS="$(tmux show-option -gqv @agent_session_use_icons 2>/dev/null)"

ICONS_FLAG=""
if [ "$USE_ICONS" = "on" ] || [ "$USE_ICONS" = "true" ] || [ "$USE_ICONS" = "1" ]; then
    ICONS_FLAG="--use-icons"
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/agent_session_resolver.py" \
    --pane-pid "$PANE_PID" \
    --pane-cmd "$PANE_CMD" \
    --pane-path "$PANE_PATH" \
    --pane-title "$PANE_TITLE" \
    --format "$FMT" \
    --max-len "$MAX_LEN" \
    --empty-title "$EMPTY_TITLE" \
    --fallback "$FALLBACK" \
    $ICONS_FLAG
