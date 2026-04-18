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
    """Minimal Agent stand-in.

    The new ``SubagentRegistry.spawn`` drives the child via ``run_stream`` and
    yields a single ``RunResponse``-shaped chunk to capture the final content.
    The shim also exposes ``model.usage.merge`` so the registry's usage
    aggregation step is exercised without instantiating a real model.
    """

    last_init_kwargs = None
    run_delay = 0.0

    def __init__(self, **kwargs):
        RecordingAgent.last_init_kwargs = kwargs
        self.name = kwargs.get("name", "child")
        # Preserve the cloned model passed by ``SubagentRegistry.spawn`` so the
        # registry's ``parent.usage.merge(child.usage)`` step gets a real
        # ``Usage`` instance.
        self.model = kwargs.get("model")

    async def run_stream(self, task, config=None):
        await asyncio.sleep(self.run_delay)
        yield SimpleNamespace(event="RunResponse", content=f"done:{task}", tools=None)


class _FakeModel:
    """Stand-in for ``Model`` so ``copy.copy`` clones cleanly during tests."""

    def __init__(self):
        self.tools = None
        self.functions = None
        self.function_call_stack = None
        self.tool_choice = None
        self.metrics = {}
        from agentica.model.usage import Usage
        self.usage = Usage()


def _make_parent_agent():
    working_memory = SimpleNamespace(summary=SimpleNamespace(summary="parent summary"))
    return SimpleNamespace(
        name="parent",
        agent_id="parent-agent-id",
        model=_FakeModel(),
        tools=[
            Function(name="read_file", entrypoint=lambda: None),
            Function(name="write_file", entrypoint=lambda: None),
            Function(name="task", entrypoint=lambda: None),
        ],
        workspace="workspace-ref",
        knowledge="knowledge-ref",
        working_memory=working_memory,
        context={},
        _event_callback=None,
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


class _CumulativeToolStreamAgent:
    """Mimics ``Agent.run_stream`` producing the cumulative ``chunk.tools`` list
    that ``Runner`` actually emits — every ToolCall* event includes ALL tool
    calls so far in the run, not just the newly-affected one. The registry is
    expected to dedupe by ``tool_call_id``.
    """

    last_init_kwargs = None

    def __init__(self, **kwargs):
        _CumulativeToolStreamAgent.last_init_kwargs = kwargs
        self.name = kwargs.get("name", "child")
        self.model = kwargs.get("model")

    async def run_stream(self, task, config=None):
        # Simulate two tool calls (read_file + ls), each going through
        # started -> completed, with the cumulative list growing each chunk.
        t1 = {"id": "call_1", "tool_name": "read_file",
              "tool_args": {"file_path": "a.py"}}
        t2 = {"id": "call_2", "tool_name": "ls",
              "tool_args": {"directory": "."}}
        # call_1 started
        yield SimpleNamespace(event="ToolCallStarted", content=None, tools=[dict(t1)])
        # call_1 completed (cumulative list still has call_1, now with content)
        t1_done = {**t1, "content": "file content"}
        yield SimpleNamespace(event="ToolCallCompleted", content=None, tools=[t1_done])
        # call_2 started — chunk.tools now has both
        yield SimpleNamespace(event="ToolCallStarted", content=None,
                              tools=[t1_done, dict(t2)])
        # call_2 completed — chunk.tools still has both, both with content
        t2_done = {**t2, "content": "listing"}
        yield SimpleNamespace(event="ToolCallCompleted", content=None,
                              tools=[t1_done, t2_done])
        yield SimpleNamespace(event="RunResponse", content=f"done:{task}", tools=None)


def test_spawn_dedupes_subagent_tool_events_by_call_id():
    """Regression: cumulative chunk.tools must not cause duplicate
    subagent.tool_started/completed events or inflate tool_count."""
    registry = SubagentRegistry()
    _CUSTOM_SUBAGENT_CONFIGS["coder"] = SubagentConfig(
        type=SubagentType.CUSTOM,
        name="coder",
        description="coder",
        system_prompt="prompt",
    )
    parent = _make_parent_agent()
    received = []
    parent._event_callback = received.append

    with patch("agentica.agent.Agent", _CumulativeToolStreamAgent), patch(
        "agentica.agent.config.ToolConfig", FakeToolConfig
    ):
        result = asyncio.run(
            registry.spawn(parent_agent=parent, task="do work", agent_type="coder")
        )

    assert result["status"] == "completed"
    assert result["tool_count"] == 2, (
        f"Expected exactly 2 tool calls, got {result['tool_count']}. "
        "Cumulative chunk.tools must be deduped by tool_call_id."
    )
    started = [e for e in received if e["type"] == "subagent.tool_started"]
    completed = [e for e in received if e["type"] == "subagent.tool_completed"]
    assert len(started) == 2
    assert len(completed) == 2
    assert [e["tool_name"] for e in started] == ["read_file", "ls"]
    assert [e["tool_name"] for e in completed] == ["read_file", "ls"]


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
