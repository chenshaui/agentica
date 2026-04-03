# -*- coding: utf-8 -*-
"""Tests for the agentic loop refactor (recursion -> while loop).

Tests the shared safety helpers and loop behavior in Model base class.
All tests mock LLM API keys per project convention.
"""
import asyncio
import weakref
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentica.model.loop_state import LoopState
from agentica.model.message import Message
from agentica.model.response import ModelResponse


# ---------------------------------------------------------------------------
# Concrete Model subclass for testing (cannot instantiate abstract Model)
# ---------------------------------------------------------------------------

class _MockModel:
    """Minimal mock that has the agentic loop methods from Model.

    We import and bind the real methods from Model base class
    so we test actual logic, not mock behavior.
    """

    def __init__(self):
        from agentica.model.base import Model
        self.id = "mock-model"
        self._agent_ref = None
        self._cost_tracker = None
        self._max_cost_usd = None
        self._in_agentic_loop = False
        self._last_stream_finish_reason = None

        # Bind real methods from Model
        self._check_death_spiral = Model._check_death_spiral.__get__(self, type(self))
        self._check_cost_budget = Model._check_cost_budget.__get__(self, type(self))
        self._check_agent_cancelled = Model._check_agent_cancelled.__get__(self, type(self))
        self._response_has_tool_calls = Model._response_has_tool_calls.__get__(self, type(self))
        self._try_reactive_compact = Model._try_reactive_compact.__get__(self, type(self))
        self._call_with_retry = Model._call_with_retry.__get__(self, type(self))
        self.agentic_loop = Model.agentic_loop.__get__(self, type(self))
        self.agentic_loop_stream = Model.agentic_loop_stream.__get__(self, type(self))
        self._maybe_compress_messages = AsyncMock()
        self.response = AsyncMock()
        self.response_stream = AsyncMock()


# ---------------------------------------------------------------------------
# _check_death_spiral tests
# ---------------------------------------------------------------------------

class TestCheckDeathSpiral:
    def test_increments_on_all_errors(self):
        model = _MockModel()
        state = LoopState(death_spiral_threshold=3)
        messages = [
            Message(role="assistant", content="calling tools"),
            Message(role="tool", content="error1", tool_call_error=True),
            Message(role="tool", content="error2", tool_call_error=True),
        ]
        assert model._check_death_spiral(messages, state) is False
        assert state.consecutive_all_error_turns == 1

    def test_resets_on_success(self):
        model = _MockModel()
        state = LoopState(death_spiral_threshold=3)
        state.consecutive_all_error_turns = 2

        messages = [
            Message(role="assistant", content="calling tools"),
            Message(role="tool", content="ok"),
        ]
        assert model._check_death_spiral(messages, state) is False
        assert state.consecutive_all_error_turns == 0

    def test_triggers_at_threshold(self):
        model = _MockModel()
        state = LoopState(death_spiral_threshold=2)
        messages = [
            Message(role="assistant", content=""),
            Message(role="tool", content="err", tool_call_error=True),
        ]
        model._check_death_spiral(messages, state)  # 1
        assert model._check_death_spiral(messages, state) is True  # 2 >= threshold

    def test_no_tool_messages(self):
        model = _MockModel()
        state = LoopState()
        messages = [Message(role="assistant", content="hello")]
        assert model._check_death_spiral(messages, state) is False
        assert state.consecutive_all_error_turns == 0


# ---------------------------------------------------------------------------
# _check_cost_budget tests
# ---------------------------------------------------------------------------

class TestCheckCostBudget:
    def test_no_budget_set(self):
        model = _MockModel()
        assert model._check_cost_budget() is None

    def test_under_budget(self):
        model = _MockModel()
        model._max_cost_usd = 1.0
        model._cost_tracker = MagicMock(total_cost_usd=0.5)
        assert model._check_cost_budget() is None

    def test_over_budget(self):
        model = _MockModel()
        model._max_cost_usd = 1.0
        model._cost_tracker = MagicMock(total_cost_usd=1.5)
        result = model._check_cost_budget()
        assert result is not None
        assert "exceeded" in result.lower()

    def test_exactly_at_budget(self):
        model = _MockModel()
        model._max_cost_usd = 1.0
        model._cost_tracker = MagicMock(total_cost_usd=1.0)
        result = model._check_cost_budget()
        assert result is not None  # >= triggers


# ---------------------------------------------------------------------------
# _check_agent_cancelled tests
# ---------------------------------------------------------------------------

