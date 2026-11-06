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

import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
from typing import Dict, List, Optional, Set, Tuple

AGENT_ICONS = {
    "agy": "✦",
    "claude": "✻",
    "codex": "⚙",
    "aider": "󰚩",
    "default": "🤖",
}

GENERIC_PANE_TITLES = {
    "zsh", "bash", "sh", "dash", "fish", "node", "python", "python3",
    "agy", "claude", "claude-code", "codex", "aider", "tmux", ""
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


def extract_text_from_message(msg) -> str:
    """Recursively extract plain text prompt from Claude/chat message structures."""
    if not msg:
        return ""
    if isinstance(msg, str):
        return msg.strip()
    if isinstance(msg, dict):
        text = msg.get("text") or msg.get("content") or ""
        return extract_text_from_message(text)
    if isinstance(msg, list):
        for item in msg:
            text = extract_text_from_message(item)
            if text:
                return text
    return ""


def get_descendants(root_pid: int) -> List[int]:
    """Get all descendant PIDs of root_pid across threads and fallback to pgrep on non-Linux."""
    if root_pid <= 1:
        return []

    pids: List[int] = []
    queue = [root_pid]
    visited: Set[int] = {root_pid}

    # 1. Linux /proc/<pid>/task/*/children
    if os.path.exists(f"/proc/{root_pid}"):
        task_dir = f"/proc/{root_pid}/task"
        has_task_dir = os.path.isdir(task_dir)
        use_children_file = False
        if has_task_dir:
            try:
                # Check if first thread has children file
                tids = os.listdir(task_dir)
                if tids and os.path.exists(f"{task_dir}/{tids[0]}/children"):
                    use_children_file = True
            except OSError:
                use_children_file = False

        if use_children_file:
            while queue:
                curr = queue.pop(0)
                curr_task_dir = f"/proc/{curr}/task"
                try:
                    for tid in os.listdir(curr_task_dir):
                        children_path = f"{curr_task_dir}/{tid}/children"
                        try:
                            with open(children_path, "r", encoding="utf-8") as f:
                                for cp_str in f.read().split():
                                    cp = int(cp_str)
                                    if cp not in visited and cp > 1:
                                        visited.add(cp)
                                        pids.append(cp)
                                        queue.append(cp)
                        except (OSError, ValueError):
                            continue
                except OSError:
                    continue
            return pids

        # Fallback: scan /proc for PPID using robust rpartition
        ppid_map: Dict[int, List[int]] = {}
        try:
            for entry in os.listdir("/proc"):
                if entry.isdigit():
                    pid = int(entry)
                    stat_path = f"/proc/{pid}/stat"
                    try:
                        with open(stat_path, "r", encoding="utf-8") as f:
                            _, _, rest = f.read().rpartition(")")
                            if rest:
                                stat_fields = rest.split()
                                ppid = int(stat_fields[1])
                                ppid_map.setdefault(ppid, []).append(pid)
                    except (OSError, ValueError, IndexError):
                        continue
        except OSError:
            pass

        while queue:
            curr = queue.pop(0)
            for child in ppid_map.get(curr, []):
                if child not in visited and child > 1:
                    visited.add(child)
                    pids.append(child)
                    queue.append(child)
        return pids

    # 2. Non-Linux (macOS / BSD) fallback using pgrep
    try:
        while queue:
            curr = queue.pop(0)
            res = subprocess.run(
                ["pgrep", "-P", str(curr)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            for line in res.stdout.split():
                try:
                    cp = int(line.strip())
                    if cp not in visited and cp > 1:
                        visited.add(cp)
                        pids.append(cp)
                        queue.append(cp)
                except ValueError:
                    continue
    except Exception:
        pass

    return pids


def get_process_info(pid: int) -> Tuple[str, str]:
    """Return (comm, cmdline) for a given pid."""
    comm = ""
    cmdline = ""
    # Try Linux /proc
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

    # Non-Linux fallback via ps
    if not comm and not cmdline:
        try:
            res = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm=,args="],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            out = res.stdout.strip()
            if out:
                parts = out.split(None, 1)
                comm = parts[0] if parts else ""
                cmdline = parts[1] if len(parts) > 1 else comm
        except Exception:
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
        if comm in ("agy", "antigravity") or first_token in ("agy", "antigravity") or first_token.startswith("agy-"):
            return "agy", pid

        # 2. claude
        if comm in ("claude", "claude-code") or first_token in ("claude", "claude-code"):
            return "claude", pid
        if "node" in comm or first_token == "node":
            for t in tokens[1:4]:
                base = os.path.basename(t)
                if base in ("claude", "claude-code", "claude.js", "claude-code.js"):
                    return "claude", pid
                if "@anthropic-ai/claude-code" in t or "/claude-code/" in t:
                    return "claude", pid

        # 3. codex
        if comm in ("codex", "codex-cli") or first_token in ("codex", "codex-cli") or first_token.startswith("codex-"):
            return "codex", pid

        # 4. aider
        if comm == "aider" or first_token == "aider":
            return "aider", pid
        if "python" in comm or first_token.startswith("python"):
            for i, t in enumerate(tokens[1:4], 1):
                if t == "-m" and i + 1 < len(tokens) and tokens[i + 1] == "aider":
                    return "aider", pid
                base = os.path.basename(t)
                if base in ("aider", "aider.py"):
                    return "aider", pid

    # Fallback check against pane_cmd
    lowered_cmd = pane_cmd.lower()
    if lowered_cmd in ("agy", "antigravity"):
        return "agy", pane_pid
    if lowered_cmd in ("claude", "claude-code"):
        return "claude", pane_pid
    if lowered_cmd in ("codex", "codex-cli"):
        return "codex", pane_pid
    if lowered_cmd == "aider":
        return "aider", pane_pid

    return None


def find_agy_conv_id(pid: int) -> Optional[str]:
    """Find the conversation ID for an agy process via presence lock file descriptor."""
    # Check process and its descendants
    search_pids = [pid] + get_descendants(pid)

    for p in search_pids:
        fd_dir = f"/proc/{p}/fd"
        if os.path.isdir(fd_dir):
            try:
                for fd_name in os.listdir(fd_dir):
                    fd_path = os.path.join(fd_dir, fd_name)
                    try:
                        target = os.readlink(fd_path)
                        if target.endswith(" (deleted)"):
                            target = target[:-10]
                        if "/presence/" in target and target.endswith(".lock"):
                            return os.path.basename(target)[:-5]
                    except OSError:
                        continue
            except OSError:
                pass

    # Non-Linux / lsof fallback
    try:
        res = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-F", "n"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        for line in res.stdout.splitlines():
            if line.startswith("n") and "/presence/" in line and line.endswith(".lock"):
                path = line[1:]
                return os.path.basename(path)[:-5]
    except Exception:
        pass

    return None


def resolve_fallback_title(pane_path: str, empty_title: str) -> str:
    """Determine title when agent has no active session title yet."""
    folder = os.path.basename(pane_path.rstrip("/")) if pane_path else ""
    if empty_title.lower() in ("folder", "dir", "directory"):
        return folder or "new session"
    if empty_title:
        return empty_title
    return folder or "new session"


def resolve_agy_title(pid: int, pane_path: str, empty_title: str) -> str:
    """Resolve session title for an agy (Antigravity CLI) process."""
    conv_id = find_agy_conv_id(pid)

    if conv_id:
        db_path = os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")
        if os.path.exists(db_path):
            try:
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
                    # If DB row exists but title/preview empty, check workspace_uris if user prefers folder
                    if empty_title.lower() in ("folder", "dir") and workspace_uris:
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

    return resolve_fallback_title(pane_path, empty_title)


def resolve_claude_title(
    pid: int, pane_path: str, pane_title: str, empty_title: str
) -> str:
    """Resolve session title for Claude Code."""
    # 1. Check pane_title if it has something descriptive
    if pane_title and pane_title.strip() not in GENERIC_PANE_TITLES:
        clean = re.sub(r"^Claude(\s*Code)?\s*[-:–]?\s*", "", pane_title, flags=re.I).strip()
        if clean and clean.lower() not in GENERIC_PANE_TITLES:
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
        slug = re.sub(r"[^a-zA-Z0-9]", "-", cwd)
        claude_dir = os.path.expanduser(f"~/.claude/projects/{slug}")
        if os.path.isdir(claude_dir):
            try:
                jsonl_files = sorted(
                    glob.glob(os.path.join(claude_dir, "*.jsonl")),
                    key=os.path.getmtime,
                    reverse=True,
                )
                if jsonl_files:
                    latest_file = jsonl_files[0]
                    with open(latest_file, "r", encoding="utf-8", errors="ignore") as f:
                        for _ in range(25):
                            line = f.readline()
                            if not line:
                                break
                            try:
                                data = json.loads(line)
                                if "custom_title" in data and data["custom_title"]:
                                    return str(data["custom_title"]).strip()
                                if "title" in data and data["title"]:
                                    return str(data["title"]).strip()
                                if data.get("type") == "user" or data.get("role") == "user":
                                    msg = data.get("message") or data.get("content") or ""
                                    text = extract_text_from_message(msg)
                                    if text:
                                        return text
                            except Exception:
                                continue
            except Exception:
                pass

    return resolve_fallback_title(cwd or pane_path, empty_title)


def resolve_aider_title(
    pid: int, pane_path: str, pane_title: str, empty_title: str
) -> str:
    """Resolve session title for Aider CLI."""
    if pane_title and pane_title.strip() not in GENERIC_PANE_TITLES:
        clean = re.sub(r"^aider\s*[-:–]?\s*", "", pane_title, flags=re.I).strip()
        if clean and clean.lower() not in GENERIC_PANE_TITLES:
            return clean

    cwd = pane_path
    try:
        proc_cwd = os.readlink(f"/proc/{pid}/cwd")
        if os.path.isdir(proc_cwd):
            cwd = proc_cwd
    except OSError:
        pass

    # Check .aider.chat.history.md
    if cwd:
        aider_history = os.path.join(cwd, ".aider.chat.history.md")
        if os.path.isfile(aider_history):
            try:
                with open(aider_history, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line_str = line.strip()
                        if line_str.startswith("#### ") and not line_str.startswith("#### /"):
                            prompt = line_str[5:].strip()
                            if prompt:
                                return prompt
            except Exception:
                pass

    return resolve_fallback_title(cwd or pane_path, empty_title)


def resolve_codex_title(
    pid: int, pane_path: str, pane_title: str, empty_title: str
) -> str:
    """Resolve session title for Codex CLI."""
    if pane_title and pane_title.strip() not in GENERIC_PANE_TITLES:
        clean = re.sub(r"^codex\s*[-:–]?\s*", "", pane_title, flags=re.I).strip()
        if clean and clean.lower() not in GENERIC_PANE_TITLES:
            return clean

    cwd = pane_path
    try:
        proc_cwd = os.readlink(f"/proc/{pid}/cwd")
        if os.path.isdir(proc_cwd):
            cwd = proc_cwd
    except OSError:
        pass

    return resolve_fallback_title(cwd or pane_path, empty_title)


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
        raw_title = resolve_fallback_title(pane_path, empty_title)

    title = sanitize_title(raw_title, max_len=max_len)
    icon = AGENT_ICONS.get(agent_name, AGENT_ICONS["default"]) if use_icons else ""

    result = format_template
    result = result.replace("#{agent}", agent_name)
    result = result.replace("#{title}", title)
    result = result.replace("#{icon}", icon)

    # Clean up formatting artifact if icon is empty
    result = re.sub(r"^\s+", "", result)

    return result


def parse_args():
    """Fast argument parsing supporting positional args from format_tab.sh or standard flags."""
    args_list = sys.argv[1:]
    # Check if invoked with standard positional arguments (from format_tab.sh)
    if args_list and not args_list[0].startswith("--"):
        pane_pid = args_list[0] if len(args_list) > 0 else ""
        pane_cmd = args_list[1] if len(args_list) > 1 else ""
        pane_path = args_list[2] if len(args_list) > 2 else ""
        pane_title = args_list[3] if len(args_list) > 3 else ""
        format_template = args_list[4] if len(args_list) > 4 else "#{agent}[#{title}]"
        try:
            max_len = int(args_list[5]) if len(args_list) > 5 else 25
        except ValueError:
            max_len = 25
        empty_title = args_list[6] if len(args_list) > 6 else "new session"
        fallback = args_list[7] if len(args_list) > 7 else "#{pane_current_command}"
        use_icons = (
            len(args_list) > 8
            and args_list[8] in ("--use-icons", "true", "1", "on")
        )
        return (
            pane_pid,
            pane_cmd,
            pane_path,
            pane_title,
            format_template,
            max_len,
            empty_title,
            fallback,
            use_icons,
        )

    # Fallback to flag parsing
    import argparse
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
    parsed = parser.parse_args(args_list)
    return (
        parsed.pane_pid,
        parsed.pane_cmd,
        parsed.pane_path,
        parsed.pane_title,
        parsed.format,
        parsed.max_len,
        parsed.empty_title,
        parsed.fallback,
        parsed.use_icons,
    )


def main():
    try:
        (
            pane_pid,
            pane_cmd,
            pane_path,
            pane_title,
            format_template,
            max_len,
            empty_title,
            fallback,
            use_icons,
        ) = parse_args()

        output = resolve(
            pane_pid_str=pane_pid,
            pane_cmd=pane_cmd,
            pane_path=pane_path,
            pane_title=pane_title,
            format_template=format_template,
            max_len=max_len,
            empty_title=empty_title,
            fallback=fallback,
            use_icons=use_icons,
        )
        print(output, end="")
    except Exception:
        # In any unhandled failure, output pane_cmd safely so tmux tab never breaks
        fallback_cmd = sys.argv[2] if len(sys.argv) > 2 else "tmux"
        print(fallback_cmd, end="")


if __name__ == "__main__":
    main()
