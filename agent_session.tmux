#!/usr/bin/env bash
# ==============================================================================
# tmux-agent-session
# TPM entrypoint script to automatically rename tmux tabs to active AI session
# ==============================================================================

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure scripts have execute permissions
chmod +x "$CURRENT_DIR/scripts/format_tab.sh" "$CURRENT_DIR/scripts/agent_session_resolver.py" 2>/dev/null

resolver="$CURRENT_DIR/scripts/format_tab.sh"
job="#($resolver '#{pane_pid}' '#{pane_current_command}' '#{pane_current_path}' '#{pane_title}')"

# Configure tmux automatic rename
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
tmux set-hook -g 'pane-focus-in[9182]'       "refresh-client -S"
