#!/usr/bin/env bash
# ==============================================================================
# tmux-agent-session
# TPM entrypoint script to automatically rename tmux tabs to active AI session
# ==============================================================================

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure scripts have execute permissions
chmod +x "$CURRENT_DIR/scripts/format_tab.sh" "$CURRENT_DIR/scripts/agent_session_resolver.py" 2>/dev/null

resolver="$CURRENT_DIR/scripts/format_tab.sh"

# Read user options once at initialization
FMT="$(tmux show-option -gqv @agent_session_format)"
[ -z "$FMT" ] && FMT='#{agent}[#{title}]'

MAX_LEN="$(tmux show-option -gqv @agent_session_max_len)"
[ -z "$MAX_LEN" ] && MAX_LEN="25"

EMPTY_TITLE="$(tmux show-option -gqv @agent_session_empty_title)"
[ -z "$EMPTY_TITLE" ] && EMPTY_TITLE="new session"

FALLBACK="$(tmux show-option -gqv @agent_session_fallback)"
[ -z "$FALLBACK" ] && FALLBACK='#{pane_current_command}'

USE_ICONS="$(tmux show-option -gqv @agent_session_use_icons)"
ICONS_FLAG=""
if [ "$USE_ICONS" = "on" ] || [ "$USE_ICONS" = "true" ] || [ "$USE_ICONS" = "1" ]; then
    ICONS_FLAG="--use-icons"
fi

# Escape '#' to '##' so tmux format engine doesn't expand #{agent}, #{title}, #{icon} into empty strings
ESCAPED_FMT="$(echo "$FMT" | sed 's/#/##/g')"
ESCAPED_FALLBACK="$(echo "$FALLBACK" | sed 's/#/##/g')"

# Build job using #{q:...} for shell-safe parameter escaping
job="#($resolver #{q:pane_pid} #{q:pane_current_command} #{q:pane_current_path} #{q:pane_title} '$ESCAPED_FMT' '$MAX_LEN' '$EMPTY_TITLE' '$ESCAPED_FALLBACK' '$ICONS_FLAG')"

# Configure tmux automatic rename globally and for windows
tmux set-option -gw automatic-rename on
tmux set-option -gw automatic-rename-format "$job"
tmux set-option -g automatic-rename on
tmux set-option -g automatic-rename-format "$job"

# Optional status-interval override
interval="$(tmux show-option -gqv @agent_session_status_interval)"
if [ -n "$interval" ]; then
    tmux set-option -g status-interval "$interval"
fi

# Snappy refresh hooks on pane and window changes
tmux set-hook -g 'after-select-window[9182]' "refresh-client -S"
tmux set-hook -g 'after-select-pane[9182]'   "refresh-client -S"
tmux set-hook -g 'client-focus-in[9182]'     "refresh-client -S"
