# tmux-agent-session 🤖

A lightweight, zero-dependency tmux plugin that automatically renames tmux tabs (windows) to display the active AI agent session name in real-time.

```text
 1 agy[Cell Reception App Planning]  2 claude[Refactor Auth API]  3 zsh 
```

Whenever you run an AI coding agent harness such as **Antigravity CLI (`agy`)**, **Claude Code (`claude`)**, **Codex (`codex`)**, or **Aider (`aider`)**, `tmux-agent-session` detects the running process, inspects the active session state, and dynamically renames the window tab. When the agent exits back to your shell, the tab reverts cleanly.

---

## ✨ Features

- **Multi-Harness Support**:
  - **`agy` (Antigravity CLI)**: Resolves active conversation ID via `/proc/<pid>/fd` presence locks and queries `conversation_summaries.db` in read-only WAL mode.
  - **`claude` (Claude Code)**: Extracts current session title or prompt from project history and terminal titles with clean directory fallbacks.
  - **`codex`**: Detects active session context or project workspace.
  - **`aider`**: Displays current repository/workspace context or task title.
- **Zero Heavy Dependencies**: Built entirely with POSIX bash and Python 3 standard library (`sqlite3`, `json`, `os`, `re`). No extra pip modules or bloated background daemons needed.
- **Safe & Non-blocking**: Uses read-only, non-blocking SQLite access (`mode=ro`) and native tmux `automatic-rename-format`, guaranteeing your databases never lock and your status line never stutters.
- **Clean Fallbacks**: When switching between tools or shells (e.g. `zsh`, `vim`, `htop`), tabs smoothly reflect the running command.
- **Customizable**: Customize title formats, truncate lengths with smart word boundaries, toggle icons, or configure fallback titles.
- **Compatible**: Works seamlessly with tmux status themes like Dracula, Catppuccin, Nord, and Powerline.

---

## 🚀 Installation

### Using TPM (Tmux Plugin Manager) - Recommended

1. Add the plugin to your `~/.tmux.conf`:
   ```tmux
   set -g @plugin 'lidorshimoni/tmux-agent-session'
   ```

2. Press `prefix` + `I` (default `Ctrl-b` then `I`) to install and reload plugins.

### Manual Installation

1. Clone this repository into your tmux plugins directory:
   ```bash
   git clone https://github.com/lidorshimoni/tmux-agent-session ~/.tmux/plugins/tmux-agent-session
   ```

2. Add this line to the bottom of your `~/.tmux.conf`:
   ```tmux
   run-shell ~/.tmux/plugins/tmux-agent-session/agent_session.tmux
   ```

3. Reload your tmux config:
   ```bash
   tmux source-file ~/.tmux.conf
   ```

---

## ⚙️ Configuration Options

Add any of these options to your `~/.tmux.conf` before `run '~/.tmux/plugins/tpm/tpm'`:

| Option | Default | Description |
| :--- | :--- | :--- |
| `@agent_session_format` | `#{agent}[#{title}]` | Format template for active agent windows. Supports `#{agent}`, `#{title}`, and `#{icon}`. |
| `@agent_session_max_len` | `25` | Maximum length of session title before truncating with `...`. Set `0` for unlimited. |
| `@agent_session_empty_title` | `new session` | Placeholder displayed when an agent is active but hasn't received a session title yet. |
| `@agent_session_fallback` | `#{pane_current_command}` | Format used when no agent harness is active (e.g. shell or editor). |
| `@agent_session_use_icons` | `off` | Enable agent icons (`on` or `off`). When enabled, `#{icon}` is populated (✦ for agy, ✻ for claude, ⚙ for codex, 󰚩 for aider). |
| `@agent_session_status_interval` | *(inherited)* | Optional status bar refresh interval in seconds (e.g. `2`). |

### Configuration Examples

#### 1. Compact with Icons
```tmux
set -g @agent_session_use_icons 'on'
set -g @agent_session_format '#{icon} #{title}'
set -g @agent_session_max_len 20
```

#### 2. Descriptive Bracket Format
```tmux
set -g @agent_session_format '[#{agent}] #{title}'
set -g @agent_session_max_len 30
```

#### 3. Snappy Real-time Refresh
For faster tab name updates when an agent updates its title:
```tmux
set -g status-interval 2
```

---

## 🔍 How It Works

1. **Process Discovery**: When tmux updates its windows, the plugin identifies the active pane's process tree top-down.
2. **Presence & State Resolution**:
   - For `agy`, it reads open file descriptors pointing to `~/.gemini/antigravity-cli/presence/<conversation_id>.lock`.
   - It queries `conversation_summaries.db` in SQLite read-only mode (`?mode=ro`) to retrieve the live title or preview.
   - For `claude`, it checks `~/.claude/projects/` transcripts and terminal title escape sequences.
3. **Smart Formatting**: Titles are sanitized (ANSI stripped, control characters cleaned) and intelligently truncated at word boundaries.
4. **Native Integration**: Leverages tmux's built-in `automatic-rename-format` and fast pane navigation hooks (`after-select-window`, `pane-focus-in`), ensuring zero tab flickering and instantaneous fallback when an agent process exits.

---

## 🧪 Testing

Run the included test suite:
```bash
./tests/run_tests.sh
```

---

## 📄 License

MIT © [Lidor Shimoni](https://github.com/lidorshimoni)
