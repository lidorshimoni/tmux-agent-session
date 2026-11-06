#!/usr/bin/env python3
"""
Unit and integration tests for agent_session_resolver.py
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add scripts directory to path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import agent_session_resolver as resolver


class TestSanitizeTitle(unittest.TestCase):
    def test_empty_and_none(self):
        self.assertEqual(resolver.sanitize_title(""), "")
        self.assertEqual(resolver.sanitize_title(None), "")

    def test_strip_ansi_escapes(self):
        colored = "\x1b[31mRed Alert\x1b[0m"
        self.assertEqual(resolver.sanitize_title(colored), "Red Alert")

    def test_control_chars_and_newlines(self):
        raw = "Line 1\nLine 2\r\nLine 3\tTabbed"
        self.assertEqual(resolver.sanitize_title(raw, max_len=0), "Line 1 Line 2 Line 3 Tabbed")

    def test_collapse_multiple_spaces(self):
        raw = "Too    many     spaces"
        self.assertEqual(resolver.sanitize_title(raw), "Too many spaces")

    def test_truncation_without_word_break(self):
        long_str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"
        sanitized = resolver.sanitize_title(long_str, max_len=15)
        self.assertTrue(len(sanitized) <= 15)
        self.assertTrue(sanitized.endswith("..."))

    def test_truncation_with_word_boundary(self):
        long_str = "Cell Reception App Planning and Evaluation"
        sanitized = resolver.sanitize_title(long_str, max_len=25)
        self.assertTrue(len(sanitized) <= 25)
        self.assertTrue(sanitized.endswith("..."))
        self.assertEqual(sanitized, "Cell Reception App...")

    def test_unicode_and_emojis(self):
        title = "🚀 Feature: שלום עולם - AI agent"
        sanitized = resolver.sanitize_title(title, max_len=0)
        self.assertEqual(sanitized, "🚀 Feature: שלום עולם - AI agent")

    def test_single_long_word_truncation(self):
        title = "Supercalifragilisticexpialidocious"
        sanitized = resolver.sanitize_title(title, max_len=10)
        self.assertEqual(len(sanitized), 10)
        self.assertTrue(sanitized.endswith("..."))

    def test_quotes_and_brackets(self):
        title = "Fix: 'issue [42]' in \"main.py\""
        sanitized = resolver.sanitize_title(title, max_len=0)
        self.assertEqual(sanitized, "Fix: 'issue [42]' in \"main.py\"")


class TestAgyResolution(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "conversation_summaries.db")

        # Create mock sqlite db
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE conversation_summaries (
                conversation_id TEXT PRIMARY KEY,
                title TEXT,
                preview TEXT,
                workspace_uris TEXT
            )
        """)
        c.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
            ("conv-1", "Feature Auth Complete", "Auth preview", '["file:///projects/my-app"]'),
        )
        c.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
            ("conv-empty-title", "", "Preview Only Fallback", '["file:///projects/my-app"]'),
        )
        c.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
            ("conv-uris-only", "", "", '["file:///projects/dashboard-service"]'),
        )
        c.execute(
            "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?)",
            ("conv-all-empty", "", "", '[]'),
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("agent_session_resolver.find_agy_conv_id")
    def test_agy_title_hierarchy(self, mock_find_conv):
        orig_expanduser = os.path.expanduser
        try:
            os.path.expanduser = lambda path: self.db_path if "conversation_summaries.db" in path else orig_expanduser(path)

            # Conv 1: Full title
            mock_find_conv.return_value = "conv-1"
            title = resolver.resolve_agy_title(1234, "/projects/my-app", "new session")
            self.assertEqual(title, "Feature Auth Complete")

            # Conv 2: Preview only
            mock_find_conv.return_value = "conv-empty-title"
            title = resolver.resolve_agy_title(1234, "/projects/my-app", "new session")
            self.assertEqual(title, "Preview Only Fallback")

            # Conv 3: Empty title/preview, default empty_title="new session"
            mock_find_conv.return_value = "conv-uris-only"
            title = resolver.resolve_agy_title(1234, "/projects/my-app", "new session")
            self.assertEqual(title, "new session")

            # Conv 3: User prefers folder fallback
            title_folder = resolver.resolve_agy_title(1234, "/projects/my-app", "folder")
            self.assertEqual(title_folder, "dashboard-service")

            # Conv not found
            mock_find_conv.return_value = None
            title_none = resolver.resolve_agy_title(1234, "/projects/my-app", "new session")
            self.assertEqual(title_none, "new session")
        finally:
            os.path.expanduser = orig_expanduser

    def test_find_agy_conv_id_with_deleted_lock(self):
        # Test real readlink simulation with (deleted)
        with tempfile.TemporaryDirectory() as proc_mock:
            fd_dir = os.path.join(proc_mock, "12345", "fd")
            os.makedirs(fd_dir)
            lock_target = "/presence/test-uuid-9876.lock (deleted)"

            with patch("os.path.isdir", side_effect=lambda p: True if p == f"/proc/12345/fd" else os.path.isdir(p)):
                with patch("os.listdir", side_effect=lambda p: ["10"] if p == f"/proc/12345/fd" else os.listdir(p)):
                    with patch("os.readlink", side_effect=lambda p: lock_target if p == f"/proc/12345/fd/10" else os.readlink(p)):
                        conv_id = resolver.find_agy_conv_id(12345)
                        self.assertEqual(conv_id, "test-uuid-9876")


class TestClaudeResolution(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_claude_pane_title(self):
        res = resolver.resolve_claude_title(
            pid=99999,
            pane_path="/some/dir",
            pane_title="Claude - Refactor Payment Gateway",
            empty_title="new session",
        )
        self.assertEqual(res, "Refactor Payment Gateway")

    def test_claude_code_pane_title_variant(self):
        res = resolver.resolve_claude_title(
            pid=99999,
            pane_path="/some/dir",
            pane_title="Claude Code: Implement Dark Mode",
            empty_title="new session",
        )
        self.assertEqual(res, "Implement Dark Mode")

    def test_claude_project_jsonl_plain_string(self):
        project_path = "/home/test/my-project"
        slug = "-home-test-my-project"
        claude_proj_dir = os.path.join(self.test_dir, ".claude", "projects", slug)
        os.makedirs(claude_proj_dir, exist_ok=True)

        jsonl_file = os.path.join(claude_proj_dir, "session-1.jsonl")
        with open(jsonl_file, "w") as f:
            f.write(json.dumps({"type": "user", "message": "Fix login crash bug"}) + "\n")

        orig_expanduser = os.path.expanduser
        try:
            os.path.expanduser = lambda path: path.replace("~", self.test_dir)
            res = resolver.resolve_claude_title(
                pid=99999,
                pane_path=project_path,
                pane_title="",
                empty_title="new session",
            )
            self.assertEqual(res, "Fix login crash bug")
        finally:
            os.path.expanduser = orig_expanduser

    def test_claude_project_jsonl_block_structure(self):
        # Claude Code often wraps message content in blocks: [{"type": "text", "text": "..."}]
        project_path = "/home/test/blocks-project"
        slug = "-home-test-blocks-project"
        claude_proj_dir = os.path.join(self.test_dir, ".claude", "projects", slug)
        os.makedirs(claude_proj_dir, exist_ok=True)

        jsonl_file = os.path.join(claude_proj_dir, "session-2.jsonl")
        with open(jsonl_file, "w") as f:
            f.write(
                json.dumps({
                    "role": "user",
                    "content": [{"type": "text", "text": "Deploy to staging cluster"}]
                }) + "\n"
            )

        orig_expanduser = os.path.expanduser
        try:
            os.path.expanduser = lambda path: path.replace("~", self.test_dir)
            res = resolver.resolve_claude_title(
                pid=99999,
                pane_path=project_path,
                pane_title="",
                empty_title="new session",
            )
            self.assertEqual(res, "Deploy to staging cluster")
        finally:
            os.path.expanduser = orig_expanduser

    def test_claude_fallback_to_empty_title_and_folder(self):
        # Fallback to empty title
        res1 = resolver.resolve_claude_title(
            pid=99999,
            pane_path="/home/test/backend-api",
            pane_title="",
            empty_title="new session",
        )
        self.assertEqual(res1, "new session")

        # Fallback to folder
        res2 = resolver.resolve_claude_title(
            pid=99999,
            pane_path="/home/test/backend-api",
            pane_title="",
            empty_title="folder",
        )
        self.assertEqual(res2, "backend-api")


class TestCodexAndAiderResolution(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_codex_title(self):
        res = resolver.resolve_codex_title(
            pid=99999,
            pane_path="/home/user/ai-engine",
            pane_title="codex: building neural net",
            empty_title="new session",
        )
        self.assertEqual(res, "building neural net")

    def test_aider_title_from_history(self):
        # Create .aider.chat.history.md
        aider_history = os.path.join(self.test_dir, ".aider.chat.history.md")
        with open(aider_history, "w") as f:
            f.write("# aider chat started\n\n#### /add test.py\n\n#### Add JWT authentication\n")

        res = resolver.resolve_aider_title(
            pid=99999,
            pane_path=self.test_dir,
            pane_title="",
            empty_title="new session",
        )
        self.assertEqual(res, "Add JWT authentication")


class TestAgentProcessDetection(unittest.TestCase):
    @patch("agent_session_resolver.get_descendants", return_value=[])
    def test_false_positive_node_script(self, _):
        # node running test-claude.js should NOT be detected as claude
        with patch("agent_session_resolver.get_process_info", return_value=("node", "node test-claude.js")):
            res = resolver.find_agent_process(12345, "node")
            self.assertIsNone(res)

    @patch("agent_session_resolver.get_descendants", return_value=[])
    def test_false_positive_python_script(self, _):
        # python running test_aider.py should NOT be detected as aider
        with patch("agent_session_resolver.get_process_info", return_value=("python3", "python3 test_aider.py")):
            res = resolver.find_agent_process(12345, "python3")
            self.assertIsNone(res)

    @patch("agent_session_resolver.get_descendants", return_value=[])
    def test_true_positive_claude_binary(self, _):
        with patch("agent_session_resolver.get_process_info", return_value=("node", "node /usr/local/bin/claude")):
            res = resolver.find_agent_process(12345, "claude")
            self.assertEqual(res, ("claude", 12345))

    @patch("agent_session_resolver.get_descendants", return_value=[])
    def test_true_positive_aider_module(self, _):
        with patch("agent_session_resolver.get_process_info", return_value=("python3", "python3 -m aider")):
            res = resolver.find_agent_process(12345, "python3")
            self.assertEqual(res, ("aider", 12345))

    @patch("agent_session_resolver.get_descendants", return_value=[])
    def test_true_positive_agy(self, _):
        with patch("agent_session_resolver.get_process_info", return_value=("agy", "agy --dangerously-skip-permissions")):
            res = resolver.find_agent_process(12345, "agy")
            self.assertEqual(res, ("agy", 12345))


class TestFullResolveFlow(unittest.TestCase):
    def test_non_agent_command(self):
        out = resolver.resolve(
            pane_pid_str="12345",
            pane_cmd="vim",
            pane_path="/home/user",
            pane_title="",
            format_template="#{agent}[#{title}]",
            max_len=25,
            empty_title="new session",
            fallback="#{pane_current_command}",
            use_icons=False,
        )
        self.assertEqual(out, "vim")

    def test_invalid_pid(self):
        out = resolver.resolve(
            pane_pid_str="not_a_pid",
            pane_cmd="zsh",
            pane_path="/home/user",
            pane_title="",
            format_template="#{agent}[#{title}]",
            max_len=25,
            empty_title="new session",
            fallback="#{pane_current_command}",
            use_icons=False,
        )
        self.assertEqual(out, "zsh")

    def test_root_pid_one(self):
        out = resolver.resolve(
            pane_pid_str="1",
            pane_cmd="init",
            pane_path="/",
            pane_title="",
            format_template="#{agent}[#{title}]",
            max_len=25,
            empty_title="new session",
            fallback="#{pane_current_command}",
            use_icons=False,
        )
        self.assertEqual(out, "init")

    def test_agent_command_fallback(self):
        out = resolver.resolve(
            pane_pid_str="999999",
            pane_cmd="codex",
            pane_path="/home/user/my-repo",
            pane_title="",
            format_template="#{agent}[#{title}]",
            max_len=25,
            empty_title="folder",
            fallback="#{pane_current_command}",
            use_icons=False,
        )
        self.assertEqual(out, "codex[my-repo]")

    def test_custom_formatting_and_icons(self):
        out = resolver.resolve(
            pane_pid_str="999999",
            pane_cmd="claude",
            pane_path="/home/user/my-repo",
            pane_title="Claude: Build CI pipeline",
            format_template="#{icon} #{agent}: #{title}",
            max_len=25,
            empty_title="new session",
            fallback="#{pane_current_command}",
            use_icons=True,
        )
        self.assertEqual(out, "✻ claude: Build CI pipeline")


if __name__ == "__main__":
    unittest.main()
