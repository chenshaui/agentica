# -*- coding: utf-8 -*-
"""Tests for agentica.subagent registry execution helpers."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentica.subagent import (
    SubagentConfig,
    SubagentRegistry,
    SubagentType,
    _CUSTOM_SUBAGENT_CONFIGS,
)
from agentica.tools.base import Function, Tool


class FakeToolConfig:
    def __init__(self, tool_call_limit=None):
        self.tool_call_limit = tool_call_limit


class RecordingAgent:
    last_init_kwargs = None
    run_delay = 0.0

    def __init__(self, **kwargs):
        RecordingAgent.last_init_kwargs = kwargs

    async def run(self, task):
        await asyncio.sleep(self.run_delay)
        return SimpleNamespace(content=f"done:{task}")


def _make_parent_agent():
    working_memory = SimpleNamespace(summary=SimpleNamespace(summary="parent summary"))
    return SimpleNamespace(
        name="parent",
        model=object(),
        tools=[
            Function(name="read_file", entrypoint=lambda: None),
            Function(name="write_file", entrypoint=lambda: None),
            Function(name="task", entrypoint=lambda: None),
        ],
        workspace="workspace-ref",
        knowledge="knowledge-ref",
        working_memory=working_memory,
        context={},
    )


def _make_toolkit():
    def read_file():
        return None

    def write_file():
        return None

    def task():
        return None

    toolkit = Tool(name="file_tools")
    toolkit.register(read_file)
    toolkit.register(write_file)
    toolkit.register(task)
    return toolkit


@pytest.fixture(autouse=True)
def reset_subagent_registry():
    SubagentRegistry._instance = None
    _CUSTOM_SUBAGENT_CONFIGS.clear()
    RecordingAgent.last_init_kwargs = None
    RecordingAgent.run_delay = 0.0
    yield
    SubagentRegistry._instance = None
    _CUSTOM_SUBAGENT_CONFIGS.clear()


def test_spawn_applies_config_to_child_agent():
    registry = SubagentRegistry()
    _CUSTOM_SUBAGENT_CONFIGS["reviewer"] = SubagentConfig(
        type=SubagentType.CUSTOM,
        name="reviewer",
        description="reviewer",
        system_prompt="system prompt",
        allowed_tools=["read_file", "task"],
        denied_tools=["task"],
        tool_call_limit=7,
        can_spawn_subagents=False,
        inherit_workspace=True,
        inherit_knowledge=True,
        inherit_context=True,
        timeout=5,
    )

    parent = _make_parent_agent()

    with patch("agentica.agent.Agent", RecordingAgent), patch(
        "agentica.agent.config.ToolConfig", FakeToolConfig
    ):
        result = asyncio.run(
            registry.spawn(parent_agent=parent, task="review this", agent_type="reviewer")
        )

    assert result["status"] == "completed"
    init_kwargs = RecordingAgent.last_init_kwargs
    assert init_kwargs is not None
    assert "parent summary" in init_kwargs["instructions"]
    assert [tool.name for tool in init_kwargs["tools"]] == ["read_file"]
    assert init_kwargs["workspace"] == "workspace-ref"
    assert init_kwargs["knowledge"] == "knowledge-ref"
    assert init_kwargs["tool_config"].tool_call_limit == 7
    assert init_kwargs["context"]["_subagent_depth"] == 1
    assert init_kwargs["context"]["_can_spawn_subagents"] is False


def test_spawn_filters_toolkit_functions_by_allowed_and_denied_lists():
    registry = SubagentRegistry()
    _CUSTOM_SUBAGENT_CONFIGS["reviewer"] = SubagentConfig(
        type=SubagentType.CUSTOM,
        name="reviewer",
        description="reviewer",
        system_prompt="system prompt",
        allowed_tools=["read_file", "task"],
        denied_tools=["task"],
    )
    parent = _make_parent_agent()
    parent.tools = [_make_toolkit()]

    with patch("agentica.agent.Agent", RecordingAgent), patch(
        "agentica.agent.config.ToolConfig", FakeToolConfig
    ):
        result = asyncio.run(
            registry.spawn(parent_agent=parent, task="review this", agent_type="reviewer")
        )

    assert result["status"] == "completed"
    init_kwargs = RecordingAgent.last_init_kwargs
    assert init_kwargs is not None
    assert len(init_kwargs["tools"]) == 1
    toolkit = init_kwargs["tools"][0]
    assert isinstance(toolkit, Tool)
    assert list(toolkit.functions.keys()) == ["read_file"]


def test_spawn_honors_timeout():
    registry = SubagentRegistry()
    _CUSTOM_SUBAGENT_CONFIGS["slow"] = SubagentConfig(
        type=SubagentType.CUSTOM,
        name="slow",
        description="slow",
        system_prompt="system prompt",
        timeout=0.01,
    )
    parent = _make_parent_agent()
    RecordingAgent.run_delay = 0.05

    with patch("agentica.agent.Agent", RecordingAgent), patch(
        "agentica.agent.config.ToolConfig", FakeToolConfig
    ):
        result = asyncio.run(
            registry.spawn(parent_agent=parent, task="slow task", agent_type="slow")
        )

    assert result["status"] == "error"
    assert "timed out" in result["error"].lower()


def test_spawn_batch_returns_error_for_invalid_spec_and_keeps_order():
    registry = SubagentRegistry()
    _CUSTOM_SUBAGENT_CONFIGS["reviewer"] = SubagentConfig(
        type=SubagentType.CUSTOM,
        name="reviewer",
        description="reviewer",
        system_prompt="system prompt",
    )
    parent = _make_parent_agent()

    with patch("agentica.agent.Agent", RecordingAgent), patch(
        "agentica.agent.config.ToolConfig", FakeToolConfig
    ):
        results = asyncio.run(
            registry.spawn_batch(
                parent_agent=parent,
                tasks=[
                    {"type": "reviewer"},
                    {"task": "valid task", "type": "reviewer"},
                ],
            )
        )

    assert len(results) == 2
    assert results[0]["status"] == "error"
    assert "task" in results[0]["error"].lower()
    assert results[1]["status"] == "completed"
    assert results[1]["content"] == "done:valid task"
