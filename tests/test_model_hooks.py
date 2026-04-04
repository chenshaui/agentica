# -*- coding: utf-8 -*-
"""
Tests for Model-layer pre/post tool hooks:
- _build_pre_tool_hook() injection via update_model()
- Context overflow handling (context_overflow_threshold)
- Repetition detection (max_repeated_tool_calls)
- Fast path: neither feature enabled → hook is None
All tests mock LLM API keys — no real API calls.
"""
import asyncio
import unittest
from unittest.mock import MagicMock

from agentica.agent.config import ToolConfig
from agentica.model.message import Message


def _make_agent(tool_config=None):
    from agentica.agent import Agent
    from agentica.model.openai import OpenAIChat
    return Agent(
        model=OpenAIChat(id="gpt-4o-mini", api_key="fake_openai_key"),
        tool_config=tool_config or ToolConfig(),
    )


def _make_fc(name: str, args: dict):
    """Make a minimal FunctionCall-like mock."""
    fc = MagicMock()
    fc.function.name = name
    fc.arguments = args
    return fc


class TestHookInjection(unittest.TestCase):
    """update_model() must inject/clear _pre_tool_hook based on ToolConfig."""

    def test_both_disabled_hook_is_none(self):
        agent = _make_agent(ToolConfig())
        agent.update_model()
        self.assertIsNone(agent.model._pre_tool_hook)

    def test_overflow_only_hook_is_set(self):
        agent = _make_agent(ToolConfig(context_overflow_threshold=0.8))
        agent.update_model()
        self.assertIsNotNone(agent.model._pre_tool_hook)

    def test_repetition_only_hook_is_set(self):
        agent = _make_agent(ToolConfig(max_repeated_tool_calls=3))
        agent.update_model()
        self.assertIsNotNone(agent.model._pre_tool_hook)

    def test_both_enabled_hook_is_set(self):
        agent = _make_agent(ToolConfig(context_overflow_threshold=0.8, max_repeated_tool_calls=3))
        agent.update_model()
        self.assertIsNotNone(agent.model._pre_tool_hook)

    def test_hook_cleared_on_subsequent_update_model_if_disabled(self):
        """Switching to disabled config clears the hook on re-initialization."""
        agent = _make_agent(ToolConfig(max_repeated_tool_calls=3))
        agent.update_model()
        self.assertIsNotNone(agent.model._pre_tool_hook)
        # Switch to disabled
        agent.tool_config = ToolConfig()
        agent.update_model()
        self.assertIsNone(agent.model._pre_tool_hook)


class TestRepetitionDetection(unittest.TestCase):
    """_pre_tool_hook must detect and break repetitive tool call loops."""

    def _make_agent_with_repeat(self, n=3):
        agent = _make_agent(ToolConfig(max_repeated_tool_calls=n))
        agent.update_model()
        return agent

    def test_identical_calls_triggers_injection(self):
        agent = self._make_agent_with_repeat(n=3)
        agent.model.function_call_stack = [
            _make_fc("web_search", {"query": "python async"}),
            _make_fc("web_search", {"query": "python async"}),
            _make_fc("web_search", {"query": "python async"}),
        ]
        messages = [Message(role="user", content="search")]
        result = asyncio.run(agent.model._pre_tool_hook(messages, []))
        self.assertTrue(result, "Hook should return True to skip the tool batch")

    def test_injection_adds_user_message_naming_the_tool(self):
        agent = self._make_agent_with_repeat(n=3)
        agent.model.function_call_stack = [
            _make_fc("read_file", {"path": "/foo"}),
            _make_fc("read_file", {"path": "/foo"}),
            _make_fc("read_file", {"path": "/foo"}),
        ]
        messages = [Message(role="user", content="read file")]
        asyncio.run(agent.model._pre_tool_hook(messages, []))
        injected = messages[-1]
        self.assertEqual(injected.role, "user")
        self.assertIn("read_file", injected.content)
        self.assertIn("3 times", injected.content)

    def test_mixed_tools_no_trigger(self):
        agent = self._make_agent_with_repeat(n=3)
        agent.model.function_call_stack = [
            _make_fc("web_search", {"q": "a"}),
            _make_fc("read_file", {"path": "x"}),
            _make_fc("web_search", {"q": "b"}),
        ]
        messages = [Message(role="user", content="search")]
        original_len = len(messages)
        result = asyncio.run(agent.model._pre_tool_hook(messages, []))
        self.assertFalse(result)
        self.assertEqual(len(messages), original_len)

    def test_same_tool_different_args_no_trigger(self):
        agent = self._make_agent_with_repeat(n=3)
        agent.model.function_call_stack = [
            _make_fc("web_search", {"query": "python"}),
            _make_fc("web_search", {"query": "golang"}),
            _make_fc("web_search", {"query": "rust"}),
        ]
        messages = [Message(role="user", content="search")]
        result = asyncio.run(agent.model._pre_tool_hook(messages, []))
        self.assertFalse(result)

    def test_fewer_than_n_calls_no_trigger(self):
        agent = self._make_agent_with_repeat(n=3)
        agent.model.function_call_stack = [
            _make_fc("web_search", {"query": "test"}),
            _make_fc("web_search", {"query": "test"}),
        ]
        messages = [Message(role="user", content="x")]
        result = asyncio.run(agent.model._pre_tool_hook(messages, []))
        self.assertFalse(result)

    def test_empty_stack_no_trigger(self):
        agent = self._make_agent_with_repeat(n=3)
        agent.model.function_call_stack = []
        messages = [Message(role="user", content="x")]
        result = asyncio.run(agent.model._pre_tool_hook(messages, []))
        self.assertFalse(result)


