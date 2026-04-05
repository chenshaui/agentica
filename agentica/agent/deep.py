# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: DeepAgent — Full-featured Agent with all capabilities enabled.

A pre-configured Agent that enables every Runner agentic-loop feature:
- 40+ built-in tools (file ops, web search, execute, subagent task, todos)
- Runner agentic loop: LLM ↔ tool-call auto-loop with multi-turn reasoning
- 5-stage compression pipeline (tool-result budget → micro-compact →
  rule-based → auto-compact → reactive compact)
- Death spiral detection + cost tracking + cost budget
- Context overflow handling (FIFO message truncation at 80%)
- Repeated tool-call detection (inject "change strategy" at 3 repeats)
- Workspace memory (AGENT.md, MEMORY.md, daily memory, relevance recall)
- Conversation archive (auto_archive for search_conversations)
- Agentic prompt (heartbeat, tools guide, self-verification)
- Sandbox isolation (optional, off by default)
- Multi-turn history

Usage:
    from agentica import DeepAgent

    # One-liner: full-featured agent
    agent = DeepAgent()
    response = agent.run_sync("Research the latest advances in RAG")
    print(response.content)

    # Enable memory tool (LLM can save/search memories)
    agent = DeepAgent(include_memory=True)

    # Enable human-in-the-loop
    agent = DeepAgent(include_user_input=True)

    # Disable web search (file-only agent)
    agent = DeepAgent(include_web_search=False, include_fetch_url=False)

    # Custom task subagent model
    from agentica import OpenAIChat
    agent = DeepAgent(task_model=OpenAIChat(id="gpt-4o-mini"))

    # With cost budget
    from agentica import RunConfig
    response = await agent.run("Analyze X", config=RunConfig(max_cost_usd=1.0))
    print(response.cost_tracker.total_cost_usd)

    # With sandbox isolation
    from agentica import SandboxConfig
    agent = DeepAgent(sandbox_config=SandboxConfig(enabled=True, writable_dirs=["./output"]))

    # Any Agent parameter works via **kwargs
    agent = DeepAgent(debug=True, tracing=True, response_model=MyModel)
"""
import os
from typing import Any, Callable, Dict, List, Optional, Union

from agentica.agent.base import Agent
from agentica.agent.config import (
    PromptConfig,
    SandboxConfig,
    ToolConfig,
    WorkspaceMemoryConfig,
)
from agentica.model.base import Model
from agentica.tools.base import Tool, ModelTool, Function
from agentica.workspace import Workspace


class DeepAgent(Agent):
    """Full-featured Agent — batteries included.

    DeepAgent = Agent + builtin tools + Runner agentic loop features.

    Enabled by default:
    - 5-stage compression pipeline (compress_tool_results=True)
    - Context overflow handling at 80% (context_overflow_threshold=0.8)
    - Repeated tool-call detection at 3 (max_repeated_tool_calls=3)
    - Workspace memory with relevance recall (max_memory_entries=10)
    - Conversation auto-archive (auto_archive=True)
    - Agentic prompt with datetime and agent name

    All parameters are optional — sensible defaults are applied.
    Any Agent parameter can be overridden via **kwargs.
    """

    def __init__(
        self,
        *,
        model: Optional[Model] = None,
        name: str = "DeepAgent",
        tools: Optional[List[Union[ModelTool, Tool, Callable, Dict, Function]]] = None,
        workspace: Optional[Union[Any, str]] = None,
        work_dir: Optional[str] = None,
        session_id: Optional[str] = None,
        add_history_to_messages: bool = True,
        history_window: int = 5,
        prompt_config: Optional[PromptConfig] = None,
        tool_config: Optional[ToolConfig] = None,
        long_term_memory_config: Optional[WorkspaceMemoryConfig] = None,
        sandbox_config: Optional[SandboxConfig] = None,
        # Builtin tool toggles — mirror get_builtin_tools() params
        include_file_tools: bool = True,
        include_execute: bool = True,
        include_web_search: bool = True,
        include_fetch_url: bool = True,
        include_todos: bool = True,
        include_task: bool = True,
        include_skills: bool = True,
        include_user_input: bool = False,
        include_memory: bool = False,
        task_model: Optional[Model] = None,
        task_tools: Optional[List[Any]] = None,
        custom_skill_dirs: Optional[List[str]] = None,
        user_input_callback: Optional[Callable] = None,
        **kwargs,
    ):
        # Default model
        if model is None:
            from agentica.model.openai import OpenAIChat
            model = OpenAIChat(id="gpt-4o")

        # Default workspace
        if workspace is None:
            workspace = Workspace(os.path.expanduser("~/.agentica/workspace"))

        # Default work_dir
        if work_dir is None:
            work_dir = os.getcwd()

        # Builtin tools + user-provided tools
        from agentica.tools.buildin_tools import get_builtin_tools
        all_tools: List[Union[ModelTool, Tool, Callable, Dict, Function]] = list(
            get_builtin_tools(
                work_dir=work_dir,
                workspace=workspace,
                include_file_tools=include_file_tools,
                include_execute=include_execute,
                include_web_search=include_web_search,
                include_fetch_url=include_fetch_url,
                include_todos=include_todos,
                include_task=include_task,
                include_skills=include_skills,
                include_user_input=include_user_input,
                include_memory=include_memory,
                task_model=task_model,
                task_tools=task_tools,
                custom_skill_dirs=custom_skill_dirs,
                user_input_callback=user_input_callback,
                sandbox_config=sandbox_config,
            )
        )
        if tools:
            all_tools.extend(tools)

        # Opinionated config defaults (user can override by passing their own)
        if prompt_config is None:
            prompt_config = PromptConfig(
                markdown=True,
                enable_agentic_prompt=True,
                add_datetime_to_instructions=True,
                add_name_to_instructions=True,
            )

        if tool_config is None:
            tool_config = ToolConfig(
                compress_tool_results=True,
                context_overflow_threshold=0.8,
                max_repeated_tool_calls=3,
            )

        if long_term_memory_config is None:
            long_term_memory_config = WorkspaceMemoryConfig(
                auto_archive=True,
                load_workspace_context=True,
                load_workspace_memory=True,
                max_memory_entries=10,
            )

        super().__init__(
            model=model,
            name=name,
            tools=all_tools,
            workspace=workspace,
            work_dir=work_dir,
            session_id=session_id,
            add_history_to_messages=add_history_to_messages,
            history_window=history_window,
            prompt_config=prompt_config,
            tool_config=tool_config,
            long_term_memory_config=long_term_memory_config,
            sandbox_config=sandbox_config,
            **kwargs,
        )
