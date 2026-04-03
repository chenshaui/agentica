# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Unit tests for Agent.as_tool(), clone(), MessageBus, AsyncAgentRegistry,
structured results, and dynamic parent_messages.
"""
import asyncio
import sys
import os
import json
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentica import Agent
from agentica.agent.config import PromptConfig, TeamConfig
from agentica.agent.team import (
    AsyncAgentRegistry,
    MessageBus,
    _serialize_result,
    _serialize_content,
    _get_parent_messages,
    get_agent_result,
)
from agentica.tools.base import Function
from agentica.run_response import RunResponse, RunEvent
from agentica.model.message import Message


async def _mock_run_stream(content, **kwargs):
    """Helper: create a mock async iterator that yields a single RunResponse."""
    yield RunResponse(content=content)


class TestClone(unittest.TestCase):
    """Test Agent.clone() resets all mutable runtime state."""

    def test_clone_basic(self):
        agent = Agent(name="Worker", instructions="Work hard")
        original_id = agent.agent_id
        clone = agent.clone()

        self.assertNotEqual(clone.agent_id, original_id)
        self.assertIsNone(clone.run_id)
        self.assertFalse(clone._running)
        self.assertIsNone(clone._run_hooks)
        self.assertIsNone(clone._enabled_tools)
        self.assertIsNone(clone._enabled_skills)

    def test_clone_resets_session_log(self):
        agent = Agent(name="Worker", instructions="Test", session_id="test-session")
        self.assertIsNotNone(agent._session_log)
        clone = agent.clone()
        self.assertIsNone(clone._session_log)

    def test_clone_resets_default_run_hooks(self):
        agent = Agent(name="Worker", instructions="Test")
        from agentica.hooks import ConversationArchiveHooks
        agent._default_run_hooks = ConversationArchiveHooks()
        clone = agent.clone()
        self.assertIsNone(clone._default_run_hooks)

    def test_clone_resets_transfer_caller(self):
        agent = Agent(name="Worker", instructions="Test")
        parent = Agent(name="Parent", instructions="Parent")
        agent._transfer_caller = parent
        clone = agent.clone()
        self.assertIsNone(clone._transfer_caller)

    def test_clone_shares_config(self):
        """Clone shares heavy config (model def, tools, instructions)."""
        agent = Agent(name="Worker", instructions="Do stuff", tools=[])
        clone = agent.clone()
        # Same name and instructions
        self.assertEqual(clone.name, agent.name)
        self.assertEqual(clone.instructions, agent.instructions)
        # Fresh working memory
        self.assertIsNot(clone.working_memory, agent.working_memory)


class TestAsToolBasic(unittest.TestCase):
    """Test as_tool() basic behavior."""

    def test_as_tool_returns_function(self):
        agent = Agent(name="Test Agent", instructions="You are a test agent")
        tool = agent.as_tool()
        self.assertIsInstance(tool, Function)

    def test_as_tool_default_name_from_agent_name(self):
        agent = Agent(name="Chinese Translator", instructions="Translate to Chinese")
        tool = agent.as_tool()
        self.assertEqual(tool.name, "chinese_translator")

    def test_as_tool_custom_name(self):
        agent = Agent(name="Chinese Translator", instructions="Translate to Chinese")
        tool = agent.as_tool(tool_name="translate_zh")
        self.assertEqual(tool.name, "translate_zh")

    def test_as_tool_default_description_from_agent_description(self):
        agent = Agent(name="Translator", description="A professional translator agent", instructions="Translate text")
        tool = agent.as_tool()
        self.assertIn("A professional translator agent", tool.description)

    def test_as_tool_default_description_from_when_to_use(self):
        agent = Agent(
            name="Translator",
            description="A translator",
            when_to_use="Use when the user needs text translated to Chinese",
            instructions="Translate text",
        )
        tool = agent.as_tool()
        self.assertIn("Use when the user needs text translated to Chinese", tool.description)

    def test_as_tool_default_description_from_agent_role(self):
        agent = Agent(
            name="Translator",
            prompt_config=PromptConfig(role="Professional Chinese translator"),
            instructions="Translate text",
        )
        tool = agent.as_tool()
        self.assertIn("Professional Chinese translator", tool.description)

    def test_as_tool_custom_description(self):
        agent = Agent(name="Translator", instructions="Translate text")
        tool = agent.as_tool(tool_description="Custom description for translation")
        self.assertIn("Custom description for translation", tool.description)

    def test_as_tool_name_fallback_to_agent_id(self):
        agent = Agent(instructions="Test agent")
        tool = agent.as_tool()
        self.assertTrue(tool.name.startswith("agent_"))
        self.assertEqual(len(tool.name), 14)  # "agent_" + 8 chars

    def test_as_tool_has_entrypoint(self):
        agent = Agent(name="Test Agent", instructions="Test")
        tool = agent.as_tool()
        self.assertIsNotNone(tool.entrypoint)
        self.assertTrue(callable(tool.entrypoint))


class TestAsToolExecution(unittest.TestCase):
    """Test as_tool() execution."""

    def test_as_tool_calls_agent_run(self):
        agent = Agent(name="Translator", instructions="Translate text")
        tool = agent.as_tool()

        with patch.object(Agent, 'run_stream', side_effect=lambda msg, **kw: _mock_run_stream("result text")):
            result = asyncio.run(tool.entrypoint("Hello world"))

        self.assertEqual(result, "result text")

    def test_as_tool_handles_none_content(self):
        agent = Agent(name="Translator", instructions="Translate text")
        tool = agent.as_tool()

        async def _empty_stream(msg, **kw):
            yield RunResponse(content=None)

        with patch.object(Agent, 'run_stream', side_effect=_empty_stream):
            result = asyncio.run(tool.entrypoint("Hello"))

        self.assertEqual(result, "No response from agent.")

    def test_as_tool_custom_output_extractor(self):
        def custom_extractor(response: RunResponse) -> str:
            return f"Extracted: {response.content} (run_id: {response.run_id})"

        agent = Agent(name="Translator", instructions="Translate text")
        tool = agent.as_tool(custom_output_extractor=custom_extractor)

        async def _stream_with_content(msg, **kw):
            yield RunResponse(content="raw output", run_id="test-123")

        with patch.object(Agent, 'run_stream', side_effect=_stream_with_content):
            result = asyncio.run(tool.entrypoint("Hello"))

        self.assertEqual(result, "Extracted: raw output (run_id: test-123)")

    def test_as_tool_fallback_to_run_on_stream_error(self):
        agent = Agent(name="Analyzer", instructions="Analyze text")
        tool = agent.as_tool()

        mock_response = RunResponse(content={"key": "value", "number": 42})

        with patch.object(Agent, 'run_stream', side_effect=Exception("not streamable")), \
             patch.object(Agent, 'run', new_callable=AsyncMock, return_value=mock_response):
            result = asyncio.run(tool.entrypoint("Analyze this"))

        # Should be JSON serialized (structured result)
        self.assertIn('"key"', result)
        self.assertIn('"value"', result)


class TestStructuredResultPassing(unittest.TestCase):
    """Test _serialize_result preserves structured info."""

    def test_simple_string_content(self):
        response = RunResponse(content="Hello world")
        result = _serialize_result(response)
        self.assertEqual(result, "Hello world")

    def test_with_reasoning(self):
        response = RunResponse(content="Answer", reasoning_content="Step 1, Step 2")
        result = _serialize_result(response)
        parsed = json.loads(result)
        self.assertEqual(parsed["content"], "Answer")
        self.assertEqual(parsed["reasoning"], "Step 1, Step 2")

    def test_with_tool_calls(self):
        response = RunResponse(
            content="Final answer",
            tools=[
                {"tool_name": "search", "tool_args": {"q": "test"}, "content": "search result"},
                {"tool_name": "calc", "tool_args": {"expr": "1+1"}, "content": "2", "tool_call_error": False},
            ],
        )
        result = _serialize_result(response)
        parsed = json.loads(result)
        self.assertEqual(parsed["content"], "Final answer")
        self.assertEqual(len(parsed["tool_calls"]), 2)
        self.assertEqual(parsed["tool_calls"][0]["tool_name"], "search")
        self.assertFalse(parsed["tool_calls"][1]["is_error"])

    def test_serialize_content_dict(self):
        result = _serialize_content({"key": "value"})
        parsed = json.loads(result)
        self.assertEqual(parsed["key"], "value")

    def test_serialize_content_str(self):
        result = _serialize_content("plain text")
        self.assertEqual(result, "plain text")


class TestAsyncAgentRegistry(unittest.TestCase):
    """Test AsyncAgentRegistry thread-safe singleton and notification queue."""

    def setUp(self):
        # Reset singleton for isolation
        AsyncAgentRegistry._instance = None

    def test_singleton(self):
        r1 = AsyncAgentRegistry.get_instance()
        r2 = AsyncAgentRegistry.get_instance()
        self.assertIs(r1, r2)

    def test_register_and_get_status(self):
        registry = AsyncAgentRegistry.get_instance()

        async def _dummy():
            await asyncio.sleep(100)

        loop = asyncio.new_event_loop()
        task = loop.create_task(_dummy())
        registry.register("agent-1", task)
        self.assertEqual(registry.get_status("agent-1"), "running")
        task.cancel()
        loop.close()

    def test_set_result_queues_notification(self):
        registry = AsyncAgentRegistry.get_instance()
        registry.set_result("agent-1", {"status": "completed", "content": "Done!"})

        self.assertEqual(registry.get_status("agent-1"), "completed")

        # Notification should be queued
        notifications = registry.drain_notifications()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["agent_id"], "agent-1")
        self.assertEqual(notifications[0]["status"], "completed")

        # Second drain should be empty
        self.assertEqual(len(registry.drain_notifications()), 0)

    def test_get_agent_result(self):
        registry = AsyncAgentRegistry.get_instance()
        registry.set_result("agent-2", {"status": "completed", "content": "Result"})
        registry.drain_notifications()  # clear

        result = asyncio.run(get_agent_result("agent-2"))
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "completed")
        self.assertEqual(parsed["content"], "Result")

    def test_get_agent_result_unknown(self):
        registry = AsyncAgentRegistry.get_instance()
        result = asyncio.run(get_agent_result("nonexistent"))
        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "unknown")

    def test_cleanup_completed(self):
        registry = AsyncAgentRegistry.get_instance()
        registry.set_result("a1", {"status": "completed", "content": "x"})
        registry.set_result("a2", {"status": "failed", "error": "y"})
        registry.drain_notifications()

        count = registry.cleanup_completed()
        self.assertEqual(count, 1)  # only "completed" cleaned
        self.assertIsNone(registry.get_result("a1"))
        self.assertIsNotNone(registry.get_result("a2"))


class TestMessageBus(unittest.TestCase):
    """Test MessageBus peer-to-peer messaging."""

    def setUp(self):
        MessageBus._instance = None

    def test_singleton(self):
        b1 = MessageBus.get_instance()
        b2 = MessageBus.get_instance()
        self.assertIs(b1, b2)

    def test_send_and_check(self):
        bus = MessageBus.get_instance()
        bus.send("alice", "bob", "Hello Bob!")
        bus.send("alice", "bob", "Second message")

        msgs = bus.check_messages("bob")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["from"], "alice")
        self.assertEqual(msgs[0]["message"], "Hello Bob!")
        self.assertEqual(msgs[1]["message"], "Second message")

        # After drain, empty
        self.assertEqual(len(bus.check_messages("bob")), 0)

    def test_has_messages(self):
        bus = MessageBus.get_instance()
        self.assertFalse(bus.has_messages("carol"))
        bus.send("dave", "carol", "Ping")
        self.assertTrue(bus.has_messages("carol"))

    def test_broadcast(self):
        bus = MessageBus.get_instance()
        # Initialize mailboxes
        bus._mailboxes["alice"] = []
        bus._mailboxes["bob"] = []
        bus._mailboxes["carol"] = []

        bus.broadcast("alice", "Team update!", exclude=["carol"])

        bob_msgs = bus.check_messages("bob")
        self.assertEqual(len(bob_msgs), 1)
        self.assertEqual(bob_msgs[0]["message"], "Team update!")

        # alice (sender) and carol (excluded) should not receive
        self.assertEqual(len(bus.check_messages("alice")), 0)
        self.assertEqual(len(bus.check_messages("carol")), 0)

    def test_no_messages_returns_empty(self):
        bus = MessageBus.get_instance()
        msgs = bus.check_messages("nobody")
        self.assertEqual(msgs, [])


class TestDynamicParentMessages(unittest.TestCase):
    """Test _get_parent_messages captures context at call time."""

    def test_disabled_by_default(self):
        agent = Agent(name="Parent", instructions="Test")
        result = _get_parent_messages(agent)
        self.assertIsNone(result)

    def test_enabled_captures_recent_messages(self):
        agent = Agent(
            name="Parent",
            instructions="Test",
            team_config=TeamConfig(share_parent_context=True, parent_context_window=3),
        )
        # Simulate messages in working memory
        agent.working_memory.add_message(Message(role="user", content="msg1"))
        agent.working_memory.add_message(Message(role="assistant", content="msg2"))
        agent.working_memory.add_message(Message(role="user", content="msg3"))
        agent.working_memory.add_message(Message(role="assistant", content="msg4"))

        result = _get_parent_messages(agent)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 3)  # last 3 messages
        self.assertEqual(result[0].content, "msg2")
        self.assertEqual(result[2].content, "msg4")

    def test_empty_memory_returns_none(self):
        agent = Agent(
            name="Parent",
            instructions="Test",
            team_config=TeamConfig(share_parent_context=True, parent_context_window=5),
        )
        result = _get_parent_messages(agent)
        self.assertIsNone(result)


class TestBackgroundMode(unittest.TestCase):
    """Test background=True as_tool mode."""

    def test_background_tool_description(self):
        agent = Agent(name="BGWorker", instructions="Work in background")
        tool = agent.as_tool(background=True)
        self.assertIn("[async]", tool.description)
        self.assertEqual(tool.name, "bgworker")

    def test_background_returns_async_launched(self):
        """Background tool should return async_launched JSON immediately."""
        agent = Agent(name="BGWorker", instructions="Background")
        tool = agent.as_tool(background=True)

        # Patch run to simulate slow work
        async def _slow_run(msg, **kw):
            await asyncio.sleep(10)
            return RunResponse(content="Done")

        with patch.object(Agent, 'run', new_callable=AsyncMock, side_effect=_slow_run):
            result = asyncio.run(tool.entrypoint("Do something"))

        parsed = json.loads(result)
        self.assertEqual(parsed["status"], "async_launched")
        self.assertIn("agent_id", parsed)


class TestWhenToUse(unittest.TestCase):
    """Test when_to_use routing hint."""

    def test_when_to_use_in_as_tool(self):
        agent = Agent(
            name="Coder",
            when_to_use="Use for code generation and debugging tasks",
            instructions="Generate code",
        )
        tool = agent.as_tool()
        self.assertEqual(tool.description, "Use for code generation and debugging tasks")

    def test_when_to_use_in_transfer_function(self):
        agent = Agent(
            name="Coder",
            when_to_use="Use for code generation and debugging tasks",
            instructions="Generate code",
        )
        transfer = agent.get_transfer_function()
        self.assertEqual(transfer.description, "Use for code generation and debugging tasks")


class TestTeamTools(unittest.TestCase):
    """Test get_tools() includes messaging and get_agent_result tools."""

    def test_team_adds_messaging_tools(self):
        child = Agent(name="Worker", instructions="Work")
        parent = Agent(
            name="Orchestrator",
            instructions="Orchestrate",
            team=[child],
        )
        tools = parent.get_tools()
        tool_names = [t.name if hasattr(t, 'name') else str(t) for t in tools]
        self.assertIn("send_message", tool_names)
        self.assertIn("check_messages", tool_names)
        self.assertIn("get_agent_result", tool_names)
        self.assertIn("transfer_to_worker", tool_names)

    def test_team_transfer_prompt_includes_messaging_info(self):
        child1 = Agent(name="Alice", instructions="Work")
        child2 = Agent(name="Bob", instructions="Work")
        parent = Agent(
            name="Orchestrator",
            instructions="Orchestrate",
            team=[child1, child2],
        )
        prompt = parent.get_transfer_prompt()
        self.assertIn("Inter-Agent Communication", prompt)
        self.assertIn("Alice", prompt)
        self.assertIn("Bob", prompt)


class TestIntegration(unittest.TestCase):
    """Integration tests for Agent as Tool pattern with mocked LLM."""

    def test_orchestrator_with_agent_tools(self):
        translator_agent = Agent(
            name="Chinese Translator",
            instructions="Translate to Chinese",
        )
        orchestrator = Agent(
            name="Orchestrator",
            instructions="Use translator when asked.",
            tools=[
                translator_agent.as_tool(
                    tool_name="translate_to_chinese",
                    tool_description="Translate text to Chinese",
                ),
            ],
        )
        self.assertIsNotNone(orchestrator.tools)
        self.assertEqual(len(orchestrator.tools), 1)
        tool = orchestrator.tools[0]
        self.assertIsInstance(tool, Function)
        self.assertEqual(tool.name, "translate_to_chinese")

    def test_multiple_agent_tools(self):
        translator = Agent(name="Translator", instructions="Translate")
        summarizer = Agent(name="Summarizer", instructions="Summarize")
        analyzer = Agent(name="Analyzer", instructions="Analyze")

        orchestrator = Agent(
            name="Orchestrator",
            instructions="Coordinate",
            tools=[
                translator.as_tool(tool_name="translate", tool_description="Translate"),
                summarizer.as_tool(tool_name="summarize", tool_description="Summarize"),
                analyzer.as_tool(tool_name="analyze", tool_description="Analyze"),
            ],
        )
        self.assertEqual(len(orchestrator.tools), 3)
        tool_names = [t.name for t in orchestrator.tools]
        self.assertIn("translate", tool_names)
        self.assertIn("summarize", tool_names)
        self.assertIn("analyze", tool_names)

    def test_agent_tool_execution_chain(self):
        summarizer = Agent(name="Summarizer", instructions="Summarize")
        translator = Agent(name="Translator", instructions="Translate")

        summarize_tool = summarizer.as_tool(tool_name="summarize")
        translate_tool = translator.as_tool(tool_name="translate")

        call_count = 0

        async def _mock_stream(msg, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield RunResponse(content="AI is intelligence demonstrated by machines.")
            else:
                yield RunResponse(content="AI is machine intelligence. (Chinese)")

        with patch.object(Agent, 'run_stream', side_effect=_mock_stream):
            summary = asyncio.run(summarize_tool.entrypoint("Long text about AI..."))
            self.assertEqual(summary, "AI is intelligence demonstrated by machines.")

            translation = asyncio.run(translate_tool.entrypoint(summary))
            self.assertEqual(translation, "AI is machine intelligence. (Chinese)")

        self.assertEqual(call_count, 2)


if __name__ == "__main__":
    unittest.main()