class TestCheckAgentCancelled:
    def test_not_cancelled(self):
        model = _MockModel()
        agent = MagicMock(_cancelled=False)
        model._check_agent_cancelled(agent)  # should not raise

    def test_cancelled_raises(self):
        model = _MockModel()
        agent = MagicMock(_cancelled=True)
        with pytest.raises(RuntimeError, match="cancelled"):
            model._check_agent_cancelled(agent)
        assert agent._cancelled is False  # reset after raise

    def test_no_agent(self):
        model = _MockModel()
        model._check_agent_cancelled(None)  # should not raise


# ---------------------------------------------------------------------------
# _response_has_tool_calls tests
# ---------------------------------------------------------------------------

class TestResponseHasToolCalls:
    def test_tool_results_present(self):
        model = _MockModel()
        messages = [
            Message(role="assistant", content="", tool_calls=[{"id": "1"}]),
            Message(role="tool", content="result"),
        ]
        assert model._response_has_tool_calls(messages) is True

    def test_no_tool_calls(self):
        model = _MockModel()
        messages = [
            Message(role="assistant", content="final answer"),
        ]
        assert model._response_has_tool_calls(messages) is False

    def test_empty_messages(self):
        model = _MockModel()
        assert model._response_has_tool_calls([]) is False


# ---------------------------------------------------------------------------
# agentic_loop integration tests
# ---------------------------------------------------------------------------

class TestAgenticLoop:
    @pytest.mark.asyncio
    async def test_death_spiral_stops_loop(self):
        model = _MockModel()

        messages = [
            Message(role="assistant", content=""),
            Message(role="tool", content="fail", tool_call_error=True),
        ]
        _OrigLoopState = LoopState
        with patch("agentica.model.loop_state.LoopState", lambda **kw: _OrigLoopState(death_spiral_threshold=1, **kw)):
            result = await model.agentic_loop(messages=messages, model_response=ModelResponse())
        assert result.content is not None
        assert "error" in result.content.lower()

    @pytest.mark.asyncio
    async def test_cost_budget_stops_loop(self):
        model = _MockModel()
        model._max_cost_usd = 0.01
        model._cost_tracker = MagicMock(total_cost_usd=0.02)

        messages = [
            Message(role="assistant", content=""),
            Message(role="tool", content="ok"),
        ]
        result = await model.agentic_loop(messages=messages, model_response=ModelResponse())
        assert result.content is not None
        assert "budget" in result.content.lower()

    @pytest.mark.asyncio
    async def test_stop_after_tool_call(self):
        model = _MockModel()
        messages = [
            Message(role="assistant", content="done", stop_after_tool_call=True),
        ]
        result = await model.agentic_loop(messages=messages, model_response=ModelResponse())
        assert result.content == "done"

    @pytest.mark.asyncio
    async def test_no_tool_calls_terminates(self):
        """When response has no tool calls, loop should terminate."""
        model = _MockModel()

        async def mock_response(messages):
            # Simulate what real response() does: append assistant msg without tool_calls
            messages.append(Message(role="assistant", content="final"))
            return ModelResponse(content="final")

        model.response = mock_response

        messages = [
            Message(role="assistant", content=""),
            Message(role="tool", content="ok"),
        ]
        result = await model.agentic_loop(messages=messages, model_response=ModelResponse())
        assert "final" in result.content

    @pytest.mark.asyncio
    async def test_cancellation_stops_loop(self):
        model = _MockModel()
        agent = MagicMock(_cancelled=True)
        model._agent_ref = weakref.ref(agent)

        messages = [
            Message(role="assistant", content=""),
            Message(role="tool", content="ok"),
        ]
        with pytest.raises(RuntimeError, match="cancelled"):
            await model.agentic_loop(messages=messages, model_response=ModelResponse())

    @pytest.mark.asyncio
    async def test_max_tokens_recovery(self):
        """Test that finish_reason='length' triggers max_tokens recovery."""
        model = _MockModel()
        call_count = 0

        async def mock_response(messages):
            nonlocal call_count
            call_count += 1
            resp = ModelResponse(content=f"part{call_count}")
            if call_count == 1:
                resp.finish_reason = "length"
                # First call: add assistant with no tool_calls (truncated output)
                messages.append(Message(role="assistant", content=f"part{call_count}"))
            else:
                resp.finish_reason = "stop"
                # Second call: final answer
                messages.append(Message(role="assistant", content=f"part{call_count}"))
            return resp

        model.response = mock_response

        messages = [
            Message(role="assistant", content=""),
            Message(role="tool", content="ok"),
        ]
        result = await model.agentic_loop(messages=messages, model_response=ModelResponse())
        assert call_count == 2
        assert "part1" in result.content
        assert "part2" in result.content


