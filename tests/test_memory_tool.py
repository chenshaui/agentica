# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Tests for BuiltinMemoryTool and MemoryExtractHooks
"""
import asyncio
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentica.workspace import Workspace
from agentica.tools.buildin_tools import BuiltinMemoryTool
from agentica.hooks import MemoryExtractHooks, ConversationArchiveHooks


class TestBuiltinMemoryTool:
    """Test BuiltinMemoryTool save_memory and search_memory."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Workspace(self.temp_dir)
        self.workspace.initialize()
        self.tool = BuiltinMemoryTool()
        self.tool.set_workspace(self.workspace)

    def teardown_method(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init(self):
        """Test BuiltinMemoryTool initialization."""
        tool = BuiltinMemoryTool()
        assert tool.name == "builtin_memory_tool"
        assert "save_memory" in tool.functions
        assert "search_memory" in tool.functions

    def test_get_system_prompt(self):
        """Test that system prompt contains memory instructions."""
        prompt = self.tool.get_system_prompt()
        assert prompt is not None
        assert "save_memory" in prompt
        assert "search_memory" in prompt
        assert "user" in prompt
        assert "feedback" in prompt
        assert "project" in prompt
        assert "reference" in prompt

    def test_save_memory(self):
        """Test saving a memory entry."""
        result = asyncio.run(self.tool.save_memory(
            title="user_role",
            content="User is a Python developer",
            memory_type="user",
        ))
        assert "Memory saved" in result
        assert "user_role" in result

        # Verify file was created
        memory_dir = self.workspace._get_user_memory_dir()
        files = list(memory_dir.glob("*.md"))
        assert len(files) == 1
        assert "user_user_role" in files[0].name

    def test_save_memory_invalid_type(self):
        """Test saving with invalid memory type."""
        result = asyncio.run(self.tool.save_memory(
            title="test",
            content="test content",
            memory_type="invalid",
        ))
        assert "Error" in result
        assert "invalid" in result.lower()

    def test_save_memory_empty_title(self):
        """Test saving with empty title."""
        result = asyncio.run(self.tool.save_memory(
            title="",
            content="test content",
        ))
        assert "Error" in result

    def test_save_memory_empty_content(self):
        """Test saving with empty content."""
        result = asyncio.run(self.tool.save_memory(
            title="test",
            content="",
        ))
        assert "Error" in result

    def test_save_memory_no_workspace(self):
        """Test saving without workspace configured."""
        tool = BuiltinMemoryTool()
        result = asyncio.run(tool.save_memory(
            title="test",
            content="test content",
        ))
        assert "Error" in result
        assert "No workspace" in result

    def test_search_memory(self):
        """Test searching memories."""
        # Save some memories first
        asyncio.run(self.tool.save_memory(
            title="python_preference",
            content="User prefers Python for AI development",
            memory_type="user",
        ))
        asyncio.run(self.tool.save_memory(
            title="testing_framework",
            content="User prefers pytest over unittest",
            memory_type="feedback",
        ))

        # Search
        result = self.tool.search_memory("python")
        parsed = json.loads(result)
        assert len(parsed) >= 1

    def test_search_memory_no_results(self):
        """Test searching with no matching results."""
        result = self.tool.search_memory("nonexistent_term_xyz")
        assert "No memories found" in result

    def test_search_memory_no_workspace(self):
        """Test searching without workspace configured."""
        tool = BuiltinMemoryTool()
        result = tool.search_memory("test")
        assert "Error" in result

    def test_memory_index_updated(self):
        """Test that MEMORY.md index is updated after save."""
        asyncio.run(self.tool.save_memory(
            title="deploy_target",
            content="Deploy to AWS Lambda",
            memory_type="project",
        ))

        index_path = self.workspace._get_user_memory_md()
        assert index_path.exists()
        content = index_path.read_text(encoding="utf-8")
        assert "deploy_target" in content

    def test_save_multiple_memories(self):
        """Test saving multiple memories."""
        types = ["user", "feedback", "project", "reference"]
        for i, mem_type in enumerate(types):
            asyncio.run(self.tool.save_memory(
                title=f"memory_{i}",
                content=f"Content for memory {i}",
                memory_type=mem_type,
            ))

        memory_dir = self.workspace._get_user_memory_dir()
        files = list(memory_dir.glob("*.md"))
        assert len(files) == 4

    def test_search_memory_chinese(self):
        """Test searching with Chinese query (CJK bigram matching)."""
        asyncio.run(self.tool.save_memory(
            title="deploy_target",
            content="部署目标是阿里云的函数计算服务",
            memory_type="project",
        ))
        asyncio.run(self.tool.save_memory(
            title="user_lang",
            content="用户偏好使用中文交流",
            memory_type="user",
        ))

        # Chinese query should match via character bigrams
        result = self.tool.search_memory("部署")
        parsed = json.loads(result)
        assert len(parsed) >= 1
        # The deploy memory should match
        assert any("部署" in r["content"] for r in parsed)

    def test_search_memory_mixed_lang(self):
        """Test searching with mixed Chinese+English query."""
        asyncio.run(self.tool.save_memory(
            title="framework_choice",
            content="项目使用FastAPI框架开发REST API",
            memory_type="project",
        ))

        # Mixed query: English word "FastAPI" + Chinese context
        result = self.tool.search_memory("FastAPI框架")
        parsed = json.loads(result)
        assert len(parsed) >= 1
        assert any("FastAPI" in r["content"] for r in parsed)


class TestMemoryExtractHooks:
    """Test MemoryExtractHooks auto-extraction logic."""

    def test_init(self):
        """Test MemoryExtractHooks initialization."""
        hooks = MemoryExtractHooks()
        assert isinstance(hooks._run_inputs, dict)
        assert isinstance(hooks._tool_calls, dict)

    def test_skips_when_save_memory_called(self):
        """Test that extraction is skipped when save_memory was already called."""
        hooks = MemoryExtractHooks()

        agent = MagicMock()
        agent.agent_id = "test_agent"
        agent.run_input = "test input"
        agent.workspace = MagicMock()
        agent.model = MagicMock()

        # Simulate: on_agent_start -> on_tool_end(save_memory) -> on_agent_end
        asyncio.run(hooks.on_agent_start(agent=agent))
        asyncio.run(hooks.on_tool_end(agent=agent, tool_name="save_memory"))
        asyncio.run(hooks.on_agent_end(agent=agent, output="test output"))

        # model.ainvoke should NOT be called (extraction skipped)
        agent.model.ainvoke.assert_not_called()

    def test_skips_when_no_workspace(self):
        """Test that extraction is skipped when no workspace."""
        hooks = MemoryExtractHooks()

        agent = MagicMock()
        agent.agent_id = "test_agent"
        agent.run_input = "test input"
        agent.workspace = None

        asyncio.run(hooks.on_agent_start(agent=agent))
        asyncio.run(hooks.on_agent_end(agent=agent, output="test output"))
        # Should not crash

    def test_tracks_tool_calls(self):
        """Test that tool calls are tracked correctly."""
        hooks = MemoryExtractHooks()

        agent = MagicMock()
        agent.agent_id = "test_agent"
        agent.run_input = "test"

        asyncio.run(hooks.on_agent_start(agent=agent))
        asyncio.run(hooks.on_tool_end(agent=agent, tool_name="read_file"))
        asyncio.run(hooks.on_tool_end(agent=agent, tool_name="save_memory"))

        assert "save_memory" in hooks._tool_calls["test_agent"]


class TestAgentAutoMemoryRegistration:
    """Test that Agent auto-registers BuiltinMemoryTool when workspace is set."""

    def test_auto_register_memory_tool(self):
        """Test that BuiltinMemoryTool is auto-registered when workspace exists."""
        from agentica.agent.base import Agent
        from agentica.model.openai import OpenAIChat

        temp_dir = tempfile.mkdtemp()
        workspace = Workspace(temp_dir)
        workspace.initialize()

        agent = Agent(
            model=OpenAIChat(api_key="fake_openai_key"),
            workspace=workspace,
        )

        # Check that BuiltinMemoryTool is in the tools list
        has_memory_tool = False
        for tool in (agent.tools or []):
            if isinstance(tool, BuiltinMemoryTool):
                has_memory_tool = True
                break
        assert has_memory_tool, "BuiltinMemoryTool should be auto-registered when workspace exists"

        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_no_duplicate_memory_tool(self):
        """Test that BuiltinMemoryTool is not duplicated if already provided."""
        from agentica.agent.base import Agent
        from agentica.model.openai import OpenAIChat

        temp_dir = tempfile.mkdtemp()
        workspace = Workspace(temp_dir)
        workspace.initialize()

        manual_tool = BuiltinMemoryTool()
        agent = Agent(
            model=OpenAIChat(api_key="fake_openai_key"),
            workspace=workspace,
            tools=[manual_tool],
        )

        # Count BuiltinMemoryTool instances
        count = sum(1 for t in (agent.tools or []) if isinstance(t, BuiltinMemoryTool))
        assert count == 1, "Should not duplicate BuiltinMemoryTool"

        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_no_memory_tool_without_workspace(self):
        """Test that BuiltinMemoryTool is NOT registered without workspace."""
        from agentica.agent.base import Agent
        from agentica.model.openai import OpenAIChat

        agent = Agent(
            model=OpenAIChat(api_key="fake_openai_key"),
        )

        has_memory_tool = any(
            isinstance(t, BuiltinMemoryTool)
            for t in (agent.tools or [])
        )
        assert not has_memory_tool, "BuiltinMemoryTool should NOT be registered without workspace"
