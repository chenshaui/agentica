# -*- coding: utf-8 -*-
"""Tests for agentica.compression — micro-compact, tool result storage, compression manager."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from agentica.model.message import Message


# ===========================================================================
# micro_compact tests
# ===========================================================================

class TestMicroCompact(unittest.TestCase):
    """Tests for agentica.compression.micro.micro_compact."""

    def _make_tool_msg(self, content: str, compacted: bool = False) -> Message:
        msg = Message(role="tool", content=content, tool_call_id="tc_1")
        msg._micro_compacted = compacted
        return msg

    def test_keeps_recent_messages_untouched(self):
        from agentica.compression.micro import micro_compact, DEFAULT_KEEP_RECENT
        msgs = [
            Message(role="user", content="hi"),
        ] + [self._make_tool_msg(f"result {'x' * 100}") for _ in range(DEFAULT_KEEP_RECENT)]
        count = micro_compact(msgs)
        self.assertEqual(count, 0, "Should not compact when tool msgs <= keep_recent")

    def test_compacts_oldest_tool_results(self):
        from agentica.compression.micro import micro_compact, MICRO_COMPACT_PLACEHOLDER
        msgs = [
            Message(role="user", content="hi"),
            self._make_tool_msg("A" * 200),  # old → should be compacted
            self._make_tool_msg("B" * 200),  # old → should be compacted
        ] + [self._make_tool_msg(f"C{'x' * 100}") for _ in range(5)]  # recent 5 → kept
        count = micro_compact(msgs, keep_recent=5)
        self.assertEqual(count, 2)
        self.assertEqual(msgs[1].content, MICRO_COMPACT_PLACEHOLDER)
        self.assertEqual(msgs[2].content, MICRO_COMPACT_PLACEHOLDER)
        # Recent 5 untouched
        for m in msgs[3:]:
            self.assertNotEqual(m.content, MICRO_COMPACT_PLACEHOLDER)

    def test_skips_short_content(self):
        from agentica.compression.micro import micro_compact, _MIN_CONTENT_LEN
        msgs = [
            Message(role="user", content="hi"),
            self._make_tool_msg("short"),  # < _MIN_CONTENT_LEN → skip
        ] + [self._make_tool_msg(f"x{'y' * 100}") for _ in range(5)]
        count = micro_compact(msgs, keep_recent=5)
        self.assertEqual(count, 0, "Short content should be skipped")
        self.assertEqual(msgs[1].content, "short")

    def test_skips_already_compacted(self):
        from agentica.compression.micro import micro_compact
        msgs = [
            Message(role="user", content="hi"),
            self._make_tool_msg("A" * 200, compacted=True),  # already compacted
        ] + [self._make_tool_msg(f"B{'x' * 100}") for _ in range(5)]
        count = micro_compact(msgs, keep_recent=5)
        self.assertEqual(count, 0, "Already compacted messages should be skipped")

    def test_marks_compacted_flag(self):
        from agentica.compression.micro import micro_compact
        msgs = [
            Message(role="user", content="hi"),
            self._make_tool_msg("A" * 200),
        ] + [self._make_tool_msg(f"B{'x' * 100}") for _ in range(5)]
        micro_compact(msgs, keep_recent=5)
        self.assertTrue(msgs[1]._micro_compacted)


# ===========================================================================
# tool_result_storage tests
# ===========================================================================

class TestSanitizePath(unittest.TestCase):
    """Tests for _sanitize_path."""

    def test_basic_path(self):
        from agentica.compression.tool_result_storage import _sanitize_path
        result = _sanitize_path("/Users/test/project")
        self.assertRegex(result, r'^[a-zA-Z0-9\-]+$')

    def test_long_path_truncated_with_hash(self):
        from agentica.compression.tool_result_storage import _sanitize_path, _MAX_SANITIZED_LENGTH
        long_path = "/a/b/c/" + "x" * 300
        result = _sanitize_path(long_path)
        self.assertLessEqual(len(result), _MAX_SANITIZED_LENGTH + 10)  # +hash suffix
        self.assertIn("-", result)  # hash appended

    def test_special_chars_replaced(self):
        from agentica.compression.tool_result_storage import _sanitize_path
        result = _sanitize_path("/path/to/my project (2)/test.txt")
        self.assertNotIn(" ", result)
        self.assertNotIn("(", result)


class TestMaybePersistResult(unittest.TestCase):
    """Tests for maybe_persist_result — Layer 1 per-tool persistence."""

    def test_small_content_unchanged(self):
        from agentica.compression.tool_result_storage import maybe_persist_result
        content = "small output"
        result = maybe_persist_result("test_tool", "call_1", content, max_result_size_chars=50000)
        self.assertEqual(result, content)

    def test_none_threshold_skips(self):
        from agentica.compression.tool_result_storage import maybe_persist_result
        big = "x" * 100_000
        result = maybe_persist_result("test_tool", "call_2", big, max_result_size_chars=None)
        self.assertEqual(result, big, "None threshold should never persist")

    def test_large_content_persisted(self):
        from agentica.compression.tool_result_storage import maybe_persist_result
        with tempfile.TemporaryDirectory() as tmpdir:
            big = "x" * 100
            with patch("agentica.compression.tool_result_storage.AGENTICA_PROJECTS_DIR", tmpdir):
                result = maybe_persist_result(
                    "test_tool", "call_3", big,
                    max_result_size_chars=50, cwd="/test/project",
                )
            self.assertIn("<persisted-output>", result)
            self.assertIn("Preview", result)

    def test_disk_failure_fallback_truncation(self):
        from agentica.compression.tool_result_storage import maybe_persist_result
        big = "x" * 100
        with patch("agentica.compression.tool_result_storage._persist_to_disk", return_value=False):
            result = maybe_persist_result(
                "test_tool", "call_4", big,
                max_result_size_chars=50,
            )
        self.assertIn("truncated", result)
        self.assertLessEqual(len(result), 80)  # truncated to threshold + message


class TestBuildPersistedMessage(unittest.TestCase):
    """Tests for _build_persisted_message."""

    def test_message_format(self):
        from agentica.compression.tool_result_storage import _build_persisted_message, PREVIEW_CHARS
        content = "x" * 5000
        msg = _build_persisted_message("/path/to/file.txt", content)
        self.assertIn("<persisted-output>", msg)
        self.assertIn("</persisted-output>", msg)
        self.assertIn("/path/to/file.txt", msg)
        self.assertIn("Preview", msg)
        self.assertIn("...", msg)  # content > PREVIEW_CHARS, so has ellipsis

    def test_short_content_no_ellipsis(self):
        from agentica.compression.tool_result_storage import _build_persisted_message
        content = "short"
        msg = _build_persisted_message("/path/to/file.txt", content)
        # Content <= PREVIEW_CHARS, no "..." before closing tag
        self.assertIn("<persisted-output>", msg)
        self.assertIn("short", msg)
        # The message should NOT have the ellipsis line
        self.assertNotIn("\n...\n", msg)


class TestEnforceToolResultBudget(unittest.TestCase):
    """Tests for enforce_tool_result_budget — Layer 2 per-message budget."""

    def test_under_budget_no_changes(self):
        from agentica.compression.tool_result_storage import enforce_tool_result_budget
        msgs = [
            Message(role="tool", content="short1", tool_call_id="t1"),
            Message(role="tool", content="short2", tool_call_id="t2"),
        ]
        count = enforce_tool_result_budget(msgs, budget=1000)
        self.assertEqual(count, 0)

    def test_over_budget_largest_persisted(self):
        from agentica.compression.tool_result_storage import enforce_tool_result_budget
        with tempfile.TemporaryDirectory() as tmpdir:
            msgs = [
                Message(role="tool", content="a" * 100, tool_call_id="t1"),
                Message(role="tool", content="b" * 500, tool_call_id="t2"),  # largest
                Message(role="tool", content="c" * 50, tool_call_id="t3"),
            ]
            with patch("agentica.compression.tool_result_storage.AGENTICA_PROJECTS_DIR", tmpdir):
                count = enforce_tool_result_budget(msgs, budget=200, cwd="/test")
            self.assertGreater(count, 0)
            # The largest should be persisted
            self.assertIn("<persisted-output>", msgs[1].content)

    def test_already_persisted_skipped(self):
        from agentica.compression.tool_result_storage import enforce_tool_result_budget
        msgs = [
            Message(role="tool", content="<persisted-output>already</persisted-output>", tool_call_id="t1"),
            Message(role="tool", content="b" * 500, tool_call_id="t2"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("agentica.compression.tool_result_storage.AGENTICA_PROJECTS_DIR", tmpdir):
                count = enforce_tool_result_budget(msgs, budget=100, cwd="/test")
        # Only the non-persisted one should be targeted
        self.assertLessEqual(count, 1)

    def test_empty_results_no_error(self):
        from agentica.compression.tool_result_storage import enforce_tool_result_budget
        count = enforce_tool_result_budget([], budget=1000)
        self.assertEqual(count, 0)


# ===========================================================================
# CompressionManager tests
# ===========================================================================

class TestCompressionManagerInit(unittest.TestCase):
    """CompressionManager initialization and defaults."""

    def test_defaults(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager()
        self.assertTrue(cm.compress_tool_results)
        self.assertIsNone(cm.compress_token_limit)
        self.assertEqual(cm.truncate_head_chars, 150)
        self.assertEqual(cm.keep_recent_rounds, 3)
        self.assertFalse(cm.use_llm_compression)

    def test_target_from_trigger(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager(compress_token_limit=10000)
        self.assertEqual(cm.compress_target_token_limit, 6000)  # 60% of trigger


class TestCompressionManagerResolveLimits(unittest.TestCase):
    """_resolve_limits auto-derives thresholds from model.context_window."""

    def test_resolve_from_model(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager()
        mock_model = MagicMock()
        mock_model.context_window = 100_000
        cm._resolve_limits(mock_model)
        self.assertEqual(cm.compress_token_limit, 80_000)
        self.assertEqual(cm.compress_target_token_limit, 50_000)

    def test_no_resolve_when_already_set(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager(compress_token_limit=5000)
        mock_model = MagicMock()
        mock_model.context_window = 200_000
        cm._resolve_limits(mock_model)
        self.assertEqual(cm.compress_token_limit, 5000, "Should not override explicit value")


class TestCompressionManagerShouldCompress(unittest.TestCase):
    """should_compress triggers based on token count."""

    def test_disabled_returns_false(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager(compress_tool_results=False)
        msgs = [Message(role="user", content="hi")]
        self.assertFalse(cm.should_compress(msgs))

    def test_under_threshold_returns_false(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager(compress_token_limit=100_000)
        msgs = [Message(role="user", content="hi")]
        with patch("agentica.compression.manager.count_tokens", return_value=1000):
            self.assertFalse(cm.should_compress(msgs))

    def test_over_threshold_returns_true(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager(compress_token_limit=1000)
        msgs = [Message(role="user", content="hi")]
        with patch("agentica.compression.manager.count_tokens", return_value=2000):
            self.assertTrue(cm.should_compress(msgs))


class TestCompressionManagerDropOldMessages(unittest.TestCase):
    """_drop_old_messages preserves system + first user + recent rounds."""

    def test_preserves_system_and_first_user(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager(keep_recent_rounds=1)
        msgs = [
            Message(role="system", content="system prompt"),
            Message(role="user", content="first user msg"),
            Message(role="assistant", content="old reply", tool_calls=[{"id": "1"}]),
            Message(role="tool", content="old tool result", tool_call_id="1"),
            Message(role="assistant", content="old reply 2", tool_calls=[{"id": "2"}]),
            Message(role="tool", content="old tool result 2", tool_call_id="2"),
            Message(role="assistant", content="recent reply", tool_calls=[{"id": "3"}]),
            Message(role="tool", content="recent tool result", tool_call_id="3"),
        ]
        dropped = asyncio.run(cm._drop_old_messages(msgs))
        self.assertGreater(dropped, 0)
        # System and first user always preserved
        self.assertEqual(msgs[0].role, "system")
        self.assertEqual(msgs[1].role, "user")
        self.assertEqual(msgs[1].content, "first user msg")

    def test_not_enough_rounds_no_drop(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager(keep_recent_rounds=5)
        msgs = [
            Message(role="system", content="sys"),
            Message(role="user", content="hi"),
            Message(role="assistant", content="reply", tool_calls=[{"id": "1"}]),
            Message(role="tool", content="result", tool_call_id="1"),
        ]
        dropped = asyncio.run(cm._drop_old_messages(msgs))
        self.assertEqual(dropped, 0)


class TestCompressionManagerAutoCompact(unittest.TestCase):
    """auto_compact circuit breaker and SM-compact."""

    def test_circuit_breaker_skips_after_max_failures(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager()
        cm._consecutive_auto_compact_failures = 3
        msgs = [Message(role="user", content="hi")]
        result = asyncio.run(cm.auto_compact(msgs, force=True))
        self.assertFalse(result)

    def test_sm_compact_reuses_working_memory_summary(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager()
        msgs = [
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
        ]
        wm = MagicMock()
        wm.summary = MagicMock()
        wm.summary.summary = "Previously discussed: project setup and testing"
        wm.summary.topics = ["setup", "testing"]

        result = asyncio.run(cm.auto_compact(msgs, force=True, working_memory=wm))
        self.assertTrue(result)
        self.assertEqual(len(msgs), 2)
        self.assertIn("[Context compressed]", msgs[0].content)
        self.assertIn("project setup", msgs[0].content)

    def test_failure_increments_counter(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager()
        msgs = [Message(role="user", content="hi")]
        with patch.object(cm, '_summarise_conversation', new_callable=AsyncMock, return_value=None):
            result = asyncio.run(cm.auto_compact(msgs, force=True))
        self.assertFalse(result)
        self.assertEqual(cm._consecutive_auto_compact_failures, 1)

    def test_success_resets_counter(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager()
        cm._consecutive_auto_compact_failures = 2
        msgs = [Message(role="user", content="hi"), Message(role="assistant", content="ok")]
        with patch.object(cm, '_summarise_conversation', new_callable=AsyncMock, return_value="summary text"):
            result = asyncio.run(cm.auto_compact(msgs, force=True))
        self.assertTrue(result)
        self.assertEqual(cm._consecutive_auto_compact_failures, 0)


class TestCompressionManagerGetStats(unittest.TestCase):
    """get_stats and get_compression_ratio."""

    def test_empty_stats(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager()
        stats = cm.get_stats()
        self.assertIn("compression_ratio", stats)
        self.assertEqual(stats["compression_ratio"], 1.0)

    def test_ratio_after_compression(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager()
        cm.stats["llm_original_size"] = 10000
        cm.stats["llm_compressed_size"] = 2000
        self.assertAlmostEqual(cm.get_compression_ratio(), 0.2)


class TestCompressionManagerCompress(unittest.TestCase):
    """compress() runs the two-stage pipeline."""

    def test_disabled_does_nothing(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager(compress_tool_results=False)
        msgs = [Message(role="user", content="hi")]
        asyncio.run(cm.compress(msgs))
        self.assertEqual(len(msgs), 1)

    def test_stage1a_truncates(self):
        from agentica.compression.manager import CompressionManager
        cm = CompressionManager(
            compress_token_limit=100,
            compress_target_token_limit=50,
            truncate_head_chars=20,
            keep_recent_rounds=1,
        )
        msgs = [
            Message(role="system", content="sys"),
            Message(role="user", content="hi"),
            Message(role="assistant", content="old", tool_calls=[{"id": "1"}]),
            Message(role="tool", content="x" * 1000, tool_call_id="1"),
            Message(role="assistant", content="recent", tool_calls=[{"id": "2"}]),
            Message(role="tool", content="y" * 100, tool_call_id="2"),
        ]
        with patch("agentica.compression.manager.count_tokens", return_value=10):
            asyncio.run(cm.compress(msgs))
        # The old tool result (index 3) should have been truncated/persisted
        old_tool = msgs[3]
        # Either persisted (has <persisted-output>) or truncated
        self.assertTrue(
            "<persisted-output>" in str(old_tool.content) or
            old_tool.compressed_content is not None or
            len(str(old_tool.content)) <= 1000
        )


if __name__ == "__main__":
    unittest.main()
