# -*- coding: utf-8 -*-
"""Tool isolation tests — Tool.clone() protocol must prevent multiple agents
from corrupting each other's state when they share the same logical tool config
(Swarm clones, subagents, manual reuse).

All tests mock the OpenAI key — no real LLM calls.
"""
import unittest

from agentica.agent import Agent
from agentica.model.openai import OpenAIChat
from agentica.tools.builtin_task_tool import BuiltinTaskTool
from agentica.tools.buildin_tools import (
    BuiltinFileTool,
    BuiltinMemoryTool,
    BuiltinTodoTool,
)
from agentica.tools.skill_tool import SkillTool


def _model():
    return OpenAIChat(id="gpt-4o-mini", api_key="fake_openai_key")


class TestToolCloneProtocol(unittest.TestCase):
    """Each stateful builtin tool must override clone() to return a fresh
    instance with its own ``_agent`` / ``_parent_agent`` / ``_workspace`` slot.
    """

    def test_builtin_todo_tool_clone_is_fresh_instance(self):
        original = BuiltinTodoTool()
        clone = original.clone()
        self.assertIsNot(clone, original)
        self.assertIsNone(clone._agent)
        self.assertEqual(clone._todos, [])

    def test_builtin_task_tool_clone_is_fresh_instance(self):
        from unittest.mock import MagicMock
        sentinel_model = MagicMock(name="model_override")
        original = BuiltinTaskTool(model_override=sentinel_model)
        clone = original.clone()
        self.assertIsNot(clone, original)
        self.assertIsNone(clone._parent_agent)
        self.assertIs(clone._model_override, sentinel_model)

    def test_builtin_memory_tool_clone_is_fresh_instance(self):
        original = BuiltinMemoryTool()
        clone = original.clone()
        self.assertIsNot(clone, original)
        self.assertIsNone(clone._workspace)

    def test_skill_tool_clone_is_fresh_instance(self):
        original = SkillTool(custom_skill_dirs=["/tmp/skills"])
        clone = original.clone()
        self.assertIsNot(clone, original)
        self.assertIsNone(clone._agent)
        self.assertEqual(clone._custom_skill_dirs, ["/tmp/skills"])
        # Mutation on clone must not bleed into original
        clone._custom_skill_dirs.append("/tmp/extra")
        self.assertNotIn("/tmp/extra", original._custom_skill_dirs)

    def test_stateless_tool_clone_is_self(self):
        original = BuiltinFileTool(work_dir="/tmp")
        # Stateless tools default to returning self (no overhead)
        self.assertIs(original.clone(), original)


class TestAgentToolIsolation(unittest.TestCase):
    """Two agents constructed with the same tool *instance* must not share
    or overwrite each other's ``_agent`` / ``_parent_agent`` / ``_workspace``.
    """

    def test_two_agents_dont_share_todo_tool_state(self):
        shared_todo = BuiltinTodoTool()
        a1 = Agent(name="a1", model=_model(), tools=[shared_todo])
        a2 = Agent(name="a2", model=_model(), tools=[shared_todo])

        # The user's original tool was never mutated
        self.assertIsNone(shared_todo._agent)
        # Each agent owns its own clone
        a1_todo = next(t for t in a1.tools if isinstance(t, BuiltinTodoTool))
        a2_todo = next(t for t in a2.tools if isinstance(t, BuiltinTodoTool))
        self.assertIsNot(a1_todo, a2_todo)
        self.assertIsNot(a1_todo, shared_todo)
        self.assertIs(a1_todo._agent, a1)
        self.assertIs(a2_todo._agent, a2)

    def test_two_agents_dont_share_task_tool_state(self):
        shared_task = BuiltinTaskTool()
        a1 = Agent(name="a1", model=_model(), tools=[shared_task])
        a2 = Agent(name="a2", model=_model(), tools=[shared_task])

        self.assertIsNone(shared_task._parent_agent)
        a1_task = next(t for t in a1.tools if isinstance(t, BuiltinTaskTool))
        a2_task = next(t for t in a2.tools if isinstance(t, BuiltinTaskTool))
        self.assertIsNot(a1_task, a2_task)
        self.assertIs(a1_task._parent_agent, a1)
        self.assertIs(a2_task._parent_agent, a2)

    def test_agent_clone_isolates_tool_state(self):
        a1 = Agent(name="a1", model=_model(), tools=[BuiltinTodoTool(), BuiltinTaskTool()])
        a2 = a1.clone()

        a1_todo = next(t for t in a1.tools if isinstance(t, BuiltinTodoTool))
        a2_todo = next(t for t in a2.tools if isinstance(t, BuiltinTodoTool))
        a1_task = next(t for t in a1.tools if isinstance(t, BuiltinTaskTool))
        a2_task = next(t for t in a2.tools if isinstance(t, BuiltinTaskTool))

        self.assertIsNot(a1_todo, a2_todo)
        self.assertIsNot(a1_task, a2_task)
        self.assertIs(a1_todo._agent, a1)
        self.assertIs(a2_todo._agent, a2)
        self.assertIs(a1_task._parent_agent, a1)
        self.assertIs(a2_task._parent_agent, a2)


