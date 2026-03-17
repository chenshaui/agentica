# -*- coding: utf-8 -*-
"""Tests for BuiltinMemoryTool (save_memory, read_memory, search_memory) and auto-archive."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentica.tools.buildin_tools import BuiltinMemoryTool
from agentica.agent.config import WorkspaceMemoryConfig


class TestBuiltinMemoryToolReadMemory:
    """Tests for read_memory method."""

    def test_read_memory_returns_content(self):
        workspace = MagicMock()
        workspace.get_memory_prompt = AsyncMock(
            return_value="## Long-term Memory\n\nUser prefers Python."
        )
        tool = BuiltinMemoryTool(workspace=workspace)

        result = asyncio.run(tool.read_memory(days=7))
        data = json.loads(result)

        assert data["success"] is True
        assert data["days"] == 7
        assert "User prefers Python" in data["content"]
        workspace.get_memory_prompt.assert_awaited_once_with(days=7)

    def test_read_memory_empty(self):
        workspace = MagicMock()
        workspace.get_memory_prompt = AsyncMock(return_value="")
        tool = BuiltinMemoryTool(workspace=workspace)

        result = asyncio.run(tool.read_memory(days=3))
        data = json.loads(result)

        assert data["success"] is True
        assert data["content"] == "No memories found."

    def test_read_memory_exception(self):
        workspace = MagicMock()
        workspace.get_memory_prompt = AsyncMock(side_effect=IOError("disk error"))
        tool = BuiltinMemoryTool(workspace=workspace)

        result = asyncio.run(tool.read_memory())
        data = json.loads(result)

        assert data["success"] is False
        assert "disk error" in data["error"]


class TestBuiltinMemoryToolSearchMemory:
    """Tests for search_memory method."""

    def test_search_memory_returns_results(self):
        workspace = MagicMock()
        workspace.search_memory = MagicMock(return_value=[
            {"content": "User prefers Python", "file_path": "memory/2024-01-01.md", "score": 1.0},
        ])
        tool = BuiltinMemoryTool(workspace=workspace)

        result = tool.search_memory(query="Python", limit=5)
        data = json.loads(result)

        assert data["success"] is True
        assert data["count"] == 1
        assert data["results"][0]["content"] == "User prefers Python"
        workspace.search_memory.assert_called_once_with(query="Python", limit=5)

    def test_search_memory_no_results(self):
        workspace = MagicMock()
        workspace.search_memory = MagicMock(return_value=[])
        tool = BuiltinMemoryTool(workspace=workspace)

        result = tool.search_memory(query="nonexistent")
        data = json.loads(result)

        assert data["success"] is True
        assert data["count"] == 0
        assert data["results"] == []

    def test_search_memory_empty_query(self):
        workspace = MagicMock()
        tool = BuiltinMemoryTool(workspace=workspace)

        result = tool.search_memory(query="")
        data = json.loads(result)

        assert data["success"] is False
        assert "empty" in data["error"].lower()

    def test_search_memory_exception(self):
        workspace = MagicMock()
        workspace.search_memory = MagicMock(side_effect=RuntimeError("search failed"))
        tool = BuiltinMemoryTool(workspace=workspace)

        result = tool.search_memory(query="test")
        data = json.loads(result)

        assert data["success"] is False
        assert "search failed" in data["error"]


class TestBuiltinMemoryToolNoWorkspace:
    """Tests for all methods when no workspace is configured."""

    def test_save_memory_no_workspace(self):
        tool = BuiltinMemoryTool(workspace=None)
        result = asyncio.run(tool.save_memory("test"))
        data = json.loads(result)
        assert data["success"] is False
        assert "No workspace" in data["error"]

    def test_read_memory_no_workspace(self):
        tool = BuiltinMemoryTool(workspace=None)
        result = asyncio.run(tool.read_memory())
        data = json.loads(result)
        assert data["success"] is False
        assert "No workspace" in data["error"]

    def test_search_memory_no_workspace(self):
        tool = BuiltinMemoryTool(workspace=None)
        result = tool.search_memory(query="test")
        data = json.loads(result)
        assert data["success"] is False
        assert "No workspace" in data["error"]


class TestWorkspaceMemoryConfigAutoArchive:
    """Tests for auto_archive config field."""

    def test_default_auto_archive_false(self):
        config = WorkspaceMemoryConfig()
        assert config.auto_archive is False

    def test_auto_archive_enabled(self):
        config = WorkspaceMemoryConfig(auto_archive=True)
        assert config.auto_archive is True


class TestAutoArchive:
    """Tests for auto-archive via ConversationArchiveHooks injection."""

    def test_auto_archive_injects_hooks(self, tmp_path):
        """Verify that when auto_archive=True, Agent injects ConversationArchiveHooks."""
        from agentica.agent.base import Agent
        from agentica.hooks import ConversationArchiveHooks

        agent = Agent(
            workspace=str(tmp_path / "ws"),
            long_term_memory_config=WorkspaceMemoryConfig(auto_archive=True),
        )
        assert agent._default_run_hooks is not None
        assert isinstance(agent._default_run_hooks, ConversationArchiveHooks)

    def test_auto_archive_disabled_no_hooks(self):
        """Verify that when auto_archive=False, no default hooks are injected."""
        from agentica.agent.base import Agent

        agent = Agent(
            long_term_memory_config=WorkspaceMemoryConfig(auto_archive=False),
        )
        assert agent._default_run_hooks is None