class TestContextOverflowHandling(unittest.TestCase):
    """_pre_tool_hook must evict old messages when context is near capacity."""

    def _make_agent_with_overflow(self, threshold=0.5, window=200):
        agent = _make_agent(ToolConfig(context_overflow_threshold=threshold))
        agent.update_model()
        agent.model.context_window = window
        return agent

    def test_overflow_evicts_oldest_non_system_message(self):
        # window=200 tokens, threshold=0.5 → trigger at 100 tokens = 400 chars
        # Fill with 500 chars of content so 500/4=125 tokens, 125/200=62.5% > 50%
        agent = self._make_agent_with_overflow(threshold=0.5, window=200)
        messages = [
            Message(role="system", content="You are helpful."),
            Message(role="user", content="A" * 150),
            Message(role="assistant", content="B" * 150),
            Message(role="user", content="C" * 150),
        ]
        result = asyncio.run(agent.model._pre_tool_hook(messages, []))
        self.assertFalse(result, "Overflow evicts but does not skip tool batch")
        self.assertLess(len(messages), 4, "At least one message should be evicted")

    def test_system_message_always_preserved(self):
        agent = self._make_agent_with_overflow(threshold=0.1, window=50)
        messages = [
            Message(role="system", content="System prompt."),
            Message(role="user", content="X" * 200),
        ]
        asyncio.run(agent.model._pre_tool_hook(messages, []))
        self.assertEqual(messages[0].role, "system")

    def test_no_overflow_no_eviction(self):
        agent = self._make_agent_with_overflow(threshold=0.9, window=100000)
        messages = [
            Message(role="system", content="System."),
            Message(role="user", content="short message"),
        ]
        original_len = len(messages)
        asyncio.run(agent.model._pre_tool_hook(messages, []))
        self.assertEqual(len(messages), original_len)

    def test_overflow_returns_false_not_true(self):
        """Context overflow evicts messages but does NOT skip the tool batch."""
        agent = self._make_agent_with_overflow(threshold=0.1, window=10)
        messages = [
            Message(role="system", content="Sys"),
            Message(role="user", content="A" * 50),
        ]
        result = asyncio.run(agent.model._pre_tool_hook(messages, []))
        self.assertFalse(result)


