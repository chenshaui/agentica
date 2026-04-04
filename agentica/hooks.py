# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Lifecycle hooks for Agent runs.

Two levels of hooks:
- AgentHooks: per-agent hooks (on_start, on_end), set on Agent instance
- RunHooks: global run-level hooks (on_agent_start, on_agent_end, on_llm_start,
  on_llm_end, on_tool_start, on_tool_end, on_agent_transfer), passed to run()
- ConversationArchiveHooks: auto-archives conversations to workspace after each run
"""
import json
from typing import Any, Optional, List, Dict

from agentica.model.message import Message
from agentica.utils.log import logger

# ─── Shared memory type specification ───────────────────────────────────────
# Used by both BuiltinMemoryTool (LLM-facing system prompt) and
# MemoryExtractHooks (extraction sub-call). Keep in sync by importing
# from this single source.

MEMORY_TYPE_SPEC = (
    "**user** — User's role, goals, preferences, and knowledge.\n"
    "  When to save: when you learn details about the user's role, preferences, "
    "responsibilities, or knowledge.\n"
    "  Example: 'User is a data scientist focused on observability/logging.'\n\n"
    "**feedback** — Guidance on how to approach work: what to avoid AND what "
    "to keep doing.\n"
    "  When to save: any time the user corrects an approach ('don't do X') OR "
    "confirms a non-obvious approach worked ('yes exactly', 'perfect'). "
    "Corrections are easy to notice; confirmations are quieter — watch for them.\n"
    "  Body structure: lead with the rule, then **Why:** (the reason), then "
    "**How to apply:** (when/where this kicks in).\n"
    "  Example: 'Integration tests must use real DB. Why: mock/prod divergence "
    "masked a broken migration. How to apply: tests/integration/.'\n\n"
    "**project** — Information about ongoing work, goals, bugs, or incidents "
    "NOT derivable from code or git history.\n"
    "  When to save: when you learn who is doing what, why, or by when. "
    "Convert relative dates to absolute dates.\n\n"
    "**reference** — Pointers to external resources: issue trackers, dashboards, "
    "wikis, documentation sites, or internal tools.\n"
    "  When to save: when you learn about an external system the team uses.\n"
)

MEMORY_EXCLUSION_SPEC = (
    "- Code patterns, conventions, architecture, file paths, or project structure "
    "— derivable by reading the codebase.\n"
    "- Git history, recent changes, or who-changed-what — `git log`/`git blame` "
    "are authoritative.\n"
    "- Debugging solutions or fix recipes — the fix is in the code.\n"
    "- Anything already documented in AGENT.md files.\n"
    "- Ephemeral task details: in-progress work, temporary state, current "
    "conversation context.\n"
    "- Activity logs, PR lists, or task summaries — only the *surprising* or "
    "*non-obvious* part is worth keeping.\n"
)


class AgentHooks:
    """Per-agent lifecycle hooks.

    Subclass and override the methods you need. Attach to an Agent via
    ``Agent(hooks=MyHooks())``.

    Example::

        class LoggingHooks(AgentHooks):
            async def on_start(self, agent, **kwargs):
                print(f"{agent.name} starting")

            async def on_end(self, agent, output, **kwargs):
                print(f"{agent.name} produced: {output}")
    """

    async def on_start(self, agent: Any, **kwargs) -> None:
        """Called when this agent begins a run."""
        pass

    async def on_end(self, agent: Any, output: Any, **kwargs) -> None:
        """Called when this agent finishes a run."""
        pass


class RunHooks:
    """Global run-level lifecycle hooks.

    These hooks observe the entire run, including LLM calls, tool calls,
    and agent transfers. Pass to ``agent.run(hooks=MyRunHooks())``.

    Example::

        class MetricsHooks(RunHooks):
            def __init__(self):
                self.event_counter = 0

            async def on_agent_start(self, agent, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: Agent {agent.name} started")

            async def on_llm_start(self, agent, messages, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: LLM call started")

            async def on_llm_end(self, agent, response, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: LLM call ended")

            async def on_tool_start(self, agent, tool_name, tool_call_id, tool_args, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: Tool {tool_name} started")

            async def on_tool_end(self, agent, tool_name, tool_call_id, tool_args, result, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: Tool {tool_name} ended")

            async def on_agent_transfer(self, from_agent, to_agent, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: Transfer from {from_agent.name} to {to_agent.name}")

            async def on_agent_end(self, agent, output, **kwargs):
                self.event_counter += 1
                print(f"#{self.event_counter}: Agent {agent.name} ended")
    """

    async def on_agent_start(self, agent: Any, **kwargs) -> None:
        """Called when any agent begins execution within this run."""
        pass

    async def on_agent_end(self, agent: Any, output: Any, **kwargs) -> None:
        """Called when any agent finishes execution within this run."""
        pass

    async def on_llm_start(
        self,
        agent: Any,
        messages: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> None:
        """Called before each LLM API call."""
        pass

    async def on_llm_end(
        self,
        agent: Any,
        response: Any = None,
        **kwargs,
    ) -> None:
        """Called after each LLM API call returns."""
        pass

    async def on_tool_start(
        self,
        agent: Any,
        tool_name: str = "",
        tool_call_id: str = "",
        tool_args: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> None:
        """Called before a tool begins execution."""
        pass

    async def on_tool_end(
        self,
        agent: Any,
        tool_name: str = "",
        tool_call_id: str = "",
        tool_args: Optional[Dict[str, Any]] = None,
        result: Any = None,
        is_error: bool = False,
        elapsed: float = 0.0,
        **kwargs,
    ) -> None:
        """Called after a tool finishes execution."""
        pass

    async def on_agent_transfer(
        self,
        from_agent: Any,
        to_agent: Any,
        **kwargs,
    ) -> None:
        """Called when a task is transferred from one agent to another."""
        pass

    async def on_user_prompt(
        self,
        agent: Any,
        message: str,
        **kwargs,
    ) -> Optional[str]:
        """Called before a user prompt is processed.

        Return a modified message string to replace the original, or None to
        keep it unchanged.  Mirrors CC's UserPromptSubmit hook.
        """
        return None

    async def on_pre_compact(
        self,
        agent: Any,
        messages: Optional[List] = None,
        **kwargs,
    ) -> None:
        """Called just before context compression is triggered.

        Use for: saving state, logging, custom archival before messages are
        compressed/dropped.  Mirrors CC's PreCompact hook.
        """
        pass

    async def on_post_compact(
        self,
        agent: Any,
        messages: Optional[List] = None,
        **kwargs,
    ) -> None:
        """Called right after context compression completes.

        ``messages`` is the compressed result (may be much shorter than before).
        Use for: post-compression analytics, re-injecting critical context.
        Mirrors CC's PostCompact hook.
        """
        pass


class ConversationArchiveHooks(RunHooks):
    """RunHooks that auto-archives conversations to workspace after each agent run.

    Captures user input and agent output from each run and appends them to
    the daily conversation archive in the workspace.

    Usage::

        from agentica.hooks import ConversationArchiveHooks

        hooks = ConversationArchiveHooks()
        response = await agent.run("Hello", config=RunConfig(hooks=hooks))
    """

    def __init__(self):
        self._run_inputs: Dict[str, Optional[str]] = {}  # agent_id -> captured run_input

    async def on_agent_start(self, agent: Any, **kwargs) -> None:
        """Capture run_input at start time for reliable access in on_agent_end."""
        agent_id = agent.agent_id
        run_input = agent.run_input
        self._run_inputs[agent_id] = run_input if isinstance(run_input, str) else None

    async def on_agent_end(self, agent: Any, output: Any, **kwargs) -> None:
        """Archive conversation after agent completes."""
        workspace = agent.workspace
        if workspace is None:
            return

        agent_id = agent.agent_id
        messages_to_archive = []

        # Use run_input captured at start time
        run_input = self._run_inputs.pop(agent_id, None)
        if run_input:
            messages_to_archive.append({"role": "user", "content": run_input})

        # Collect agent output
        if output and isinstance(output, str):
            messages_to_archive.append({"role": "assistant", "content": output})

        if not messages_to_archive:
            return

        try:
            session_id = agent.run_id
            filepath = await workspace.archive_conversation(messages_to_archive, session_id=session_id)
            logger.debug(f"Conversation saved to {filepath}")
        except Exception as e:
            logger.warning(f"Failed to archive conversation: {e}")


class MemoryExtractHooks(RunHooks):
    """RunHooks that auto-extracts memories from conversations after each agent run.

    After each run, checks if the LLM already called save_memory during the conversation.
    If not, uses the LLM to extract key information worth remembering and saves it
    to the workspace memory directory.

    This mirrors Claude Code's extractMemories service: a background process that
    fires after each conversation to capture important information the main agent
    didn't explicitly save.

    Usage::

        from agentica.hooks import MemoryExtractHooks

        hooks = MemoryExtractHooks()
        response = await agent.run("Hello", config=RunConfig(hooks=hooks))
    """

    # Prompt for the memory extraction sub-call.
    # Uses shared MEMORY_TYPE_SPEC / MEMORY_EXCLUSION_SPEC constants
    # (same source as BuiltinMemoryTool.MEMORY_SYSTEM_PROMPT).
    _EXTRACT_PROMPT = (
        "You are a memory extraction assistant. Review the conversation below and "
        "extract key information worth remembering for future sessions.\n\n"
        "Memories capture context NOT derivable from the current project state. "
        "Code patterns, architecture, git history, and file structure are derivable "
        "(via grep/git/AGENT.md) and must NOT be saved as memories.\n\n"
        "## Memory types\n\n"
        + MEMORY_TYPE_SPEC +
        "\n## What NOT to save\n\n"
        + MEMORY_EXCLUSION_SPEC +
        "\n## Output format\n\n"
        "For each memory, output a JSON object with fields:\n"
        '  {"title": "short_name", "content": "what to remember (include Why + '
        'How to apply for feedback type)", "type": "user|feedback|project|reference"}\n\n'
        "Output a JSON array of memories. If nothing worth remembering, output: []\n\n"
        "Conversation:\n"
    )

    def __init__(self):
        self._run_inputs: Dict[str, Optional[str]] = {}
        self._tool_calls: Dict[str, List[str]] = {}  # agent_id -> list of tool names called

    async def on_agent_start(self, agent: Any, **kwargs) -> None:
        agent_id = agent.agent_id
        self._run_inputs[agent_id] = agent.run_input if isinstance(agent.run_input, str) else None
        self._tool_calls[agent_id] = []

    async def on_tool_end(self, agent: Any, tool_name: str = "", **kwargs) -> None:
        """Track tool calls to detect if save_memory was already used."""
        agent_id = agent.agent_id
        if agent_id not in self._tool_calls:
            self._tool_calls[agent_id] = []
        self._tool_calls[agent_id].append(tool_name)

    async def on_agent_end(self, agent: Any, output: Any, **kwargs) -> None:
        """Extract and save memories if LLM didn't use save_memory during this run."""
        workspace = agent.workspace
        if workspace is None:
            return

        agent_id = agent.agent_id
        run_input = self._run_inputs.pop(agent_id, None)
        tool_calls = self._tool_calls.pop(agent_id, [])

        # If LLM already called save_memory, skip extraction (CC's hasMemoryWritesSince)
        if "save_memory" in tool_calls:
            logger.debug("Skipping memory extraction: save_memory was called during this run")
            return

        # Build conversation text for extraction
        if not run_input and not output:
            return

        conversation_text = ""
        if run_input:
            conversation_text += f"User: {run_input}\n\n"
        if output and isinstance(output, str):
            conversation_text += f"Assistant: {output}\n"

        # Skip very short conversations (nothing to extract)
        if len(conversation_text) < 50:
            return

        # Use the agent's model to extract memories
        model = agent.model
        if model is None:
            return

        # Await directly — asyncio.create_task() would be silently cancelled
        # in run_stream_sync()/run_sync() scenarios where the event loop exits
        # immediately after the stream is consumed. Direct await ensures the
        # extraction completes before on_agent_end returns.
        await self._extract_and_save(model, workspace, conversation_text)

    async def _extract_and_save(self, model: Any, workspace: Any, conversation_text: str) -> None:
        """Run the LLM extraction call and persist results."""
        extract_messages = [
            Message(role="user", content=self._EXTRACT_PROMPT + conversation_text),
        ]

        try:
            model_response = await model.response(extract_messages)
            if not model_response or not model_response.content:
                return

            # Parse JSON array from response
            text = model_response.content.strip()
            # Handle markdown code blocks
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            memories = json.loads(text)
            if not isinstance(memories, list) or not memories:
                return

            for mem in memories:
                if not isinstance(mem, dict):
                    continue
                title = mem.get("title", "").strip()
                content = mem.get("content", "").strip()
                mem_type = mem.get("type", "project").strip()
                if not title or not content:
                    continue
                if mem_type not in ("user", "feedback", "project", "reference"):
                    mem_type = "project"

                await workspace.write_memory_entry(
                    title=title,
                    content=content,
                    memory_type=mem_type,
                    description=title,
                )
                logger.debug(f"Auto-extracted memory: {title} (type: {mem_type})")

        except json.JSONDecodeError as e:
            logger.debug(f"Memory extraction: LLM returned invalid JSON: {e}")
        except Exception as e:
            logger.warning(f"Memory extraction failed: {e}")


class _CompositeRunHooks(RunHooks):
    """Internal wrapper that dispatches to multiple RunHooks instances.

    Used to combine auto-injected hooks (e.g. ConversationArchiveHooks)
    with user-provided hooks without requiring users to manage composition.
    """

    def __init__(self, hooks_list: List[RunHooks]):
        self._hooks_list = hooks_list

    async def on_agent_start(self, agent: Any, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_agent_start(agent=agent, **kwargs)

    async def on_agent_end(self, agent: Any, output: Any, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_agent_end(agent=agent, output=output, **kwargs)

    async def on_llm_start(self, agent: Any, messages: Optional[List[Dict[str, Any]]] = None, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_llm_start(agent=agent, messages=messages, **kwargs)

    async def on_llm_end(self, agent: Any, response: Any = None, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_llm_end(agent=agent, response=response, **kwargs)

    async def on_tool_start(self, agent: Any, tool_name: str = "", tool_call_id: str = "",
                            tool_args: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_tool_start(agent=agent, tool_name=tool_name, tool_call_id=tool_call_id,
                                  tool_args=tool_args, **kwargs)

    async def on_tool_end(self, agent: Any, tool_name: str = "", tool_call_id: str = "",
                          tool_args: Optional[Dict[str, Any]] = None, result: Any = None,
                          is_error: bool = False, elapsed: float = 0.0, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_tool_end(agent=agent, tool_name=tool_name, tool_call_id=tool_call_id,
                                tool_args=tool_args, result=result, is_error=is_error,
                                elapsed=elapsed, **kwargs)

    async def on_agent_transfer(self, from_agent: Any, to_agent: Any, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_agent_transfer(from_agent=from_agent, to_agent=to_agent, **kwargs)

    async def on_user_prompt(self, agent: Any, message: str, **kwargs) -> Optional[str]:
        result = None
        for h in self._hooks_list:
            r = await h.on_user_prompt(agent=agent, message=message, **kwargs)
            if r is not None:
                result = r
                message = r  # chain: next hook sees the modified message
        return result

    async def on_pre_compact(self, agent: Any, messages=None, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_pre_compact(agent=agent, messages=messages, **kwargs)

    async def on_post_compact(self, agent: Any, messages=None, **kwargs) -> None:
        for h in self._hooks_list:
            await h.on_post_compact(agent=agent, messages=messages, **kwargs)
