# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: BuiltinTaskTool - subagent spawner for complex multi-step tasks.

Extracted from buildin_tools.py for maintainability.
"""
import asyncio
import json
import time
import uuid
from datetime import datetime
from textwrap import dedent
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from agentica.tools.base import Tool
from agentica.utils.log import logger

if TYPE_CHECKING:
    from agentica.agent import Agent
    from agentica.model.base import Model


class BuiltinTaskTool(Tool):
    """
    Built-in task tool for launching subagents to handle complex, multi-step tasks.

    This tool allows the main agent to delegate complex tasks to ephemeral subagents
    that can work independently with isolated context windows.

    Supports multiple subagent types:
    - explore: Read-only codebase exploration (fastest, lowest context)
    - research: Web search and document analysis
    - code: Code generation and execution (full capabilities)
    - Custom user-defined subagent types (via register_custom_subagent)

    Key Features:
    - Parallel execution: Launch multiple subagents simultaneously for independent tasks
    - Isolated context: Each subagent has its own context window
    - Type-specific tools: Each subagent type has optimized tool sets
    """

    # Base system prompt template for task tool usage guidance
    TASK_SYSTEM_PROMPT_TEMPLATE = dedent("""## task Tool (Subagent Spawner)

    Launch a subagent to handle complex, multi-step tasks autonomously.
    Each subagent runs in its own isolated context window and returns a single result.

    ### Available Subagent Types

    {subagent_table}

    ### Writing the Description (IMPORTANT)

    Brief the subagent like a smart colleague who just walked into the room — it hasn't seen
    this conversation, doesn't know what you've tried, doesn't understand why this task matters.

    - Explain what you're trying to accomplish and why
    - Describe what you've already learned or ruled out
    - Give enough context about the surrounding problem
    - If you need a short response, say so ("report in under 200 words")

    **Never delegate understanding.** Don't write "based on your findings, fix the bug" or
    "based on the research, implement it." Those phrases push synthesis onto the subagent
    instead of doing it yourself. You should synthesize the subagent's result yourself.

    ### Don't Peek, Don't Race

    After launching a subagent, you know nothing about what it found until it returns.
    - Do NOT fabricate or predict subagent results
    - Trust the returned output — the subagent's results should generally be trusted
    - Do NOT re-read files the subagent already examined unless you need to verify something specific

    ### Parallel Execution

    When you have **multiple independent tasks**, launch them in parallel:
    - Tasks execute simultaneously — total time = max(task_times), not sum(task_times)
    - Ideal for: exploring multiple directories, researching multiple topics, running multiple experiments

    ### When to Use

    - Research: open-ended exploration across many files or directories
    - Implementation: work that requires more than a couple of edits
    - Multi-part tasks: independent subtasks that can run in parallel

    ### When NOT to Use

    - Task is trivial (1-3 tool calls) — just do it directly
    - You need to see intermediate steps
    - Task depends heavily on the main conversation context
    - Reading a specific known file — use read_file instead
    - Searching for a specific definition — use grep/glob instead""")

    def __init__(
            self,
            model: Optional["Model"] = None,
            tools: Optional[List[Any]] = None,
            work_dir: Optional[str] = None,
            tool_call_limit: int = 100,
            custom_subagent_configs: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize BuiltinTaskTool.

        Args:
            model: Model to use for subagents. If None, will use the parent agent's model.
            tools: Default tools for subagents. If None, will use basic tools based on type.
            work_dir: Work directory for file operations.
            tool_call_limit: Maximum number of tool calls allowed for subagent execution.
            custom_subagent_configs: Custom subagent configurations to add/override defaults.
        """
        super().__init__(name="builtin_task_tool")
        self._model = model
        self._tools = tools
        self._work_dir = work_dir
        self._tool_call_limit = tool_call_limit
        self._parent_agent: Optional["Agent"] = None
        self._custom_configs = custom_subagent_configs or {}
        self.register(self.task)

    def _build_subagent_table(self) -> str:
        """Build a markdown table of available subagent types."""
        from agentica.subagent import get_available_subagent_types

        available_types = get_available_subagent_types()

        lines = ["| Type | Name | Description |", "|------|------|-------------|"]
        for st in available_types:
            # Truncate description for table
            desc = st['description'].split('\n')[0][:60]
            if len(st['description'].split('\n')[0]) > 60:
                desc += "..."
            type_name = st['type']
            name = st['name']
            lines.append(f"| `{type_name}` | {name} | {desc} |")

        return "\n".join(lines)

    def get_system_prompt(self) -> Optional[str]:
        """
        Get the dynamically generated system prompt for task tool.

        This prompt is regenerated each time to include any custom subagent types
        that may have been registered since initialization.
        """
        subagent_table = self._build_subagent_table()
        return self.TASK_SYSTEM_PROMPT_TEMPLATE.format(subagent_table=subagent_table)

    def set_parent_agent(self, agent: "Agent") -> None:
        """Set the parent agent reference for accessing model and tools."""
        self._parent_agent = agent
        # Also set work_dir from parent agent if available
        if self._work_dir is None and hasattr(agent, 'work_dir') and agent.work_dir:
            self._work_dir = agent.work_dir

    def _get_tools_for_subagent(self, subagent_type: str) -> List[Any]:
        """Get appropriate tools for a subagent type based on config.allowed_tools.

        Uses SubagentConfig.allowed_tools as the single source of truth.
        Each allowed tool name is mapped to the Tool class that provides it.
        """
        from agentica.subagent import get_subagent_config
        from agentica.tools.buildin_tools import (
            BuiltinFileTool, BuiltinExecuteTool, BuiltinWebSearchTool,
            BuiltinFetchUrlTool, BuiltinTodoTool,
        )

        config = get_subagent_config(subagent_type)

        # Inherit sandbox_config from parent agent
        sandbox_cfg = None
        if self._parent_agent is not None:
            sandbox_cfg = self._parent_agent.sandbox_config

        # Default tools if no config found
        if config is None:
            return self._tools or [
                BuiltinFileTool(work_dir=self._work_dir, sandbox_config=sandbox_cfg),
                BuiltinWebSearchTool(),
                BuiltinFetchUrlTool(),
            ]

        # If allowed_tools is None, give all basic tools (except task)
        if config.allowed_tools is None:
            tools: List[Any] = [
                BuiltinFileTool(work_dir=self._work_dir, sandbox_config=sandbox_cfg),
                BuiltinExecuteTool(work_dir=self._work_dir, sandbox_config=sandbox_cfg),
                BuiltinWebSearchTool(),
                BuiltinFetchUrlTool(),
                BuiltinTodoTool(),
            ]
            return tools

        # Map function names to the Tool class that provides them
        # Each Tool class registers multiple functions; we track which classes are needed
        FILE_TOOL_FUNCTIONS = {"ls", "read_file", "write_file", "edit_file", "multi_edit_file", "glob", "grep"}
        EXECUTE_FUNCTIONS = {"execute"}
        WEB_SEARCH_FUNCTIONS = {"web_search"}
        FETCH_URL_FUNCTIONS = {"fetch_url"}
        TODO_FUNCTIONS = {"write_todos"}

        allowed = set(config.allowed_tools)
        tools = []

        if allowed & FILE_TOOL_FUNCTIONS:
            tools.append(BuiltinFileTool(work_dir=self._work_dir, sandbox_config=sandbox_cfg))
        if allowed & EXECUTE_FUNCTIONS:
            tools.append(BuiltinExecuteTool(work_dir=self._work_dir, sandbox_config=sandbox_cfg))
        if allowed & WEB_SEARCH_FUNCTIONS:
            tools.append(BuiltinWebSearchTool())
        if allowed & FETCH_URL_FUNCTIONS:
            tools.append(BuiltinFetchUrlTool())
        if allowed & TODO_FUNCTIONS:
            tools.append(BuiltinTodoTool())

        return tools

    def _get_parent_context_summary(self, max_chars: int = 2000) -> str:
        """Extract a brief context summary from the parent agent's recent conversation."""
        if self._parent_agent is None:
            return ""
        memory = self._parent_agent.working_memory
        if memory is None:
            return ""
        messages = memory.messages
        if not messages:
            return ""
        # Take last few messages, extract content
        summary_parts = []
        total = 0
        for msg in reversed(messages[-6:]):
            content = msg.content
            if content and isinstance(content, str):
                snippet = content[:500]
                summary_parts.append(f"[{msg.role}] {snippet}")
                total += len(snippet)
                if total > max_chars:
                    break
        summary_parts.reverse()
        return "\n".join(summary_parts)

    async def _run_subagent_stream(
        self, subagent: Any, task_description: str, tool_calls_log: List[Dict[str, Any]]
    ) -> str:
        """Run a subagent with streaming, collecting tool usage info."""
        final_content = ""
        from agentica.run_config import RunConfig
        async for chunk in subagent.run_stream(task_description, config=RunConfig(stream_intermediate_steps=True)):
            if chunk is None:
                continue
            # Collect tool call info from intermediate events
            if chunk.event in ("ToolCallStarted", "ToolCallCompleted"):
                if chunk.tools:
                    for tool_info in chunk.tools:
                        tool_name = tool_info.get("tool_name") or tool_info.get("name", "")
                        if not tool_name:
                            continue
                        tool_args = tool_info.get("tool_args") or tool_info.get("arguments", {})
                        content = tool_info.get("content")
                        brief = self._format_tool_brief(tool_name, tool_args, content)
                        entry = {"name": tool_name, "info": brief}
                        if chunk.event == "ToolCallCompleted":
                            for i in range(len(tool_calls_log) - 1, -1, -1):
                                if tool_calls_log[i]["name"] == tool_name and "result" not in tool_calls_log[i]:
                                    tool_calls_log[i]["info"] = brief
                                    tool_calls_log[i]["result"] = True
                                    break
                        else:
                            tool_calls_log.append(entry)
            # Accumulate final content
            if chunk.event in ("RunResponse",) and chunk.content:
                final_content += str(chunk.content)
        return final_content

    async def task(self, description: str, subagent_type: str = "code") -> str:
        """Launch a subagent to handle a complex task.

        Args:
            description: Detailed description of the task to perform.
                Include what you want the subagent to do and what information to return.
            subagent_type: Type of subagent to use. Options:
                - "explore": Read-only codebase exploration (fast, low context)
                - "research": Web search and document analysis
                - "code": Full code generation and execution (default)

        Returns:
            The result from the subagent after completing the task.
        """
        from agentica.subagent import (
            SubagentRegistry, SubagentRun, SubagentType,
            get_subagent_config,
        )

        # Get registry
        registry = SubagentRegistry()

        # Compute current nesting depth from parent agent's context.
        # Each spawned subagent inherits depth + 1 via its context dict.
        _MAX_TASK_DEPTH = 5
        current_depth = 0
        if self._parent_agent is not None and self._parent_agent.context:
            current_depth = int(self._parent_agent.context.get("_task_depth", 0))

        if current_depth >= _MAX_TASK_DEPTH:
            logger.warning(
                f"task() blocked: max recursion depth {_MAX_TASK_DEPTH} reached "
                f"(parent agent: {self._parent_agent.name if self._parent_agent else 'unknown'})"
            )
            return json.dumps({
                "success": False,
                "error": (
                    f"Max subagent nesting depth ({_MAX_TASK_DEPTH}) reached. "
                    "Complete your task directly without further delegation."
                ),
            }, ensure_ascii=False)

        # Check if we're already in a subagent (prevent nesting)
        if self._parent_agent is not None:
            parent_agent_id = self._parent_agent.agent_id
            if registry.is_subagent(parent_agent_id):
                return json.dumps({
                    "success": False,
                    "error": "Nested subagent spawning is not allowed. Complete your task without delegating.",
                }, ensure_ascii=False)

        try:
            # Get subagent configuration
            config = get_subagent_config(subagent_type)
            if config is None:
                # Default to code if unknown type
                config = get_subagent_config("code")
                logger.warning(f"Unknown subagent type '{subagent_type}', using 'code'")

            # Get model from parent agent or use configured model.
            # IMPORTANT: Create an isolated copy to avoid sharing mutable state (tools,
            # functions, function_call_stack, tool_choice, client) between parent and
            # subagent. We use shallow model_copy and manually reset runtime fields;
            # deep=True would fail because the HTTP client contains unpicklable locks.
            source_model = self._model
            if source_model is None and self._parent_agent is not None:
                source_model = self._parent_agent.model

            if source_model is None:
                return json.dumps({
                    "success": False,
                    "error": "No model available for subagent. Please configure a model.",
                }, ensure_ascii=False)

            import copy
            try:
                model = source_model.model_copy()
            except AttributeError:
                model = copy.copy(source_model)
            # Reset runtime state so Agent.__init__ registers subagent's own tools cleanly
            model.tools = None
            model.functions = None
            model.function_call_stack = None
            model.tool_choice = None
            model.metrics = {}
            from agentica.model.usage import Usage
            model.usage = Usage()
            # Force a fresh HTTP client (the old one belongs to the parent)
            for attr in ('client', 'http_client', 'async_client'):
                if hasattr(model, attr):
                    setattr(model, attr, None)

            # Generate unique run_id for subagent
            parent_agent_id = self._parent_agent.agent_id if self._parent_agent else 'main'
            run_id = str(uuid.uuid4())

            # Create subagent run entry
            run = SubagentRun(
                run_id=run_id,
                subagent_type=config.type,
                parent_agent_id=parent_agent_id,
                task_label=description[:50] + "..." if len(description) > 50 else description,
                task_description=description,
                started_at=datetime.now(),
                status="running",
            )
            registry.register(run)

            # Get tools for this subagent type
            subagent_tools = self._get_tools_for_subagent(subagent_type)

            # Import Agent here to avoid circular imports
            from agentica.agent import Agent

            # Create subagent with isolated session
            from agentica.agent.config import ToolConfig, PromptConfig

            # Apply permission isolation from config
            subagent_kwargs: Dict[str, Any] = dict(
                model=model,
                name=f"{config.name}",
                description=config.description,
                instructions=config.system_prompt,
                tools=subagent_tools,
                prompt_config=PromptConfig(markdown=True),
                tool_config=ToolConfig(tool_call_limit=config.tool_call_limit),
                # Propagate task depth so nested task() calls can detect recursion limit
                context={"_task_depth": current_depth + 1},
            )

            # Conditionally inherit workspace from parent
            if config.inherit_workspace and self._parent_agent is not None:
                parent_workspace = self._parent_agent.workspace
                if parent_workspace is not None:
                    subagent_kwargs['workspace'] = parent_workspace

            # Conditionally inherit knowledge base from parent
            if config.inherit_knowledge and self._parent_agent is not None:
                parent_knowledge = self._parent_agent.knowledge
                if parent_knowledge is not None:
                    subagent_kwargs['knowledge'] = parent_knowledge
                    subagent_kwargs['search_knowledge'] = True

            subagent = Agent(**subagent_kwargs)

            # Optionally prepend parent context summary to the task description
            task_description = description
            if config.inherit_context and self._parent_agent is not None:
                context_summary = self._get_parent_context_summary()
                if context_summary:
                    task_description = f"Parent agent context:\n{context_summary}\n\nTask:\n{description}"

            logger.debug(f"Launching {config.name} [{config.type.value}] for task: {description[:100]}...")

            # Run subagent with async streaming to collect tool usage info
            subagent_timeout = config.timeout if config.timeout > 0 else None
            start_time = time.time()
            tool_calls_log = []
            final_content = ""

            try:
                coro = self._run_subagent_stream(
                    subagent, task_description, tool_calls_log
                )
                if subagent_timeout:
                    final_content = await asyncio.wait_for(coro, timeout=subagent_timeout)
                else:
                    final_content = await coro
            except asyncio.TimeoutError:
                logger.warning(f"Subagent timed out after {subagent_timeout}s")
                final_content = f"[Subagent timed out after {subagent_timeout} seconds. Partial results may be available above.]"

            elapsed = time.time() - start_time
            result = final_content if final_content else "Subagent completed but returned no content."

            # Build tool calls summary for display
            tool_summary = []
            for tc in tool_calls_log:
                tool_summary.append({"name": tc["name"], "info": tc.get("info", "")})

            # Update registry with success
            registry.update_status(
                run_id=run_id,
                status="completed",
                result=result,
            )

            # Merge subagent usage into parent
            if (self._parent_agent is not None
                    and hasattr(subagent, 'model') and subagent.model is not None
                    and hasattr(self._parent_agent, 'model') and self._parent_agent.model is not None):
                self._parent_agent.model.usage.merge(subagent.model.usage)

            logger.debug(f"{config.name} [{config.type.value}] completed task.")

            return json.dumps({
                "success": True,
                "subagent_type": config.type.value,
                "subagent_name": config.name,
                "result": result,
                "tool_calls_summary": tool_summary,
                "execution_time": round(elapsed, 3),
                "tool_count": len(tool_summary),
            }, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"Subagent task error: {e}")

            # Update registry with error if we have a run_id
            if 'run_id' in locals():
                registry.update_status(
                    run_id=run_id,
                    status="error",
                    error=str(e),
                )

            return json.dumps({
                "success": False,
                "error": f"Subagent task error: {e}",
                "description": description[:300],
            }, ensure_ascii=False)

    @staticmethod
    def _format_tool_brief(tool_name: str, tool_args, content=None) -> str:
        """Format a brief description for a subagent tool call."""
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except (json.JSONDecodeError, TypeError):
                tool_args = {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        if tool_name == "read_file":
            fp = tool_args.get("file_path", "")
            if fp:
                fname = fp.rsplit("/", 1)[-1] if "/" in fp else fp
                lines = ""
                if tool_args.get("offset") or tool_args.get("limit"):
                    start = (tool_args.get("offset", 0) or 0) + 1
                    end = start + (tool_args.get("limit", 500) or 500) - 1
                    lines = f" (L{start}-{end})"
                if content:
                    line_count = str(content).count("\n") + 1
                    return f"Read {line_count} line(s) from {fname}"
                return f"{fname}{lines}"
        elif tool_name in ("grep", "search_content"):
            pattern = tool_args.get("pattern", "")
            if content and isinstance(content, str):
                match_count = content.count("\n") + 1 if content.strip() else 0
                return f'Found {match_count} match(es) for "{pattern[:40]}"'
            return f'"{pattern[:40]}"'
        elif tool_name in ("glob", "search_file"):
            pattern = tool_args.get("pattern", "")
            return f'pattern: {pattern}'
        elif tool_name == "ls":
            directory = tool_args.get("directory", ".")
            return directory.rsplit("/", 1)[-1] if "/" in directory else directory
        elif tool_name == "execute":
            cmd = tool_args.get("command", "")
            return cmd[:80] + ("..." if len(cmd) > 80 else "")
        elif tool_name == "write_file":
            fp = tool_args.get("file_path", "")
            return fp.rsplit("/", 1)[-1] if "/" in fp else fp
        elif tool_name == "edit_file":
            fp = tool_args.get("file_path", "")
            return fp.rsplit("/", 1)[-1] if "/" in fp else fp
        elif tool_name == "multi_edit_file":
            fp = tool_args.get("file_path", "")
            edits = tool_args.get("edits", [])
            fname = fp.rsplit("/", 1)[-1] if "/" in fp else fp
            return f"{fname} ({len(edits)} edits)"
        elif tool_name == "web_search":
            queries = tool_args.get("queries", "")
            if isinstance(queries, list):
                return ", ".join(str(q)[:30] for q in queries[:2])
            return str(queries)[:60]
        elif tool_name == "fetch_url":
            url = tool_args.get("url", "")
            return url[:60] + ("..." if len(url) > 60 else "")

        # Default: show first arg value briefly
        for k, v in tool_args.items():
            return f"{k}={str(v)[:50]}"
        return ""
