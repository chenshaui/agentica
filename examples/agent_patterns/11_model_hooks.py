# -*- coding: utf-8 -*-
"""
Model-layer hooks demo: context overflow handling + repetition detection

Demonstrates two opt-in "deep agent" capabilities added directly to Agent
via ToolConfig — no subclass needed:

1. context_overflow_threshold: when estimated token usage reaches the
   threshold fraction of context_window, old non-system messages are
   evicted (FIFO) before the next tool call. Prevents silent context
   overflow errors.

2. max_repeated_tool_calls: if the same tool is called with identical
   arguments N times in a row, a strategy-change message is injected
   and the current tool batch is skipped so the model can reconsider.
   Breaks infinite tool-call loops.

Usage:
    python examples/agent_patterns/11_model_hooks.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
from unittest.mock import AsyncMock, MagicMock

from agentica import Agent, OpenAIChat
from agentica.agent.config import ToolConfig
from agentica.model.message import Message
from agentica.run_response import RunResponse, RunEvent


# ---------------------------------------------------------------------------
# Helper: build a minimal fake run_response
# ---------------------------------------------------------------------------

def _mock_response(text="ok"):
    r = RunResponse(content=text, event=RunEvent.run_response.value)
    return r


# ---------------------------------------------------------------------------
# Demo 1: Repetition detection (no real LLM needed)
# ---------------------------------------------------------------------------

def demo_repetition_detection():
    print("=" * 60)
    print("Demo 1: Repetition Detection")
    print("=" * 60)
    print("""
    If the same tool is called with identical args N times in a row,
    a strategy-change message is injected and the tool batch is skipped.
    This breaks loops like: agent keeps calling web_search with the same
    query because the results aren't what it expected.
    """)

    agent = Agent(
        name="repetition-demo",
        model=OpenAIChat(id="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY", "fake")),
        tool_config=ToolConfig(
            max_repeated_tool_calls=3,   # trigger after 3 identical calls
        ),
    )
    agent.update_model()

    hook = agent.model._pre_tool_hook
    if hook is None:
        print("  [SKIP] Hook is None — max_repeated_tool_calls=0 (disabled)")
        return

    # Simulate 3 identical web_search calls in the stack
    fc = MagicMock()
    fc.function.name = "web_search"
    fc.arguments = {"query": "python async tutorial"}
    agent.model.function_call_stack = [fc, fc, fc]

    messages = [Message(role="user", content="Find me a python async tutorial")]
    n_before = len(messages)

    skip = asyncio.run(hook(messages, []))

    print(f"  function_call_stack: 3× web_search('python async tutorial')")
    print(f"  Hook returned: skip={skip}")
    print(f"  Messages before hook: {n_before}")
    print(f"  Messages after hook:  {len(messages)}")
    if len(messages) > n_before:
        injected = messages[-1]
        print(f"  Injected message role: {injected.role}")
        print(f"  Injected message (first 120 chars): {injected.content[:120]}...")
    print()

    print("  Usage:")
    print("    agent = Agent(")
    print("        tool_config=ToolConfig(max_repeated_tool_calls=3),")
    print("        ...)")
    print("  Set max_repeated_tool_calls=0 to disable (default).")


# ---------------------------------------------------------------------------
# Demo 2: Context overflow handling (no real LLM needed)
# ---------------------------------------------------------------------------

def demo_context_overflow():
    print("=" * 60)
    print("Demo 2: Context Overflow Handling")
    print("=" * 60)
    print("""
    When estimated token usage reaches context_overflow_threshold × context_window,
    oldest non-system messages are evicted (FIFO) to make room.
    System message is always preserved. Eviction continues until usage drops
    below threshold + 5pp hard limit.
    """)

    agent = Agent(
        name="overflow-demo",
        model=OpenAIChat(id="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY", "fake")),
        tool_config=ToolConfig(
            context_overflow_threshold=0.6,  # trigger at 60% of context_window
        ),
    )
    agent.update_model()
    # Use a tiny window so the demo is easy to trigger
    agent.model.context_window = 200

    hook = agent.model._pre_tool_hook
    if hook is None:
        print("  [SKIP] Hook is None — context_overflow_threshold=0.0 (disabled)")
        return

    # Build a message history that exceeds 60% of 200 tokens (= 120 tokens = 480 chars)
    messages = [
        Message(role="system", content="You are a helpful AI assistant."),
        Message(role="user", content="A" * 160),
        Message(role="assistant", content="B" * 160),
        Message(role="user", content="C" * 160),
        Message(role="assistant", content="D" * 160),
    ]
    n_before = len(messages)
    total_chars_before = sum(len(str(m.content) or "") for m in messages)
    estimated_tokens_before = total_chars_before / 4

    skip = asyncio.run(hook(messages, []))

    total_chars_after = sum(len(str(m.content) or "") for m in messages)
    estimated_tokens_after = total_chars_after / 4

    print(f"  context_window: 200 tokens, threshold: 60%  (trigger at 120 tokens)")
    print(f"  Messages before: {n_before}")
    print(f"  Estimated tokens before: {estimated_tokens_before:.0f} "
          f"({estimated_tokens_before / 200:.0%} of window)")
    print(f"  Messages after eviction: {len(messages)}")
    print(f"  Estimated tokens after:  {estimated_tokens_after:.0f} "
          f"({estimated_tokens_after / 200:.0%} of window)")
    print(f"  System message preserved: {messages[0].role == 'system'}")
    print(f"  Hook skip=False (eviction, not skip): {not skip}")
    print()

    print("  Usage:")
    print("    agent = Agent(")
    print("        tool_config=ToolConfig(context_overflow_threshold=0.8),")
    print("        ...)")
    print("  Set context_overflow_threshold=0.0 to disable (default).")
    print("  Recommended: 0.8 for production (trigger at 80% of context window).")


# ---------------------------------------------------------------------------
# Demo 3: Combined — both enabled together
# ---------------------------------------------------------------------------

def demo_combined():
    print("=" * 60)
    print("Demo 3: Both hooks combined")
    print("=" * 60)
    print("""
    A 'deep agent' equivalent: context overflow + repetition detection
    enabled together. No Agent subclass needed.
    """)

    agent = Agent(
        name="deep-agent",
        model=OpenAIChat(id="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY", "fake")),
        tool_config=ToolConfig(
            context_overflow_threshold=0.8,   # evict at 80% context
            max_repeated_tool_calls=3,         # break loops at 3× same call
            tool_call_limit=50,                # hard stop at 50 total calls
        ),
    )

    print(f"  tool_config.context_overflow_threshold = {agent.tool_config.context_overflow_threshold}")
    print(f"  tool_config.max_repeated_tool_calls    = {agent.tool_config.max_repeated_tool_calls}")
    print(f"  tool_config.tool_call_limit            = {agent.tool_config.tool_call_limit}")
    print()
    print("  Old pattern (DeepAgent subclass):")
    print("    from agentica import DeepAgent")
    print("    agent = DeepAgent(model=model, work_dir='/path')")
    print()
    print("  New pattern (ToolConfig fields on Agent):")
    print("    from agentica import Agent")
    print("    from agentica.tools.buildin_tools import get_builtin_tools")
    print("    agent = Agent(")
    print("        model=model,")
    print("        tools=get_builtin_tools(work_dir='/path'),")
    print("        prompt_config=PromptConfig(enable_agentic_prompt=True),")
    print("        tool_config=ToolConfig(")
    print("            context_overflow_threshold=0.8,")
    print("            max_repeated_tool_calls=3,")
    print("        ),")
    print("    )")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Agentica: Model-layer hooks (context overflow + repetition detection)\n")
    demo_repetition_detection()
    demo_context_overflow()
    demo_combined()
    print("=" * 60)
    print("Summary: Enable deep-agent capabilities via ToolConfig fields.")
    print("No subclassing needed. Both hooks are disabled by default (zero overhead).")
    print("=" * 60)