class TestSwarmCloneToolIsolation(unittest.TestCase):
    """Swarm._clone_agent_for_task must not let cloned agents stomp on the
    source agent's tool state. This is the bug the user explicitly asked to fix.
    """

    def test_swarm_clone_does_not_overwrite_source_agent_tool(self):
        from agentica.swarm import _clone_agent_for_task

        source = Agent(
            name="source",
            model=_model(),
            tools=[BuiltinTodoTool(), BuiltinTaskTool()],
        )
        source_todo = next(t for t in source.tools if isinstance(t, BuiltinTodoTool))
        source_task = next(t for t in source.tools if isinstance(t, BuiltinTaskTool))

        clone = _clone_agent_for_task(source)
        clone_todo = next(t for t in clone.tools if isinstance(t, BuiltinTodoTool))
        clone_task = next(t for t in clone.tools if isinstance(t, BuiltinTaskTool))

        # Source bindings are untouched
        self.assertIs(source_todo._agent, source)
        self.assertIs(source_task._parent_agent, source)
        # Clone has its own bindings
        self.assertIs(clone_todo._agent, clone)
        self.assertIs(clone_task._parent_agent, clone)
        # And they're different instances
        self.assertIsNot(clone_todo, source_todo)
        self.assertIsNot(clone_task, source_task)


class TestSubagentRegistryToolFiltering(unittest.TestCase):
    """Bug 1 regression: ``_select_child_tools`` must not let the filtered child
    tool's functions remain bound to the parent's tool instance — otherwise
    calling a write tool from the subagent mutates the parent agent's state.
    """

    def test_filtered_child_tool_does_not_mutate_parent_state(self):
        """write_todos invoked through the registry-filtered tool must mutate
        the *child* tool's bound agent, never the parent's todo list."""
        from agentica.subagent import (
            SubagentConfig,
            SubagentRegistry,
            SubagentType,
        )

        parent_agent = Agent(name="parent", model=_model(), tools=[BuiltinTodoTool()])
        parent_todo = next(t for t in parent_agent.tools if isinstance(t, BuiltinTodoTool))
        self.assertEqual(parent_agent.todos, [])

        config = SubagentConfig(
            type=SubagentType.CUSTOM,
            name="todo_only",
            description="only allowed to manage its own todos",
            system_prompt="-",
            allowed_tools=["write_todos"],
        )

        child_tools = SubagentRegistry()._select_child_tools(parent_agent.tools, config)
        self.assertEqual(len(child_tools), 1)
        filtered = child_tools[0]
        self.assertIsInstance(filtered, BuiltinTodoTool)
        self.assertIsNot(filtered, parent_todo)

        # Constructing the actual child Agent goes through ``_post_init`` →
        # ``Tool.clone`` again, which is the second-clone path that would have
        # silently restored the *full* function set if ``BuiltinTodoTool.clone``
        # didn't preserve ``self.functions`` keys.
        child_agent = Agent(name="child", model=_model(), tools=child_tools)
        child_todo = next(t for t in child_agent.tools if isinstance(t, BuiltinTodoTool))
        self.assertIs(child_todo._agent, child_agent)
        self.assertIsNot(child_todo, parent_todo)
        self.assertIn("write_todos", child_todo.functions)

        # Invoke via the registered Function entrypoint exactly the way the
        # Runner does — this is what would silently mutate the parent's state
        # if the entrypoint were still bound to ``parent_todo``.
        sample_todos = [{"content": "child-only", "status": "pending"}]
        child_todo.functions["write_todos"].entrypoint(todos=sample_todos)

        # Side effect landed on the child agent (write_todos may inject ``id``).
        self.assertEqual(len(child_agent.todos), 1)
        self.assertEqual(child_agent.todos[0]["content"], "child-only")
        # The bug we are guarding against:
        self.assertEqual(parent_agent.todos, [],
                         "parent agent's todos must not be mutated by the child tool")
        self.assertEqual(parent_todo._todos, [])


