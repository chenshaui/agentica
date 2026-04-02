# -*- coding: utf-8 -*-
"""Tests for SessionLog — append-only JSONL session persistence with compact boundaries."""
import json
import os
import sys
import tempfile

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from agentica.memory.session_log import SessionLog


@pytest.fixture
def tmp_dir():
    """Create a temp directory for session logs, cleaned up after test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestSessionLogBasic:
    """Core append + load tests."""

    def test_append_and_load_messages(self, tmp_dir):
        log = SessionLog("s1", base_dir=tmp_dir)
        log.append_message("user", "hello")
        log.append_message("assistant", "hi there")
        log.append_message("user", "how are you?")

        messages = log.load()
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "hi there"
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == "how are you?"

    def test_load_empty_log(self, tmp_dir):
        log = SessionLog("empty", base_dir=tmp_dir)
        assert log.load() == []
        assert log.exists() is False

    def test_load_nonexistent_session(self, tmp_dir):
        log = SessionLog("nonexistent", base_dir=tmp_dir)
        assert log.load() == []

    def test_entry_count(self, tmp_dir):
        log = SessionLog("s2", base_dir=tmp_dir)
        assert log.entry_count() == 0
        log.append_message("user", "a")
        log.append_message("assistant", "b")
        assert log.entry_count() == 2

    def test_exists(self, tmp_dir):
        log = SessionLog("s3", base_dir=tmp_dir)
        assert log.exists() is False
        log.append_message("user", "test")
        assert log.exists() is True

    def test_append_tool_result(self, tmp_dir):
        log = SessionLog("s4", base_dir=tmp_dir)
        log.append_message("user", "run ls")
        log.append_tool_result("execute", "call-1", "file1.py\nfile2.py", is_error=False)
        log.append_message("assistant", "found 2 files")

        messages = log.load()
        assert len(messages) == 3
        assert messages[1]["role"] == "tool"
        assert "file1.py" in messages[1]["content"]


class TestCompactBoundary:
    """Compact boundary = resume checkpoint."""

    def test_resume_from_compact_boundary(self, tmp_dir):
        """Messages before boundary should be replaced by summary."""
        log = SessionLog("compact1", base_dir=tmp_dir)

        # Conversation history
        log.append_message("user", "old message 1")
        log.append_message("assistant", "old response 1")
        log.append_message("user", "old message 2")
        log.append_message("assistant", "old response 2")

        # Compact boundary (auto_compact summarised everything above)
        log.append_compact_boundary("User asked 2 questions, assistant answered both.")

        # New messages after compact
        log.append_message("user", "new question")
        log.append_message("assistant", "new answer")

        messages = log.load()

        # Should NOT contain old messages
        assert not any("old message" in m["content"] for m in messages)

        # Should contain: resumed summary + new messages
        assert len(messages) == 4  # summary(user) + ack(assistant) + user + assistant
        assert "[Resumed session" in messages[0]["content"]
        assert "User asked 2 questions" in messages[0]["content"]
        assert messages[2]["content"] == "new question"
        assert messages[3]["content"] == "new answer"

    def test_multiple_compact_boundaries(self, tmp_dir):
        """Only the LAST boundary should be used for resume."""
        log = SessionLog("compact2", base_dir=tmp_dir)

        log.append_message("user", "round 1")
        log.append_compact_boundary("Summary of round 1")

        log.append_message("user", "round 2")
        log.append_compact_boundary("Summary of rounds 1+2")

        log.append_message("user", "round 3")

        messages = log.load()

        # Should resume from last boundary only
        assert not any("round 1" in m["content"] and m["role"] == "user" for m in messages
                       if "Resumed" not in m["content"])
        assert any("Summary of rounds 1+2" in m["content"] for m in messages)
        assert messages[-1]["content"] == "round 3"

    def test_no_boundary_replays_all(self, tmp_dir):
        """Without any boundary, all messages are replayed."""
        log = SessionLog("no-boundary", base_dir=tmp_dir)
        log.append_message("user", "msg1")
        log.append_message("assistant", "msg2")
        log.append_message("user", "msg3")

        messages = log.load()
        assert len(messages) == 3
        assert messages[0]["content"] == "msg1"

    def test_boundary_at_end(self, tmp_dir):
        """Boundary at the very end with no new messages after it."""
        log = SessionLog("boundary-end", base_dir=tmp_dir)
        log.append_message("user", "question")
        log.append_message("assistant", "answer")
        log.append_compact_boundary("Conversation about a question")

        messages = log.load()
        # Only the resumed summary + ack
        assert len(messages) == 2
        assert "[Resumed session" in messages[0]["content"]


class TestJSONLFormat:
    """Verify the file is valid JSONL."""

    def test_file_is_valid_jsonl(self, tmp_dir):
        log = SessionLog("jsonl-check", base_dir=tmp_dir)
        log.append_message("user", "hello")
        log.append_tool_result("grep", "c1", "results")
        log.append_compact_boundary("summary text")
        log.append_message("assistant", "response")

        with open(log.path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 4
        for line in lines:
            entry = json.loads(line)  # should not raise
            assert "type" in entry
            assert "ts" in entry

    def test_unicode_content(self, tmp_dir):
        log = SessionLog("unicode", base_dir=tmp_dir)
        log.append_message("user", "你好世界 🌍")
        log.append_message("assistant", "こんにちは")

        messages = log.load()
        assert messages[0]["content"] == "你好世界 🌍"
        assert messages[1]["content"] == "こんにちは"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
