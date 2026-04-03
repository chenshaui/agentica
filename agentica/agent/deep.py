# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: DeepAgent — Full-featured Agent with all capabilities enabled.

A pre-configured Agent with:
- 40+ built-in tools (file ops, web search, execute, task, memory, etc.)
- JSONL SessionLog (CC-style append-only with compact boundary resume)
- Conversation archive (auto_archive for search_conversations)
- Workspace memory (AGENT.md, MEMORY.md, daily memory, git context)
- 3-layer context compression (micro + auto + reactive compact)
- Death spiral detection + cost tracking + cost budget
- Agentic prompt (heartbeat, soul, tools guide, self-verification)
- Multi-turn history

Usage:
    from agentica import DeepAgent

    # One-liner: full-featured agent
    agent = DeepAgent()
    response = agent.run_sync("Research the latest advances in RAG")
    print(response.content)
    print(response.cost_summary)

    # With custom model
    from agentica import OpenAIChat
    agent = DeepAgent(model=OpenAIChat(id="gpt-4o"))

    # Resume previous session
    agent = DeepAgent(session_id="my-previous-session")

    # Any Agent parameter works via **kwargs
    agent = DeepAgent(debug=True, tracing=True, response_model=MyModel)
"""
import os
from typing import Any, Callable, Dict, List, Optional, Union

from agentica.agent.base import Agent
from agentica.agent.config import (
    PromptConfig,
    ToolConfig,
    WorkspaceMemoryConfig,
)
from agentica.model.base import Model
from agentica.tools.base import Tool, ModelTool, Function
from agentica.workspace import Workspace


class DeepAgent(Agent):
    """Full-featured Agent — batteries included.

    DeepAgent = Agent + builtin tools + compression + auto_archive +
    workspace memory + agentic prompt + history + cost tracking.

    All parameters are optional — sensible defaults are applied.
    Any Agent parameter can be overridden via **kwargs.
    """

    def __init__(
        self,
        *,
        # Only parameters with changed defaults are listed explicitly.
        # Everything else is forwarded to Agent via **kwargs.
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
            get_builtin_tools(work_dir=work_dir, workspace=workspace)
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
                memory_days=7,
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
            **kwargs,
        )