class TestAgentCloneRuntimeIsolation(unittest.TestCase):
    """Bug 2 regression: ``Agent.clone()`` must not alias mutable runtime
    containers (``todos``, ``context``) with the source agent.
    """

    def test_clone_does_not_share_todos_list(self):
        a = Agent(name="a", model=_model())
        a.todos.append({"content": "parent task", "status": "pending"})

        b = a.clone()
        self.assertIsNot(a.todos, b.todos,
                         "clone must own a fresh todos list")
        self.assertEqual(b.todos, [],
                         "clone should start with no todos, not inherit parent's")

        b.todos.append({"content": "clone task", "status": "pending"})
        self.assertEqual(len(a.todos), 1)
        self.assertEqual(a.todos[0]["content"], "parent task")

    def test_clone_does_not_share_context_dict(self):
        a = Agent(name="a", model=_model(), context={"shared_key": "v0"})
        b = a.clone()
        self.assertIsNot(a.context, b.context)
        b.context["shared_key"] = "mutated"
        b.context["new_key"] = "x"
        self.assertEqual(a.context["shared_key"], "v0")
        self.assertNotIn("new_key", a.context)


class TestSwarmCloneModelUsageIsolation(unittest.TestCase):
    """Bug 4 regression: ``_clone_agent_for_task`` must give each clone its own
    ``Usage`` instance — concurrent swarm tasks would otherwise pollute each
    other's token counters via the shared Usage from ``copy.copy(model)``.
    """

    def test_swarm_clone_isolates_model_usage(self):
        from agentica.swarm import _clone_agent_for_task

        source = Agent(name="source", model=_model())
        source.model.usage.total_tokens = 100  # simulate prior accumulation

        clone = _clone_agent_for_task(source)
        self.assertIsNot(clone.model.usage, source.model.usage,
                         "swarm clone must own a fresh Usage instance")
        # Mutating the clone's usage must not bleed back
        clone.model.usage.total_tokens = 999
        self.assertEqual(source.model.usage.total_tokens, 100)


class TestSubagentSpawnContextRobustness(unittest.TestCase):
    """Bug 3 regression: ``spawn()`` and ``_build_inherited_context`` must not
    crash on string contexts or dicts containing non-JSON-serializable values.
    """

    def test_build_inherited_context_handles_non_serializable_dict(self):
        from datetime import datetime
        from types import SimpleNamespace
        from agentica.subagent import SubagentRegistry

        parent = SimpleNamespace(
            working_memory=SimpleNamespace(summary=None),
            run_response=None,
            context={"started_at": datetime(2024, 1, 1), "fn": lambda: None},
        )
        result = SubagentRegistry._build_inherited_context(parent)
        self.assertIn("started_at", result)
        self.assertNotEqual(result, "")

    def test_build_inherited_context_handles_string_context(self):
        from types import SimpleNamespace
        from agentica.subagent import SubagentRegistry

        parent = SimpleNamespace(
            working_memory=SimpleNamespace(summary=None),
            run_response=None,
            context="raw string brief from upstream",
        )
        result = SubagentRegistry._build_inherited_context(parent)
        self.assertEqual(result, "raw string brief from upstream")


if __name__ == "__main__":
    unittest.main()
