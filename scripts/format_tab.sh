#!/usr/bin/env bash
# format_tab.sh - High-performance tmux tab formatter for agent harnesses

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

# Ultra-fast path: If running an idle shell with no children, return immediately (<1ms)
if [ -f "/proc/$PANE_PID/task/$PANE_PID/children" ]; then
    read -r _children < "/proc/$PANE_PID/task/$PANE_PID/children" 2>/dev/null
    if [ -z "$_children" ]; then
        case "$PANE_CMD" in
            zsh|bash|fish|sh|dash)
                printf "%s" "$PANE_CMD"
                exit 0
                ;;
        esac
    fi
fi

# Options passed directly from agent_session.tmux, avoiding redundant tmux show-option calls
FMT="${5:-}"
if [ -z "$FMT" ]; then
    FMT="$(tmux show-option -gqv @agent_session_format 2>/dev/null)"
    [ -z "$FMT" ] && FMT='#{agent}[#{title}]'
fi

MAX_LEN="${6:-}"
if [ -z "$MAX_LEN" ]; then
    MAX_LEN="$(tmux show-option -gqv @agent_session_max_len 2>/dev/null)"
    [ -z "$MAX_LEN" ] && MAX_LEN="25"
fi

EMPTY_TITLE="${7:-}"
if [ -z "$EMPTY_TITLE" ]; then
    EMPTY_TITLE="$(tmux show-option -gqv @agent_session_empty_title 2>/dev/null)"
    [ -z "$EMPTY_TITLE" ] && EMPTY_TITLE="new session"
fi

FALLBACK="${8:-}"
if [ -z "$FALLBACK" ]; then
    FALLBACK="$(tmux show-option -gqv @agent_session_fallback 2>/dev/null)"
    [ -z "$FALLBACK" ] && FALLBACK='#{pane_current_command}'
fi

ICONS_FLAG="${9:-}"
if [ -z "$ICONS_FLAG" ]; then
    USE_ICONS="$(tmux show-option -gqv @agent_session_use_icons 2>/dev/null)"
    if [ "$USE_ICONS" = "on" ] || [ "$USE_ICONS" = "true" ] || [ "$USE_ICONS" = "1" ]; then
        ICONS_FLAG="--use-icons"
    fi
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/agent_session_resolver.py" \
    "$PANE_PID" \
    "$PANE_CMD" \
    "$PANE_PATH" \
    "$PANE_TITLE" \
    "$FMT" \
    "$MAX_LEN" \
    "$EMPTY_TITLE" \
    "$FALLBACK" \
    "$ICONS_FLAG"