# ---------------------------------------------------------------------------
# agentic_loop_stream integration tests
# ---------------------------------------------------------------------------

class TestAgenticLoopStream:
    @pytest.mark.asyncio
    async def test_death_spiral_in_stream(self):
        model = _MockModel()

        # response_stream must be an async generator, not AsyncMock
        async def _fake_stream(messages):
            yield ModelResponse(content="chunk")

        model.response_stream = _fake_stream

        messages = [
            Message(role="assistant", content=""),
            Message(role="tool", content="fail", tool_call_error=True),
        ]
        chunks = []
        _OrigLoopState = LoopState
        with patch("agentica.model.loop_state.LoopState", lambda **kw: _OrigLoopState(death_spiral_threshold=1, **kw)):
            async for chunk in model.agentic_loop_stream(messages=messages):
                chunks.append(chunk)
        assert any("Error" in (c.content or "") for c in chunks)

    @pytest.mark.asyncio
    async def test_cost_budget_in_stream(self):
        model = _MockModel()
        model._max_cost_usd = 0.01
        model._cost_tracker = MagicMock(total_cost_usd=0.02)

        messages = [
            Message(role="assistant", content=""),
            Message(role="tool", content="ok"),
        ]
        chunks = []
        async for chunk in model.agentic_loop_stream(messages=messages):
            chunks.append(chunk)
        assert any("budget" in (c.content or "").lower() for c in chunks)

    @pytest.mark.asyncio
    async def test_no_tool_calls_terminates_stream(self):
        model = _MockModel()

        async def mock_stream(messages):
            # Simulate adding assistant message without tool_calls
            messages.append(Message(role="assistant", content="streamed"))
            yield ModelResponse(content="streamed")

        model.response_stream = mock_stream

        messages = [
            Message(role="assistant", content=""),
            Message(role="tool", content="ok"),
        ]
        chunks = []
        async for chunk in model.agentic_loop_stream(messages=messages):
            chunks.append(chunk)
        assert any("streamed" in (c.content or "") for c in chunks)

    @pytest.mark.asyncio
    async def test_max_tokens_recovery_in_stream(self):
        """Stream path should detect finish_reason='length' and continue."""
        model = _MockModel()
        call_count = 0

        async def mock_stream(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: truncated output (finish_reason = "length")
                messages.append(Message(role="assistant", content=f"part{call_count}"))
                model._last_stream_finish_reason = "length"
                yield ModelResponse(content=f"part{call_count}")
            else:
                # Second call: final answer
                messages.append(Message(role="assistant", content=f"part{call_count}"))
                model._last_stream_finish_reason = "stop"
                yield ModelResponse(content=f"part{call_count}")

        model.response_stream = mock_stream

        messages = [
            Message(role="assistant", content=""),
            Message(role="tool", content="ok"),
        ]
        chunks = []
        async for chunk in model.agentic_loop_stream(messages=messages):
            chunks.append(chunk)
        # Should have called twice: first truncated, then continued
        assert call_count == 2
        contents = "".join(c.content or "" for c in chunks)
        assert "part1" in contents
        assert "part2" in contents
        # Check that a "Continue" message was injected
        assert any(m.role == "user" and "Continue" in (m.content or "") for m in messages)


# ---------------------------------------------------------------------------
# Sentinel flag tests
# ---------------------------------------------------------------------------

class TestSentinelFlag:
    @pytest.mark.asyncio
    async def test_call_with_retry_sets_and_clears_flag(self):
        model = _MockModel()
        flag_during_call = None

        async def mock_response(messages):
            nonlocal flag_during_call
            flag_during_call = model._in_agentic_loop
            return ModelResponse(content="ok")

        model.response = mock_response
        state = LoopState()

        await model._call_with_retry(messages=[], state=state)
        assert flag_during_call is True  # was True during call
        assert model._in_agentic_loop is False  # cleared after

    @pytest.mark.asyncio
    async def test_flag_cleared_on_exception(self):
        model = _MockModel()

        async def mock_response(messages):
            raise ValueError("test error")

        model.response = mock_response
        state = LoopState(max_api_retry=1)

        with pytest.raises(ValueError):
            await model._call_with_retry(messages=[], state=state)
        assert model._in_agentic_loop is False  # cleared even on error
