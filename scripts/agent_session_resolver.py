#!/usr/bin/env python3
"""
agent_session_resolver.py
Resolves tmux window/tab names based on active AI agent harness sessions.
Supports:
  - agy (Antigravity CLI)
  - claude (Claude Code)
  - codex (Codex CLI)
  - aider (Aider CLI)
  - clean fallback for non-agent panes
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

AGENT_ICONS = {
    "agy": "✦",
    "claude": "✻",
    "codex": "⚙",
    "aider": "󰚩",
    "default": "🤖",
}


def sanitize_title(title: str, max_len: int = 25) -> str:
    """Sanitize title for safe display in tmux status line."""
    if not title:
        return ""
    # Strip ANSI escape sequences
    title = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", title)
    # Replace control characters and newlines with space
    title = re.sub(r"[\r\n\t]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    # Truncate if needed
    if max_len > 0 and len(title) > max_len:
        cutoff = max(1, max_len - 3)
        # Try word boundary
        truncated = title[:cutoff]
        last_space = truncated.rfind(" ")
        if last_space > cutoff // 2:
            truncated = truncated[:last_space]
        title = truncated.strip() + "..."
    return title


def get_descendants(root_pid: int) -> List[int]:
    """Get all descendant PIDs of root_pid."""
    if root_pid <= 1:
        return []

    pids = []
    queue = [root_pid]
    visited: Set[int] = {root_pid}

    # Attempt fast /proc/<pid>/task/<pid>/children lookup
    use_proc_children = os.path.exists(f"/proc/{root_pid}/task/{root_pid}/children")

    if use_proc_children:
        while queue:
            curr = queue.pop(0)
            children_path = f"/proc/{curr}/task/{curr}/children"
            try:
                with open(children_path, "r", encoding="utf-8") as f:
                    child_pids = [int(p) for p in f.read().split()]
                    for cp in child_pids:
                        if cp not in visited and cp > 1:
                            visited.add(cp)
                            pids.append(cp)
                            queue.append(cp)
            except (OSError, ValueError):
                continue
    else:
        # Fallback: scan /proc for PPID
        ppid_map: Dict[int, List[int]] = {}
        for entry in os.listdir("/proc"):
            if entry.isdigit():
                pid = int(entry)
                stat_path = f"/proc/{pid}/stat"
                try:
                    with open(stat_path, "r", encoding="utf-8") as f:
                        parts = f.read().split(")")
                        if len(parts) > 1:
                            stat_fields = parts[1].split()
                            ppid = int(stat_fields[1])
                            ppid_map.setdefault(ppid, []).append(pid)
                except (OSError, ValueError, IndexError):
                    continue

        while queue:
            curr = queue.pop(0)
            for child in ppid_map.get(curr, []):
                if child not in visited and child > 1:
                    visited.add(child)
                    pids.append(child)
                    queue.append(child)

    return pids


def get_process_info(pid: int) -> Tuple[str, str]:
    """Return (comm, cmdline) for a given pid."""
    comm = ""
    cmdline = ""
    try:
        with open(f"/proc/{pid}/comm", "r", encoding="utf-8") as f:
            comm = f.read().strip()
    except OSError:
        pass

    try:
        with open(f"/proc/{pid}/cmdline", "r", encoding="utf-8", errors="replace") as f:
            cmdline = f.read().replace("\x00", " ").strip()
    except OSError:
        pass

    return comm, cmdline


def find_agent_process(
    pane_pid: int, pane_cmd: str
) -> Optional[Tuple[str, int]]:
    """
    Find if pane_pid or any of its descendants is a supported agent harness.
    Returns (agent_name, pid) or None.
    Traverses top-down so parent agent processes take precedence over child tools.
    """
    if pane_pid <= 1:
        return None

    candidates = [pane_pid] + get_descendants(pane_pid)

    for pid in candidates:
        comm, cmdline = get_process_info(pid)
        tokens = cmdline.split()
        first_token = os.path.basename(tokens[0]) if tokens else ""

        # 1. agy
        if comm == "agy" or first_token == "agy" or first_token.startswith("agy"):
            return "agy", pid

        # 2. claude
        if (
            comm == "claude"
            or first_token in ("claude", "claude-code")
            or ("node" in comm and any("claude" in t for t in tokens[:3]))
        ):
            return "claude", pid

        # 3. codex
        if comm == "codex" or first_token == "codex":
            return "codex", pid

        # 4. aider
        if (
            comm == "aider"
            or first_token == "aider"
            or ("python" in comm and any("aider" in t for t in tokens[:3]))
        ):
            return "aider", pid

    # Also check pane_cmd as a fallback
    lowered_cmd = pane_cmd.lower()
    if lowered_cmd == "agy":
        return "agy", pane_pid
    if lowered_cmd in ("claude", "claude-code"):
        return "claude", pane_pid
    if lowered_cmd == "codex":
        return "codex", pane_pid
    if lowered_cmd == "aider":
        return "aider", pane_pid

    return None


def resolve_agy_title(pid: int, pane_path: str, empty_title: str) -> str:
    """Resolve session title for an agy (Antigravity CLI) process."""
    # Find open presence lock fd
    conv_id = None
    fd_dir = f"/proc/{pid}/fd"
    if os.path.isdir(fd_dir):
        try:
            for fd_name in os.listdir(fd_dir):
                fd_path = os.path.join(fd_dir, fd_name)
                try:
                    target = os.readlink(fd_path)
                    if "/presence/" in target and target.endswith(".lock"):
                        conv_id = os.path.basename(target)[:-5]
                        break
                except OSError:
                    continue
        except OSError:
            pass

    if conv_id:
        db_path = os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")
        if os.path.exists(db_path):
            try:
                # Open read-only and non-blocking
                conn = sqlite3.connect(
                    f"file:{db_path}?mode=ro", uri=True, timeout=0.5
                )
                c = conn.cursor()
                c.execute(
                    "SELECT title, preview, workspace_uris FROM conversation_summaries WHERE conversation_id = ?",
                    (conv_id,),
                )
                row = c.fetchone()
                conn.close()

                if row:
                    title, preview, workspace_uris = row[0], row[1], row[2]
                    if title and title.strip():
                        return title.strip()
                    if preview and preview.strip():
                        return preview.strip()
                    if workspace_uris:
                        try:
                            uris = json.loads(workspace_uris)
                            if uris and isinstance(uris, list):
                                folder = os.path.basename(uris[0].rstrip("/"))
                                if folder:
                                    return folder
                        except Exception:
                            pass
            except Exception:
                pass

        # If conv_id found but no summary title in DB yet:
        if pane_path:
            folder = os.path.basename(pane_path.rstrip("/"))
            if folder:
                return folder

    # Fallback to current path folder name or empty_title
    if pane_path:
        folder = os.path.basename(pane_path.rstrip("/"))
        if folder:
            return folder

    return empty_title


def resolve_claude_title(
    pid: int, pane_path: str, pane_title: str, empty_title: str
) -> str:
    """Resolve session title for Claude Code."""
    # 1. Check pane_title if it has something descriptive
    if pane_title and pane_title not in ("claude", "node", "zsh", "bash", ""):
        # Claude often sets titles like "Claude - task description"
        clean = re.sub(r"^Claude\s*[-:–]?\s*", "", pane_title, flags=re.I).strip()
        if clean:
            return clean

    # 2. Check Claude Code project history in ~/.claude/projects/
    cwd = pane_path
    try:
        proc_cwd = os.readlink(f"/proc/{pid}/cwd")
        if os.path.isdir(proc_cwd):
            cwd = proc_cwd
    except OSError:
        pass

    if cwd:
        # Claude converts path into slug where non-alphanumeric is '-'
        slug = re.sub(r"[^a-zA-Z0-9]", "-", cwd)
        claude_dir = os.path.expanduser(f"~/.claude/projects/{slug}")
        if os.path.isdir(claude_dir):
            try:
                # Find newest .jsonl
                jsonl_files = sorted(
                    glob.glob(os.path.join(claude_dir, "*.jsonl")),
                    key=os.path.getmtime,
                    reverse=True,
                )
                if jsonl_files:
                    latest_file = jsonl_files[0]
                    # Read the first few lines to find custom title or first prompt
                    with open(latest_file, "r", encoding="utf-8", errors="ignore") as f:
                        for _ in range(20):
                            line = f.readline()
                            if not line:
                                break
                            try:
                                data = json.loads(line)
                                if "custom_title" in data and data["custom_title"]:
                                    return str(data["custom_title"]).strip()
                                if "title" in data and data["title"]:
                                    return str(data["title"]).strip()
                                # Check user message content
                                if data.get("type") == "user" or data.get("role") == "user":
                                    msg = data.get("message") or data.get("content") or ""
                                    if isinstance(msg, str) and msg.strip():
                                        return msg.strip()
                            except Exception:
                                continue
            except Exception:
                pass

        # Fallback to project directory name
        folder = os.path.basename(cwd.rstrip("/"))
        if folder:
            return folder

    return empty_title


def resolve_codex_title(
    pid: int, pane_path: str, pane_title: str, empty_title: str
) -> str:
    """Resolve session title for Codex CLI."""
    if pane_title and pane_title not in ("codex", "zsh", "bash", ""):
        clean = re.sub(r"^codex\s*[-:–]?\s*", "", pane_title, flags=re.I).strip()
        if clean:
            return clean

    cwd = pane_path
    try:
        proc_cwd = os.readlink(f"/proc/{pid}/cwd")
        if os.path.isdir(proc_cwd):
            cwd = proc_cwd
    except OSError:
        pass

    if cwd:
        folder = os.path.basename(cwd.rstrip("/"))
        if folder:
            return folder

    return empty_title


def resolve_aider_title(
    pid: int, pane_path: str, pane_title: str, empty_title: str
) -> str:
    """Resolve session title for Aider CLI."""
    if pane_title and pane_title not in ("aider", "python", "zsh", "bash", ""):
        clean = re.sub(r"^aider\s*[-:–]?\s*", "", pane_title, flags=re.I).strip()
        if clean:
            return clean

    cwd = pane_path
    try:
        proc_cwd = os.readlink(f"/proc/{pid}/cwd")
        if os.path.isdir(proc_cwd):
            cwd = proc_cwd
    except OSError:
        pass

    if cwd:
        folder = os.path.basename(cwd.rstrip("/"))
        if folder:
            return folder

    return empty_title


def resolve(
    pane_pid_str: str,
    pane_cmd: str,
    pane_path: str,
    pane_title: str,
    format_template: str,
    max_len: int,
    empty_title: str,
    fallback: str,
    use_icons: bool,
) -> str:
    """Main resolution logic."""
    try:
        pane_pid = int(pane_pid_str)
    except (ValueError, TypeError):
        return fallback.replace("#{pane_current_command}", pane_cmd)

    agent_match = find_agent_process(pane_pid, pane_cmd)
    if not agent_match:
        # Not an agent harness, return fallback
        return fallback.replace("#{pane_current_command}", pane_cmd)

    agent_name, agent_pid = agent_match

    # Extract session title
    raw_title = ""
    if agent_name == "agy":
        raw_title = resolve_agy_title(agent_pid, pane_path, empty_title)
    elif agent_name == "claude":
        raw_title = resolve_claude_title(agent_pid, pane_path, pane_title, empty_title)
    elif agent_name == "codex":
        raw_title = resolve_codex_title(agent_pid, pane_path, pane_title, empty_title)
    elif agent_name == "aider":
        raw_title = resolve_aider_title(agent_pid, pane_path, pane_title, empty_title)

    if not raw_title:
        raw_title = empty_title

    title = sanitize_title(raw_title, max_len=max_len)
    icon = AGENT_ICONS.get(agent_name, AGENT_ICONS["default"]) if use_icons else ""

    result = format_template
    result = result.replace("#{agent}", agent_name)
    result = result.replace("#{title}", title)
    result = result.replace("#{icon}", icon)

    # Clean up formatting artifact if icon is empty
    result = re.sub(r"^\s+", "", result)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Resolve tmux window name based on agent harness session."
    )
    parser.add_argument("--pane-pid", default="", help="Pane PID")
    parser.add_argument("--pane-cmd", default="", help="Pane current command")
    parser.add_argument("--pane-path", default="", help="Pane current working directory")
    parser.add_argument("--pane-title", default="", help="Pane title")
    parser.add_argument(
        "--format", default="#{agent}[#{title}]", help="Agent tab format string"
    )
    parser.add_argument(
        "--max-len", type=int, default=25, help="Maximum title length (0 for unlimited)"
    )
    parser.add_argument(
        "--empty-title", default="new session", help="Fallback title if unknown"
    )
    parser.add_argument(
        "--fallback", default="#{pane_current_command}", help="Fallback when not an agent"
    )
    parser.add_argument(
        "--use-icons", action="store_true", help="Include agent icons in #{icon}"
    )

    args = parser.parse_args()

    try:
        output = resolve(
            pane_pid_str=args.pane_pid,
            pane_cmd=args.pane_cmd,
            pane_path=args.pane_path,
            pane_title=args.pane_title,
            format_template=args.format,
            max_len=args.max_len,
            empty_title=args.empty_title,
            fallback=args.fallback,
            use_icons=args.use_icons,
        )
        print(output, end="")
    except Exception:
        # In any unhandled failure, output pane_cmd safely so tmux tab never breaks
        print(args.pane_cmd or "tmux", end="")


if __name__ == "__main__":
    main()
