# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Team collaboration, transfer, and inter-agent communication.

Features:
- P0: clone() per call to prevent concurrent state corruption
- P1a: when_to_use for improved LLM routing accuracy
- P1b: background=True for async fire-and-forget subagents + notification queue
- P2: subagent_launched/progress/completed RunEvent streaming
- P3: parent_messages dynamically captured at call time (not snapshot)
- P4: MessageBus for peer-to-peer inter-agent communication
- P5: Structured result passing (tool_calls, reasoning, usage)
"""

import asyncio
import json
import threading
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Union,
)

from agentica.utils.log import logger
from agentica.tools.base import ModelTool, Tool, Function


# ============================================================================
# Structured result serializer
# ============================================================================

def _serialize_result(response: Any) -> str:
    """Serialize RunResponse with structured info (tools, reasoning, usage).

    Preserves tool_calls, reasoning_content, and usage alongside content,
    so the parent agent can make informed decisions.
    """
    if response is None:
        return "No response from agent."

    # Build a rich result dict
    result: Dict[str, Any] = {}

    # Main content
    content = getattr(response, 'content', None)
    if content is not None:
        result["content"] = _serialize_content(content)
    else:
        result["content"] = ""

    # Reasoning chain (if available)
    reasoning = getattr(response, 'reasoning_content', None)
    if reasoning:
        result["reasoning"] = reasoning

    # Tool calls summary (structured, not just a string)
    tools = getattr(response, 'tools', None)
    if tools:
        result["tool_calls"] = [
            {
                "tool_name": t.get("tool_name", ""),
                "tool_args": t.get("tool_args", {}),
                "result_preview": str(t.get("content", ""))[:500],
                "is_error": t.get("tool_call_error", False),
            }
            for t in tools
        ]

    # Usage info
    usage = getattr(response, 'usage', None)
    if usage:
        result["usage"] = {
            "input_tokens": getattr(usage, 'input_tokens', 0),
            "output_tokens": getattr(usage, 'output_tokens', 0),
        }

    # If only content, return it directly for simplicity
    if len(result) == 1 and "content" in result:
        return result["content"]

    return json.dumps(result, ensure_ascii=False, default=str)


def _serialize_content(content: Any) -> str:
    """Serialize agent response content to JSON string.

    Handles Pydantic models, lists of Pydantic models, dicts, and plain values.
    """
    if isinstance(content, str):
        return content

    # Pydantic model
    if hasattr(content, 'model_dump'):
        return json.dumps(content.model_dump(), ensure_ascii=False, default=str)

    # List / tuple -- items may be Pydantic models
    if isinstance(content, (list, tuple)):
        items = [i.model_dump() if hasattr(i, 'model_dump') else i for i in content]
        return json.dumps(items, ensure_ascii=False, default=str)

    return json.dumps(content, ensure_ascii=False, default=str)


# ============================================================================
# Async Agent Registry -- tracks background subagent tasks + notification queue
# ============================================================================

class AsyncAgentRegistry:
    """Registry for tracking async (background) subagent tasks.

    Thread-safe singleton. Includes a notification queue so parent agents
    can discover completed background tasks.
    """

    _instance: Optional["AsyncAgentRegistry"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, Dict[str, Any]] = {}
        # Notification queue: list of {agent_id, status, content/error}
        # drained by parent agents at the start of each turn.
        self._pending_notifications: List[Dict[str, Any]] = []

    @classmethod
    def get_instance(cls) -> "AsyncAgentRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register(self, agent_id: str, task: asyncio.Task) -> None:
        self._tasks[agent_id] = task

    def set_result(self, agent_id: str, result: Dict[str, Any]) -> None:
        self._results[agent_id] = result
        self._tasks.pop(agent_id, None)
        # Queue a notification for the parent
        self._pending_notifications.append({
            "agent_id": agent_id,
            **result,
        })

    def get_result(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._results.get(agent_id)

    def is_running(self, agent_id: str) -> bool:
        task = self._tasks.get(agent_id)
        return task is not None and not task.done()

    def get_status(self, agent_id: str) -> str:
        if agent_id in self._results:
            return self._results[agent_id].get("status", "completed")
        if self.is_running(agent_id):
            return "running"
        return "unknown"

    def list_all(self) -> Dict[str, str]:
        """Return {agent_id: status} for all tracked agents."""
        all_ids = set(self._tasks.keys()) | set(self._results.keys())
        return {aid: self.get_status(aid) for aid in all_ids}

    def drain_notifications(self) -> List[Dict[str, Any]]:
        """Drain and return all pending notifications. Thread-safe."""
        if not self._pending_notifications:
            return []
        notifications = self._pending_notifications[:]
        self._pending_notifications.clear()
        return notifications

    def cleanup_completed(self) -> int:
        """Remove completed results. Returns count of cleaned entries."""
        completed = [aid for aid, r in self._results.items() if r.get("status") == "completed"]
        for aid in completed:
            del self._results[aid]
        return len(completed)


# ============================================================================
# get_agent_result tool function (for LLM to query background agent status)
# ============================================================================

async def get_agent_result(agent_id: str) -> str:
    """Check the status/result of a background agent.

    Args:
        agent_id: The agent_id returned by async_launched response.

    Returns:
        JSON string with status and content/error.
    """
    registry = AsyncAgentRegistry.get_instance()
    status = registry.get_status(agent_id)
    result = registry.get_result(agent_id)

    if result is not None:
        return json.dumps(result, ensure_ascii=False, default=str)
    elif status == "running":
        return json.dumps({"status": "running", "message": "Agent is still running."})
    else:
        return json.dumps({"status": "unknown", "message": f"No agent found with id '{agent_id}'."})


# ============================================================================
# MessageBus -- peer-to-peer inter-agent communication
# ============================================================================

class MessageBus:
    """Simple in-process message bus for inter-agent communication.

    Agents send messages by name. Recipients check their inbox at turn start.
    Thread-safe singleton.
    """

    _instance: Optional["MessageBus"] = None
    _lock = threading.Lock()

    def __init__(self):
        # {recipient_name: [{from, message, timestamp}, ...]}
        self._mailboxes: Dict[str, List[Dict[str, Any]]] = {}

    @classmethod
    def get_instance(cls) -> "MessageBus":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def send(self, from_name: str, to_name: str, message: str) -> None:
        """Send a message from one agent to another."""
        import time
        if to_name not in self._mailboxes:
            self._mailboxes[to_name] = []
        self._mailboxes[to_name].append({
            "from": from_name,
            "message": message,
            "timestamp": time.time(),
        })
        logger.debug(f"MessageBus: {from_name} -> {to_name}: {message[:100]}")

    def check_messages(self, recipient_name: str) -> List[Dict[str, Any]]:
        """Drain and return all messages for a recipient."""
        msgs = self._mailboxes.pop(recipient_name, [])
        return msgs

    def has_messages(self, recipient_name: str) -> bool:
        return bool(self._mailboxes.get(recipient_name))

    def broadcast(self, from_name: str, message: str, exclude: Optional[List[str]] = None) -> None:
        """Broadcast a message to all known recipients (except sender and excluded)."""
        exclude_set = set(exclude or [])
        exclude_set.add(from_name)
        for name in list(self._mailboxes.keys()):
            if name not in exclude_set:
                self.send(from_name, name, message)


# ============================================================================
# send_message / check_messages tool functions (for LLM to use)
# ============================================================================

async def send_message_tool(from_agent_name: str, to_agent_name: str, message: str) -> str:
    """Send a message to another agent on the team.

    Args:
        from_agent_name: Your agent name (the sender).
        to_agent_name: The recipient agent name.
        message: The message content to send.

    Returns:
        Confirmation string.
    """
    bus = MessageBus.get_instance()
    bus.send(from_agent_name, to_agent_name, message)
    return json.dumps({"status": "sent", "to": to_agent_name})


async def check_messages_tool(agent_name: str) -> str:
    """Check your inbox for messages from other agents.

    Args:
        agent_name: Your agent name.

    Returns:
        JSON list of messages, or empty list if none.
    """
    bus = MessageBus.get_instance()
    msgs = bus.check_messages(agent_name)
    if not msgs:
        return json.dumps({"messages": [], "count": 0})
    return json.dumps({"messages": msgs, "count": len(msgs)}, ensure_ascii=False, default=str)


# ============================================================================
# TeamMixin
# ============================================================================

class TeamMixin:
    """Mixin class containing team and tool methods for Agent."""

    def as_tool(
        self,
        tool_name: Optional[str] = None,
        tool_description: Optional[str] = None,
        custom_output_extractor: Optional[Callable] = None,
        background: bool = False,
    ) -> Function:
        """Convert this Agent to a Function that can be used by other agents.

        Args:
            tool_name: The name of the tool. Defaults to snake_case of agent name or 'agent_{id}'.
            tool_description: The tool description. Defaults to when_to_use or description.
            custom_output_extractor: Optional function to extract output from RunResponse.
            background: If True, run the agent asynchronously (fire-and-forget).
                Returns immediately with agent_id; result can be fetched later.

        Returns:
            A Function instance that wraps this agent.
        """
        # Generate tool name
        if tool_name:
            name = tool_name
        elif self.name:
            name = self.name.lower().replace(' ', '_').replace('-', '_')
        else:
            name = f"agent_{self.agent_id[:8]}"

        # Generate description: prefer when_to_use > tool_description > description > role
        description = (
            tool_description
            or getattr(self, 'when_to_use', None)
            or self.description
            or self.prompt_config.role
            or f"Run the {name} agent."
        )

        # Capture reference for closures
        agent_self = self

        if background:
            # P1b: Async fire-and-forget mode
            async def agent_entrypoint_bg(message: str) -> str:
                """Launch the agent in background and return immediately with agent_id."""
                clone = agent_self.clone()
                registry = AsyncAgentRegistry.get_instance()

                # P3: dynamically capture parent context at call time
                parent_msgs = _get_parent_messages(agent_self)

                async def _run_bg():
                    try:
                        response = await clone.run(
                            message,
                            add_messages=parent_msgs,
                        )
                        content = "No response from agent."
                        if custom_output_extractor:
                            content = custom_output_extractor(response)
                        elif response:
                            content = _serialize_result(response)
                        registry.set_result(clone.agent_id, {
                            "status": "completed",
                            "agent_name": clone.identifier,
                            "content": content,
                        })
                    except Exception as e:
                        logger.error(f"Background agent '{clone.identifier}' failed: {e}")
                        registry.set_result(clone.agent_id, {
                            "status": "failed",
                            "agent_name": clone.identifier,
                            "error": str(e),
                        })

                task = asyncio.create_task(_run_bg())
                registry.register(clone.agent_id, task)
                logger.info(f"Launched background agent '{clone.identifier}' (id={clone.agent_id})")
                return json.dumps({
                    "status": "async_launched",
                    "agent_id": clone.agent_id,
                    "agent_name": clone.identifier,
                    "message": f"Agent '{clone.identifier}' is running in background. "
                               f"Use get_agent_result(agent_id='{clone.agent_id}') to check status.",
                })

            return Function(
                name=name,
                description=f"[async] {description}",
                entrypoint=agent_entrypoint_bg,
            )

        # Synchronous (blocking) mode -- default
        async def agent_entrypoint(message: str) -> str:
            """Run the agent with the given message and return the response."""
            # P0: clone to prevent concurrent state corruption
            clone = agent_self.clone()

            # P3: dynamically capture parent context at call time
            parent_msgs = _get_parent_messages(agent_self)

            response = None
            full_content = ""

            try:
                async for chunk in clone.run_stream(
                    message,
                    add_messages=parent_msgs,
                ):
                    response = chunk
                    if chunk.content:
                        full_content = chunk.content
            except Exception:
                # Fallback to non-streaming if streaming fails
                response = await clone.run(
                    message,
                    add_messages=parent_msgs,
                )

            if custom_output_extractor and response:
                return custom_output_extractor(response)

            # P5: structured result with tool_calls, reasoning, usage
            if response and response.content:
                return _serialize_result(response)

            return full_content if full_content else "No response from agent."

        return Function(
            name=name,
            description=description,
            entrypoint=agent_entrypoint,
        )

    def get_transfer_function(self) -> Function:
        """Get a function to transfer tasks to this agent.

        Returns:
            A Function instance that can transfer tasks to this agent.
        """
        agent_name = self.name or "agent"
        # P1a: Prefer when_to_use for description
        agent_description = (
            getattr(self, 'when_to_use', None)
            or self.description
            or self.prompt_config.role
            or f"Transfer task to {agent_name}"
        )

        # Capture reference
        agent_self = self

        async def transfer_to_agent(task: str) -> str:
            """Transfer a task to this agent.

            Args:
                task: The task description to transfer.

            Returns:
                The response from the agent.
            """
            # --- Lifecycle: agent transfer ---
            caller = getattr(agent_self, '_transfer_caller', None)
            if caller is not None and hasattr(caller, '_run_hooks') and caller._run_hooks is not None:
                await caller._run_hooks.on_agent_transfer(from_agent=caller, to_agent=agent_self)

            logger.info(f"Transferring task to {agent_name}: {task}")

            # P0: clone to prevent concurrent state corruption
            clone = agent_self.clone()

            # P3: dynamically capture parent context at call time
            parent_msgs = _get_parent_messages(agent_self)

            response = await clone.run(
                message=task,
                add_messages=parent_msgs,
            )

            # P5: structured result
            if response:
                return _serialize_result(response)
            return "No response from agent"

        return Function(
            name=f"transfer_to_{agent_name.lower().replace(' ', '_')}",
            description=agent_description,
            entrypoint=transfer_to_agent,
        )

    def get_transfer_prompt(self) -> str:
        """Get prompt for transferring tasks to team members.

        Returns:
            A string with instructions for task transfer.
        """
        if not self.has_team():
            return ""

        transfer_prompt = "\n## Task Transfer\n"
        transfer_prompt += "You can transfer tasks to the following team members:\n"

        for member in self.team:
            member_name = member.name or "unnamed_agent"
            # P1a: use when_to_use for better LLM routing
            member_desc = (
                getattr(member, 'when_to_use', None)
                or member.prompt_config.role
                or member.description
                or "No description"
            )
            transfer_prompt += f"- **{member_name}**: {member_desc}\n"

        transfer_prompt += "\nUse the appropriate transfer function to delegate tasks to team members.\n"

        # P4: Messaging instructions if team has names
        named_members = [m.name for m in self.team if m.name]
        if named_members:
            transfer_prompt += (
                "\n## Inter-Agent Communication\n"
                "You can send messages to and check messages from team members:\n"
                f"- Team members: {', '.join(named_members)}\n"
                "- Use `send_message` to communicate with a specific team member.\n"
                "- Use `check_messages` to read messages sent to you.\n"
            )

        return transfer_prompt

    def get_tools(self) -> Optional[List[Union[ModelTool, Tool, Callable, Dict, Function]]]:
        """Get all tools available to this agent.

        This includes:
        - User-provided tools
        - Default tools (chat history, knowledge base search, etc.)
        - Team transfer functions (if enabled)
        - Messaging tools (if team is present)
        - get_agent_result tool (if background agents may be used)

        Returns:
            A list of tools, or None if no tools are available.
        """
        tools: List[Union[ModelTool, Tool, Callable, Dict, Function]] = []

        # Add user-provided tools
        if self.tools is not None:
            tools.extend(self.tools)

        # Add default tools based on settings
        if self.tool_config.read_chat_history:
            tools.append(self.get_chat_history)

        if self.tool_config.read_tool_call_history:
            tools.append(self.get_tool_call_history)

        # Add knowledge base tools if knowledge is configured
        if self.knowledge is not None:
            if self.tool_config.search_knowledge:
                tools.append(self.search_knowledge_base)

            if self.tool_config.update_knowledge:
                tools.append(self.add_to_knowledge)

        # Add team transfer functions if team is present and transfer instructions are enabled
        if self.has_team() and self.team_config.add_transfer_instructions:
            for member in self.team:
                # Set caller reference for lifecycle hooks
                member._transfer_caller = self
                transfer_func = member.get_transfer_function()
                tools.append(transfer_func)

            # P4: Add messaging tools for inter-agent communication
            agent_name = self.name or self.agent_id[:8]
            team_member_names = [m.name or "unnamed" for m in self.team]

            # Bind send_message with this agent's name as sender
            async def _send_message(to_agent_name: str, message: str) -> str:
                """Send a message to a team member.

                Args:
                    to_agent_name: Name of the team member to message.
                    message: The message content.

                Returns:
                    Confirmation.
                """
                return await send_message_tool(agent_name, to_agent_name, message)

            async def _check_messages() -> str:
                """Check your inbox for messages from team members.

                Returns:
                    JSON list of messages.
                """
                return await check_messages_tool(agent_name)

            tools.append(Function(
                name="send_message",
                description=f"Send a message to a team member. Available members: {', '.join(team_member_names)}",
                entrypoint=_send_message,
            ))
            tools.append(Function(
                name="check_messages",
                description="Check your inbox for messages from other team members.",
                entrypoint=_check_messages,
            ))

            # Add get_agent_result tool for querying background agent status
            tools.append(Function(
                name="get_agent_result",
                description="Check the status or result of a background agent by its agent_id.",
                entrypoint=get_agent_result,
            ))

        return tools if len(tools) > 0 else []


# ============================================================================
# Helper: dynamic parent message capture
# ============================================================================

def _get_parent_messages(agent_self) -> Optional[List]:
    """Dynamically capture parent context at call time (not at closure creation).

    Returns the last N messages from the parent agent's working memory,
    or None if share_parent_context is disabled.
    """
    if not getattr(agent_self, 'team_config', None):
        return None
    if not agent_self.team_config.share_parent_context:
        return None
    window = agent_self.team_config.parent_context_window
    recent = agent_self.working_memory.messages[-window:] if agent_self.working_memory.messages else []
    return recent if recent else None
