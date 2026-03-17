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
from typing import Any, Optional, List, Dict

from agentica.utils.log import logger


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
        agent_id = getattr(agent, 'agent_id', 'unknown')
        run_input = getattr(agent, 'run_input', None)
        self._run_inputs[agent_id] = run_input if isinstance(run_input, str) else None

    async def on_agent_end(self, agent: Any, output: Any, **kwargs) -> None:
        """Archive conversation after agent completes."""
        workspace = getattr(agent, 'workspace', None)
        if workspace is None:
            return

        agent_id = getattr(agent, 'agent_id', 'unknown')
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
            session_id = getattr(agent, 'run_id', None)
            await workspace.archive_conversation(messages_to_archive, session_id=session_id)
            logger.debug(f"Archived conversation for agent {agent_id}")
        except Exception as e:
            logger.warning(f"Failed to archive conversation: {e}")


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
