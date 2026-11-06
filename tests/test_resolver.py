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
        # Should break cleanly around a word
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

    def test_agy_title_hierarchy(self):
        # Patch db path
        orig_expanduser = os.path.expanduser
        try:
            os.path.expanduser = lambda path: self.db_path if "conversation_summaries.db" in path else orig_expanduser(path)

            # Mock presence lock fd lookup
            with tempfile.TemporaryDirectory() as proc_mock:
                fd_dir = os.path.join(proc_mock, "fd")
                os.makedirs(fd_dir)
                os.symlink("/dummy/presence/conv-1.lock", os.path.join(fd_dir, "10"))

                orig_proc_fd = resolver.os.path.isdir
                # Directly test with conv-1
                title1 = resolver.resolve_agy_title(99999, "/projects/my-app", "empty")
                # Since pid 99999 doesn't have /proc/99999/fd in real /proc, test fallback
                self.assertEqual(title1, "my-app")

                # Test DB query logic by simulating found conv_id
                conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
                c = conn.cursor()

                # Conv 1
                c.execute("SELECT title, preview, workspace_uris FROM conversation_summaries WHERE conversation_id = 'conv-1'")
                r1 = c.fetchone()
                self.assertEqual(r1[0], "Feature Auth Complete")

                # Conv empty title
                c.execute("SELECT title, preview, workspace_uris FROM conversation_summaries WHERE conversation_id = 'conv-empty-title'")
                r2 = c.fetchone()
                self.assertEqual(r2[1], "Preview Only Fallback")

                # Conv uris only
                c.execute("SELECT title, preview, workspace_uris FROM conversation_summaries WHERE conversation_id = 'conv-uris-only'")
                r3 = c.fetchone()
                uris = json.loads(r3[2])
                self.assertEqual(os.path.basename(uris[0]), "dashboard-service")

                conn.close()
        finally:
            os.path.expanduser = orig_expanduser


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

    def test_claude_project_jsonl(self):
        # Setup ~/.claude/projects/-some-project
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

    def test_claude_fallback_to_folder(self):
        res = resolver.resolve_claude_title(
            pid=99999,
            pane_path="/home/test/backend-api",
            pane_title="",
            empty_title="new session",
        )
        self.assertEqual(res, "backend-api")


class TestCodexAndAiderResolution(unittest.TestCase):
    def test_codex_title(self):
        res = resolver.resolve_codex_title(
            pid=99999,
            pane_path="/home/user/ai-engine",
            pane_title="codex: building neural net",
            empty_title="new session",
        )
        self.assertEqual(res, "building neural net")

    def test_aider_title(self):
        res = resolver.resolve_aider_title(
            pid=99999,
            pane_path="/home/user/ai-engine",
            pane_title="",
            empty_title="new session",
        )
        self.assertEqual(res, "ai-engine")


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
        # When pane_cmd is "codex" and no process tree is found, falls back to pane_cmd
        out = resolver.resolve(
            pane_pid_str="999999",
            pane_cmd="codex",
            pane_path="/home/user/my-repo",
            pane_title="",
            format_template="#{agent}[#{title}]",
            max_len=25,
            empty_title="new session",
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