class TestPostToolHook(unittest.TestCase):
    """_post_tool_hook: None when no TodoTool, set when TodoTool is present."""

    def test_post_tool_hook_is_none_without_todo_tool(self):
        """Without BuiltinTodoTool, _post_tool_hook should be None."""
        agent = _make_agent()
        agent.update_model()
        self.assertIsNone(agent.model._post_tool_hook)

    def test_post_tool_hook_is_set_with_todo_tool(self):
        """With BuiltinTodoTool, _post_tool_hook should be an async callable."""
        from agentica.tools.buildin_tools import BuiltinTodoTool
        agent = _make_agent_with_todo_tool()
        agent.update_model()
        self.assertIsNotNone(agent.model._post_tool_hook)

    def test_post_tool_hook_is_none_when_reminder_disabled(self):
        """With todo_reminder_interval=0, _post_tool_hook should be None."""
        from agentica.tools.buildin_tools import BuiltinTodoTool
        from agentica.agent.config import PromptConfig
        from agentica.agent import Agent
        from agentica.model.openai import OpenAIChat
        agent = Agent(
            model=OpenAIChat(id="gpt-4o-mini", api_key="fake_openai_key"),
            tools=[BuiltinTodoTool()],
            prompt_config=PromptConfig(todo_reminder_interval=0),
        )
        agent.update_model()
        self.assertIsNone(agent.model._post_tool_hook)


class TestTodoReminder(unittest.TestCase):
    """Integration tests for _post_tool_hook todo reminder injection."""

    def _make_agent_with_todos(self, interval=3, todos=None):
        agent = _make_agent_with_todo_tool(reminder_interval=interval)
        agent.update_model()
        if todos:
            agent.todos = todos
        return agent

    def test_no_reminder_when_write_todos_recent(self):
        """If write_todos was called recently, no reminder should be injected."""
        agent = self._make_agent_with_todos(
            interval=3,
            todos=[{"content": "Task A", "status": "pending"}],
        )
        # Simulate: write_todos was 1 assistant turn ago
        messages = [
            Message(role="user", content="do task"),
            Message(role="assistant", content="ok"),
            Message(role="tool", tool_name="write_todos", content="{}"),
            Message(role="assistant", content="updated todos"),
        ]
        original_len = len(messages)
        asyncio.run(agent.model._post_tool_hook(messages, []))
        self.assertEqual(len(messages), original_len, "No reminder should be injected")

    def test_reminder_injected_after_enough_turns(self):
        """After enough assistant turns without write_todos, reminder is injected."""
        agent = self._make_agent_with_todos(
            interval=2,
            todos=[{"content": "Task A", "status": "in_progress"}],
        )
        # Simulate: no write_todos call, 3 assistant turns
        messages = [
            Message(role="user", content="do stuff"),
            Message(role="assistant", content="doing 1"),
            Message(role="user", content="next"),
            Message(role="assistant", content="doing 2"),
            Message(role="user", content="next"),
            Message(role="assistant", content="doing 3"),
        ]
        asyncio.run(agent.model._post_tool_hook(messages, []))
        # Should have injected a reminder
        self.assertEqual(len(messages), 7)
        injected = messages[-1]
        self.assertEqual(injected.role, "user")
        self.assertIn("[Todo Reminder]", injected.content)
        self.assertIn("Task A", injected.content)

    def test_no_reminder_when_todos_empty(self):
        """No reminder when there are no active todos."""
        agent = self._make_agent_with_todos(interval=1, todos=[])
        messages = [
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
            Message(role="assistant", content="hello2"),
        ]
        original_len = len(messages)
        asyncio.run(agent.model._post_tool_hook(messages, []))
        self.assertEqual(len(messages), original_len)

    def test_no_double_reminder(self):
        """Should not inject reminder if a recent reminder already exists within interval."""
        agent = self._make_agent_with_todos(
            interval=3,
            todos=[{"content": "Task A", "status": "pending"}],
        )
        # Simulate: reminder was injected, then only 2 assistant turns (< interval=3)
        messages = [
            Message(role="user", content="[Todo Reminder] ..."),
            Message(role="assistant", content="ok noted"),
            Message(role="assistant", content="working..."),
        ]
        original_len = len(messages)
        asyncio.run(agent.model._post_tool_hook(messages, []))
        self.assertEqual(len(messages), original_len, "No double reminder")


def _make_agent_with_todo_tool(reminder_interval=10):
    """Helper: create agent with BuiltinTodoTool registered."""
    from agentica.agent import Agent
    from agentica.model.openai import OpenAIChat
    from agentica.tools.buildin_tools import BuiltinTodoTool
    from agentica.agent.config import PromptConfig
    return Agent(
        model=OpenAIChat(id="gpt-4o-mini", api_key="fake_openai_key"),
        tools=[BuiltinTodoTool()],
        prompt_config=PromptConfig(todo_reminder_interval=reminder_interval),
    )


if __name__ == "__main__":
    unittest.main()
