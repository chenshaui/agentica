# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Agent base class - V2 architecture with layered configuration.

Architecture:
- Agent defines identity and capabilities ("who I am, what I can do")
- Runner handles execution (LLM calls, tool calls, streaming, memory updates)
- Mixins: PromptsMixin, TeamMixin, ToolsMixin, PrinterMixin
- Session state: in-memory WorkingMemory (serializable via to_dict/from_dict)
- Multi-modal: images/videos/audio passed as run() parameters, not stored on Agent

Parameters organized in three layers:
1. Core definition (~10): model, name, instructions, tools, knowledge, etc.
2. Common config (~5): add_history_to_messages, debug, tracing, etc.
3. Packed config (4): PromptConfig, ToolConfig, WorkspaceMemoryConfig, TeamConfig
"""
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Sequence,
    Type,
    Union,
)
import copy
import os
import weakref
from inspect import signature
from uuid import uuid4
from pathlib import Path
from dataclasses import dataclass, field
from agentica.utils.log import logger, set_log_level_to_debug, set_log_level_to_info
from agentica.model.openai import OpenAIChat
from agentica.model.message import Message
from agentica.tools.base import ModelTool, Tool, Function
from agentica.model.base import Model
from agentica.run_response import RunResponse, AgentCancelledError
from agentica.run_config import RunConfig
from agentica.memory import WorkingMemory
from agentica.memory.session_log import SessionLog
from agentica.compression import CompressionManager
from agentica.config import LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY
from agentica.agent.config import (
    PromptConfig, ToolConfig, WorkspaceMemoryConfig, TeamConfig, SandboxConfig,
    ToolRuntimeConfig, SkillRuntimeConfig,
)
from agentica.hooks import AgentHooks, RunHooks, ConversationArchiveHooks, MemoryExtractHooks, _CompositeRunHooks
from agentica.runner import Runner

# Import mixin classes — pure method containers, no state, no __init__
from agentica.agent.prompts import PromptsMixin
from agentica.agent.team import TeamMixin
from agentica.agent.tools import ToolsMixin
from agentica.agent.printer import PrinterMixin


@dataclass(init=False)
class Agent(PromptsMixin, TeamMixin, ToolsMixin, PrinterMixin):
    """AI Agent — defines identity and capabilities.

    Agent only describes "who I am, what I can do".
    Session persistence is handled by external SessionManager.

    Parameters are organized in three layers:
    1. Core definition (~10): model, name, instructions, tools, etc.
    2. Common config (~5): add_history_to_messages, debug, etc.
    3. Packed config (4): prompt_config, tool_config, long_term_memory_config, team_config

    For output_language, markdown, search_knowledge etc., set them via
    prompt_config=PromptConfig(...) or tool_config=ToolConfig(...).

    Example - Minimal:
        >>> agent = Agent(instructions="You are a helpful assistant.")
        >>> response = await agent.run("Hello!")

    Example - Full:
        >>> agent = Agent(
        ...     name="analyst",
        ...     model=OpenAIChat(id="gpt-4o"),
        ...     instructions="You are a data analyst.",
        ...     tools=[web_search, calculator],
        ...     knowledge=my_knowledge,
        ...     response_model=AnalysisReport,
        ...     prompt_config=PromptConfig(markdown=True, output_language="Chinese"),
        ... )
    """

    # ============================
    # Layer 1: Core definition
    # ============================
    model: Optional[Model] = None
    name: Optional[str] = None
    agent_id: str = ""
    description: Optional[str] = None
    when_to_use: Optional[str] = None  # Hint for LLM: when to delegate tasks to this agent
    instructions: Optional[Union[str, List[str], Callable]] = None
    tools: Optional[List[Union[ModelTool, Tool, Callable, Dict, Function]]] = None
    knowledge: Optional[Any] = None  # Knowledge type
    team: Optional[List["Agent"]] = None
    workspace: Optional[Any] = None  # Workspace type
    work_dir: Optional[str] = None  # Working directory for file operations (used by builtin tools)
    memory: bool = False  # Whether to enable long-term memory tools and hooks
    response_model: Optional[Type[Any]] = None

    # ============================
    # Layer 2: Common config
    # ============================
    add_history_to_messages: bool = False
    history_window: int = 3
    structured_outputs: bool = False
    debug: bool = False
    tracing: bool = False

    # Session persistence (CC-style append-only JSONL):
    # Set session_id to enable. Stored at .sessions/{session_id}.jsonl
    # Supports compact boundaries for resume from last compaction point.
    session_id: Optional[str] = None

    # Lifecycle hooks (per-agent)
    hooks: Optional[AgentHooks] = None

    # ============================
    # Layer 3: Packed config
    # ============================
    prompt_config: PromptConfig = field(default_factory=PromptConfig)
    tool_config: ToolConfig = field(default_factory=ToolConfig)
    long_term_memory_config: WorkspaceMemoryConfig = field(default_factory=WorkspaceMemoryConfig)
    team_config: TeamConfig = field(default_factory=TeamConfig)
    sandbox_config: Optional[SandboxConfig] = None

    # Tool-level guardrails (run before/after each tool call)
    tool_input_guardrails: List[Any] = field(default_factory=list)
    tool_output_guardrails: List[Any] = field(default_factory=list)

    # ============================
    # Runtime
    # ============================
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    run_id: Optional[str] = field(default=None, init=False, repr=False)
    run_input: Optional[Any] = field(default=None, init=False, repr=False)
    run_response: RunResponse = field(default_factory=RunResponse, init=False, repr=False)
    stream: Optional[bool] = field(default=None, init=False, repr=False)
    stream_intermediate_steps: bool = field(default=False, init=False, repr=False)
    _cancelled: bool = field(default=False, init=False, repr=False)
    _running: bool = field(default=False, init=False, repr=False)

    # Run-level hooks (set per-run via run(hooks=...))
    _run_hooks: Optional[RunHooks] = field(default=None, init=False, repr=False)
    # Default run hooks (auto-injected, e.g. ConversationArchiveHooks when auto_archive=True)
    _default_run_hooks: Optional[RunHooks] = field(default=None, init=False, repr=False)
    # Per-run cost budget (USD). Set by Runner before _run_impl, read by Model.
    _run_max_cost_usd: Optional[float] = field(default=None, init=False, repr=False)

    # Tool/Skill runtime configs (Agent-level enable/disable)
    _tool_runtime_configs: Dict[str, ToolRuntimeConfig] = field(default_factory=dict, init=False, repr=False)
    _skill_runtime_configs: Dict[str, SkillRuntimeConfig] = field(default_factory=dict, init=False, repr=False)

    # Query-level enabled_tools/enabled_skills (set per-run, cleared after run)
    _enabled_tools: Optional[List[str]] = field(default=None, init=False, repr=False)
    _enabled_skills: Optional[List[str]] = field(default=None, init=False, repr=False)

    # Task list (populated by BuiltinTodoTool.write_todos)
    todos: List[Dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    # Context for tools and prompt functions (runtime input)
    context: Optional[Dict[str, Any]] = None
    # Reference to parent agent (set by team.get_tools for transfer hooks)
    _transfer_caller: Optional["Agent"] = field(default=None, init=False, repr=False)

    def __init__(
            self,
            *,
            # ---- Core definition ----
            model: Optional[Model] = None,
            name: Optional[str] = None,
            agent_id: Optional[str] = None,
            description: Optional[str] = None,
            when_to_use: Optional[str] = None,
            instructions: Optional[Union[str, List[str], Callable]] = None,
            tools: Optional[List[Union[ModelTool, Tool, Callable, Dict, Function]]] = None,
            knowledge: Optional[Any] = None,
            team: Optional[List["Agent"]] = None,
            workspace: Optional[Union[Any, str]] = None,  # Workspace or str path
            work_dir: Optional[str] = None,  # Working directory for file operations
            memory: bool = False,  # Enable long-term memory tools and hooks
            response_model: Optional[Type[Any]] = None,
            # ---- Common config ----
            add_history_to_messages: bool = False,
            history_window: int = 3,
            structured_outputs: bool = False,
            debug: bool = False,
            tracing: bool = False,
            hooks: Optional[AgentHooks] = None,
            # ---- Session persistence ----
            session_id: Optional[str] = None,
            # ---- Packed config ----
            prompt_config: Optional[PromptConfig] = None,
            tool_config: Optional[ToolConfig] = None,
            long_term_memory_config: Optional[WorkspaceMemoryConfig] = None,
            team_config: Optional[TeamConfig] = None,
            sandbox_config: Optional[SandboxConfig] = None,
            tool_input_guardrails: Optional[List[Any]] = None,
            tool_output_guardrails: Optional[List[Any]] = None,
            # ---- Runtime ----
            working_memory: Optional[WorkingMemory] = None,
            context: Optional[Dict[str, Any]] = None,
    ):
        # Core
        self.model = model
        self.name = name
        self.agent_id = agent_id or str(uuid4())
        self.description = description
        self.when_to_use = when_to_use
        self.instructions = instructions
        self.tools = tools
        self.knowledge = knowledge
        self.team = team
        self.response_model = response_model
        self.work_dir = work_dir
        self.memory = memory

        # Handle workspace: str → Workspace(path=str)
        if isinstance(workspace, str):
            from agentica.workspace import Workspace
            self.workspace = Workspace(workspace)
        else:
            self.workspace = workspace

        # Common
        self.add_history_to_messages = add_history_to_messages
        self.history_window = history_window
        self.structured_outputs = structured_outputs
        self.debug = debug
        self.tracing = tracing
        self.hooks = hooks

        # Session persistence
        self.session_id = session_id
        # JSONL session log: auto-created when session_id is set
        self._session_log = None
        if session_id is not None:
            self._session_log = SessionLog(session_id=session_id)

        # Packed config (use defaults if not provided)
        self.prompt_config = prompt_config or PromptConfig()
        self.tool_config = tool_config or ToolConfig()
        self.long_term_memory_config = long_term_memory_config or WorkspaceMemoryConfig()
        self.team_config = team_config or TeamConfig()
        self.sandbox_config = sandbox_config
        self.tool_input_guardrails = tool_input_guardrails or []
        self.tool_output_guardrails = tool_output_guardrails or []

        # Runtime
        self.working_memory = working_memory or WorkingMemory()
        self.context = context
        self.run_id = None
        self.run_input = None
        self.run_response = RunResponse()
        self.stream = None
        self.stream_intermediate_steps = False
        self._cancelled = False
        self._running = False
        self._run_hooks = None
        self._default_run_hooks = None
        self._tool_runtime_configs: Dict[str, ToolRuntimeConfig] = {}
        self._skill_runtime_configs: Dict[str, SkillRuntimeConfig] = {}
        self._enabled_tools = None
        self._enabled_skills = None
        self._transfer_caller = None
        self.todos = []

        # Session-level set of memory filenames already surfaced (dedup across turns).
        # Prevents the same memory entry from occupying system prompt slots every turn.
        self._surfaced_memories: set = set()

        # Create Runner instance
        self._runner = Runner(self)

        # Post-init setup
        self._post_init()

    def _post_init(self):
        """Post-initialization setup."""
        if self.debug:
            set_log_level_to_debug()
            logger.debug("Set Log level: debug")
        else:
            set_log_level_to_info()

        # Auto-load MCP tools
        if self.tool_config.auto_load_mcp:
            self._load_mcp_tools()

        # Merge tool system prompts into instructions
        self._merge_tool_system_prompts()

        # Wire builtin tools that need agent reference
        if self.tools:
            from agentica.tools.buildin_tools import BuiltinTodoTool, BuiltinMemoryTool
            from agentica.tools.builtin_task_tool import BuiltinTaskTool
            for tool in self.tools:
                if isinstance(tool, BuiltinTodoTool):
                    tool.set_agent(self)
                elif isinstance(tool, BuiltinTaskTool):
                    tool.set_parent_agent(self)
                elif isinstance(tool, BuiltinMemoryTool):
                    tool.set_workspace(self.workspace)
                    tool.set_sync_global_agent_md(
                        self.long_term_memory_config.sync_memories_to_global_agent_md
                    )

        # Register BuiltinMemoryTool when memory=True and workspace exists
        if self.memory and self.workspace is not None:
            from agentica.tools.buildin_tools import BuiltinMemoryTool
            has_memory_tool = any(isinstance(t, BuiltinMemoryTool) for t in (self.tools or []))
            if not has_memory_tool:
                memory_tool = BuiltinMemoryTool()
                memory_tool.set_workspace(self.workspace)
                memory_tool.set_sync_global_agent_md(
                    self.long_term_memory_config.sync_memories_to_global_agent_md
                )
                if self.tools is None:
                    self.tools = [memory_tool]
                else:
                    self.tools = list(self.tools) + [memory_tool]

        # Load runtime config from workspace YAML
        self._load_runtime_config()

        # Initialize compression manager
        if self.tool_config.compress_tool_results and self.tool_config.compression_manager is None:
            self.tool_config.compression_manager = CompressionManager(
                model=self.model,
                compress_tool_results=True,
                workspace=self.workspace,
            )

        # Tracing: check Langfuse config when enabled
        if self.tracing:
            if not LANGFUSE_SECRET_KEY or not LANGFUSE_PUBLIC_KEY:
                logger.warning(
                    "tracing=True but Langfuse is not configured. "
                    "Set environment variables to enable:\n"
                    "  LANGFUSE_SECRET_KEY=sk-lf-xxx\n"
                    "  LANGFUSE_PUBLIC_KEY=pk-lf-xxx\n"
                    "  LANGFUSE_BASE_URL=https://cloud.langfuse.com  # or self-hosted\n"
                    "Install: pip install langfuse"
                )

        # Auto-archive: inject ConversationArchiveHooks when memory=True and auto_archive=True (zero cost)
        # Auto-extract: inject MemoryExtractHooks when memory=True and auto_extract_memory=True (LLM cost)
        if self.memory and self.workspace is not None:
            auto_hooks: list = []
            if self.long_term_memory_config.auto_archive:
                auto_hooks.append(ConversationArchiveHooks())
            if self.long_term_memory_config.auto_extract_memory:
                auto_hooks.append(
                    MemoryExtractHooks(
                        sync_memories_to_global_agent_md=(
                            self.long_term_memory_config.sync_memories_to_global_agent_md
                        )
                    )
                )
            if auto_hooks:
                self._default_run_hooks = _CompositeRunHooks(auto_hooks)

    async def get_workspace_context_prompt(self) -> Optional[str]:
        """Dynamically load workspace context for system prompt."""
        if not self.workspace or not self.long_term_memory_config.load_workspace_context:
            return None
        if not self.workspace.exists():
            return None
        context = await self.workspace.get_context_prompt()
        return context if context else None

    async def get_workspace_memory_prompt(self, query: str = "") -> Optional[str]:
        """Dynamically load relevant workspace memory for system prompt.

        Uses CC-style relevance-based recall instead of loading all memory:
        - Scores MEMORY.md index entries against the current query
        - Loads only the top-k most relevant entry files
        - Deduplicates entries already shown in this session

        Args:
            query: Current user query string for relevance scoring.

        Returns:
            Formatted memory string, or None if workspace/memory not configured.
        """
        if not self.memory:
            return None
        if not self.workspace or not self.long_term_memory_config.load_workspace_memory:
            return None
        memory = await self.workspace.get_relevant_memories(
            query=query,
            limit=self.long_term_memory_config.max_memory_entries,
            already_surfaced=self._surfaced_memories,
        )
        return memory if memory else None

    def _load_mcp_tools(self):
        """Auto-load MCP tools from mcp_config.json/yaml if available."""
        try:
            import asyncio
            from agentica.mcp.config import MCPConfig
            from agentica.tools.mcp_tool import McpTool, CompositeMultiMcpTool

            config = MCPConfig()
            if not config.servers:
                return

            mcp_tool = McpTool.from_config(config_path=config.config_path)

            async def init_mcp():
                await mcp_tool.__aenter__()
                await mcp_tool.__aexit__(None, None, None)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, init_mcp())
                    future.result(timeout=30)
            else:
                asyncio.run(init_mcp())

            if self.tools is None:
                self.tools = [mcp_tool]
            else:
                self.tools = list(self.tools) + [mcp_tool]

            logger.info(f"Auto-loaded MCP tools from: {config.config_path}")
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Failed to auto-load MCP tools: {e}")

    def _merge_tool_system_prompts(self) -> None:
        """Collect system prompts from all tools and merge into instructions.

        Separates skill-related prompts from tool usage prompts,
        and formats them as markdown sections instead of XML tags.
        """
        if not self.tools:
            return

        from agentica.tools.skill_tool import SkillTool

        tool_prompts = []
        skill_prompts = []

        for tool in self.tools:
            if isinstance(tool, Tool) and hasattr(tool, 'get_system_prompt'):
                prompt = tool.get_system_prompt()
                if not prompt:
                    continue
                if isinstance(tool, SkillTool):
                    tool._agent = self
                    skill_prompts.append(prompt)
                else:
                    tool_prompts.append(prompt)

        if not tool_prompts and not skill_prompts:
            return

        merged_parts = []

        if tool_prompts:
            merged_parts.append("## Tool Usage Guide\n\n" + "\n\n".join(tool_prompts))

        if skill_prompts:
            merged_parts.append("\n".join(skill_prompts))

        merged_prompt = "\n\n---\n\n".join(merged_parts)

        if self.instructions is None:
            self.instructions = [merged_prompt]
        elif isinstance(self.instructions, str):
            self.instructions = [self.instructions, merged_prompt]
        elif isinstance(self.instructions, list):
            self.instructions = list(self.instructions) + [merged_prompt]

        logger.debug(f"Merged {len(tool_prompts)} tool prompts and {len(skill_prompts)} skill prompts into instructions")

    def cancel(self):
        """Cancel the current run. Can be called from another thread/task."""
        self._cancelled = True

    def _check_cancelled(self):
        """Check if cancelled and raise AgentCancelledError if so."""
        if self._cancelled:
            self._cancelled = False
            raise AgentCancelledError("Agent run cancelled by user")

    @property
    def is_streamable(self) -> bool:
        """For structured outputs we disable streaming."""
        return self.response_model is None

    @property
    def identifier(self) -> Optional[str]:
        return self.name or self.agent_id

    @classmethod
    def from_workspace(
        cls,
        workspace_path: str,
        model: Optional["Model"] = None,
        initialize: bool = True,
        **kwargs
    ) -> "Agent":
        """Create Agent from workspace path."""
        from agentica.workspace import Workspace

        workspace = Workspace(workspace_path)
        if initialize and not workspace.exists():
            workspace.initialize()

        return cls(workspace=workspace, model=model, **kwargs)

    def add_instruction(self, instruction: str):
        """Dynamically add instruction to Agent."""
        if not instruction:
            return
        if self.instructions is None:
            self.instructions = [instruction]
        elif isinstance(self.instructions, str):
            self.instructions = [self.instructions, instruction]
        elif isinstance(self.instructions, list):
            self.instructions = list(self.instructions) + [instruction]
        else:
            logger.warning(f"Cannot add instruction: instructions is {type(self.instructions)}")
            return
        logger.debug(f"Added instruction to agent: {instruction[:50]}...")

    # =========================================================================
    # Tool/Skill runtime control
    # =========================================================================

    def enable_tool(self, name: str) -> None:
        """Enable a tool by name (function name or tool class name)."""
        self._tool_runtime_configs[name] = ToolRuntimeConfig(name=name, enabled=True)

    def disable_tool(self, name: str) -> None:
        """Disable a tool by name (function name or tool class name)."""
        self._tool_runtime_configs[name] = ToolRuntimeConfig(name=name, enabled=False)

    def enable_skill(self, name: str) -> None:
        """Enable a skill by name."""
        self._skill_runtime_configs[name] = SkillRuntimeConfig(name=name, enabled=True)

    def disable_skill(self, name: str) -> None:
        """Disable a skill by name."""
        self._skill_runtime_configs[name] = SkillRuntimeConfig(name=name, enabled=False)

    def _is_tool_enabled(self, func_name: str) -> bool:
        """Check if a tool function is enabled.

        Priority: query-level (enabled_tools) > agent-level (runtime_configs) > default (True).
        """
        # Query-level whitelist: if set, only listed tools are allowed
        if self._enabled_tools is not None:
            return func_name in self._enabled_tools
        # Agent-level config
        cfg = self._tool_runtime_configs.get(func_name)
        if cfg is not None:
            return cfg.enabled
        return True

    def _is_skill_enabled(self, skill_name: str) -> bool:
        """Check if a skill is enabled.

        Priority: query-level (enabled_skills) > agent-level (runtime_configs) > default (True).
        """
        if self._enabled_skills is not None:
            return skill_name in self._enabled_skills
        cfg = self._skill_runtime_configs.get(skill_name)
        if cfg is not None:
            return cfg.enabled
        return True

    def _load_runtime_config(self) -> None:
        """Load tool/skill runtime configs from workspace YAML.

        Searches for `.agentica/runtime_config.yaml` in:
        1. workspace path (if workspace is set)
        2. current working directory

        YAML format:
            tools:
              execute:
                enabled: false
              write_file:
                enabled: true
            skills:
              iwiki-doc:
                enabled: false
        """
        config_name = ".agentica/runtime_config.yaml"
        config_path = None

        # Try workspace path first
        if self.workspace is not None:
            candidate = self.workspace.path / config_name
            if candidate.exists():
                config_path = candidate

        # Fallback: current working directory
        if config_path is None:
            candidate = Path(os.getcwd()) / config_name
            if candidate.exists():
                config_path = candidate

        if config_path is None:
            return

        try:
            import yaml
        except ImportError:
            logger.debug("PyYAML not installed, skipping runtime config loading")
            return

        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return

            # Load tool configs
            tools_data = data.get("tools")
            if isinstance(tools_data, dict):
                for name, cfg in tools_data.items():
                    if isinstance(cfg, dict):
                        enabled = cfg.get("enabled", True)
                        self._tool_runtime_configs[name] = ToolRuntimeConfig(name=name, enabled=enabled)

            # Load skill configs
            skills_data = data.get("skills")
            if isinstance(skills_data, dict):
                for name, cfg in skills_data.items():
                    if isinstance(cfg, dict):
                        enabled = cfg.get("enabled", True)
                        self._skill_runtime_configs[name] = SkillRuntimeConfig(name=name, enabled=enabled)

            logger.debug(f"Loaded runtime config from {config_path}")
        except Exception as e:
            logger.warning(f"Failed to load runtime config from {config_path}: {e}")

    def clone(self) -> "Agent":
        """Create a lightweight clone of this Agent for concurrent execution.

        Shares heavy config (tools, instructions, knowledge) but creates a
        fresh Model instance and resets all mutable runtime state.
        Safe for parallel asyncio.gather() calls.
        """
        clone = copy.copy(self)
        # Deep-copy model so each clone has independent state
        # (metrics, _agent_ref, function_call_stack, tool_choice)
        if self.model is not None:
            clone.model = copy.deepcopy(self.model)
        # Reset mutable runtime state
        clone.agent_id = str(uuid4())
        clone.run_id = None
        clone.run_input = None
        clone.run_response = RunResponse()
        clone.stream = None
        clone.stream_intermediate_steps = False
        clone._cancelled = False
        clone._running = False
        clone._run_hooks = None
        clone._default_run_hooks = None
        clone._enabled_tools = None
        clone._enabled_skills = None
        clone._session_log = None
        clone._transfer_caller = None
        # Fresh working memory (don't share session state)
        clone.working_memory = WorkingMemory()
        # Fresh session-level memory dedup set
        clone._surfaced_memories = set()
        # Fresh Runner bound to the clone
        clone._runner = Runner(clone)
        return clone

    def has_team(self) -> bool:
        return self.team is not None and len(self.team) > 0

    def add_introduction(self, introduction: str) -> None:
        """Add an introduction message to memory."""
        if introduction is None:
            return
        for message in self.working_memory.messages:
            if message.role == "assistant" and message.content == introduction:
                return
        self.working_memory.add_message(Message(role="assistant", content=introduction))

    def _resolve_context(self) -> None:
        logger.debug("Resolving context")
        if self.context is not None:
            for ctx_key, ctx_value in self.context.items():
                if callable(ctx_value):
                    try:
                        sig = signature(ctx_value)
                        resolved_ctx_value = None
                        if "agent" in sig.parameters:
                            resolved_ctx_value = ctx_value(agent=self)
                        else:
                            resolved_ctx_value = ctx_value()
                        if resolved_ctx_value is not None:
                            self.context[ctx_key] = resolved_ctx_value
                    except Exception as e:
                        logger.warning(f"Failed to resolve context for {ctx_key}: {e}")
                else:
                    self.context[ctx_key] = ctx_value

    def update_model(self) -> None:
        if self.model is None:
            logger.debug("Model not set, Using OpenAIChat as default")
            self.model = OpenAIChat()
        logger.debug(f"Agent '{self.name}' using {self.model.name or self.model.__class__.__name__}(id={self.model.id})")

        # Clear previously registered tools/functions and metrics to prevent accumulation
        # across multiple run() calls on the same Agent instance.
        if self.model.functions:
            self.model.functions.clear()
        if self.model.tools:
            self.model.tools.clear()
        self.model.metrics.clear()
        # Reset tool-call state so each agent run starts clean.
        # Prevents function_call_stack / tool_choice leaking between runs
        # (or between agents that share the same Model instance).
        self.model.function_call_stack = None
        self.model.tool_choice = None

        # Set agent reference on model (legacy, for backward compatibility with
        # direct model.run_function_calls() calls in tests/examples).
        # In normal Runner-driven execution, run_tools=False and Runner owns
        # tool execution, so _agent_ref is not needed.
        self.model._agent_ref = weakref.ref(self)

        # Set response_format
        if self.response_model is not None and self.model.response_format is None:
            if self.structured_outputs and self.model.supports_structured_outputs:
                logger.debug("Setting Model.response_format to Agent.response_model")
                self.model.response_format = self.response_model
                self.model.structured_outputs = True
            else:
                self.model.response_format = {"type": "json_object"}

        # Add tools to the Model (with runtime filtering)
        agent_tools = self.get_tools()
        if agent_tools is not None and self.tool_config.support_tool_calls:
            for tool in agent_tools:
                if (
                        self.response_model is not None
                        and self.structured_outputs
                        and self.model.supports_structured_outputs
                ):
                    self.model.add_tool(tool=tool, strict=True, agent=self)
                else:
                    self.model.add_tool(tool=tool, agent=self)

            # Filter out disabled functions from model after add_tool
            self._filter_model_functions()

        # Set tool_choice
        if self.model.tool_choice is None and self.tool_config.tool_choice is not None:
            self.model.tool_choice = self.tool_config.tool_choice

        # Set tool_call_limit
        if self.tool_config.tool_call_limit is not None:
            self.model.tool_call_limit = self.tool_config.tool_call_limit

        # Add agent name to the Model for Langfuse tracing
        if self.name is not None:
            self.model.agent_name = self.name

    def _build_pre_tool_hook(self):
        """Build the pre-tool hook function based on ToolConfig settings.

        Returns an async callable (messages, function_calls) -> bool, or None if no
        hooks are active.  Returning True tells run_function_calls to skip the batch.

        Capabilities bundled in the hook (both are opt-in via ToolConfig):

        1. Context overflow handling
           Triggered when: tool_config.context_overflow_threshold > 0 and
           estimated token usage / context_window >= threshold.
           Action: FIFO-evict oldest non-system messages until usage drops below
           a hard limit (threshold + 5pp), then log a warning.
           This is a best-effort heuristic — accurate token counting requires the
           tokenizer; here we estimate ~4 chars/token for speed.

        2. Repetition detection
           Triggered when: tool_config.max_repeated_tool_calls > 0 and the last
           N calls in function_call_stack all have the same (tool_name, args) pair.
           Action: inject a role="user" message telling the model it's looping and
           must change strategy, then return True (skip the current batch) so the
           model can reconsider on the next LLM call.

        Returning None means no hook is registered (fast path, no overhead).
        """
        overflow_threshold = self.tool_config.context_overflow_threshold
        max_repeat = self.tool_config.max_repeated_tool_calls

        # Fast path: neither feature is enabled
        if overflow_threshold <= 0.0 and max_repeat <= 0:
            return None

        agent_ref = self  # captured in closure

        async def _pre_tool_hook(messages: list, function_calls: list) -> bool:
            model = agent_ref.model
            if model is None:
                return False

            # ---- 1. Context overflow handling ----
            if overflow_threshold > 0.0:
                context_window = model.context_window or 128000
                # Estimate tokens: sum of all message content lengths / 4 (chars-per-token heuristic)
                total_chars = sum(
                    len(str(m.content)) if m.content else 0
                    for m in messages
                )
                estimated_tokens = total_chars / 4.0
                usage_ratio = estimated_tokens / context_window

                if usage_ratio >= overflow_threshold:
                    # Evict oldest non-system messages until we drop below threshold + 5pp hard limit
                    hard_limit = min(overflow_threshold + 0.05, 0.95)
                    evicted = 0
                    while usage_ratio >= hard_limit and len(messages) > 2:
                        # Find first non-system message
                        for idx, m in enumerate(messages):
                            if m.role != "system":
                                messages.pop(idx)
                                evicted += 1
                                break
                        else:
                            break  # Only system messages left
                        total_chars = sum(
                            len(str(m.content)) if m.content else 0
                            for m in messages
                        )
                        usage_ratio = (total_chars / 4.0) / context_window

                    logger.warning(
                        f"Agent '{agent_ref.identifier}': context overflow detected "
                        f"(estimated {usage_ratio:.0%} of {context_window} tokens). "
                        f"Evicted {evicted} old messages. "
                        "Set tool_config=ToolConfig(context_overflow_threshold=0.0) to disable."
                    )

            # ---- 2. Repetition detection ----
            if max_repeat > 0 and model.function_call_stack is not None:
                stack = model.function_call_stack
                if len(stack) >= max_repeat:
                    # Check if last N calls are all identical (same name + same args)
                    last_n = stack[-max_repeat:]
                    first = last_n[0]
                    first_key = (
                        first.function.name,
                        str(sorted(first.arguments.items())) if first.arguments else "",
                    )
                    all_same = all(
                        (
                            fc.function.name,
                            str(sorted(fc.arguments.items())) if fc.arguments else "",
                        ) == first_key
                        for fc in last_n
                    )
                    if all_same:
                        tool_name = first.function.name
                        logger.warning(
                            f"Agent '{agent_ref.identifier}': repetition detected — "
                            f"tool '{tool_name}' called with identical args {max_repeat}x in a row. "
                            "Injecting strategy-change message."
                        )
                        # Inject a user message to break the loop
                        messages.append(Message(
                            role="user",
                            content=(
                                f"[System notice] You have called '{tool_name}' {max_repeat} times "
                                f"in a row with identical arguments and it has not resolved the problem. "
                                "Stop repeating this call. Try a fundamentally different approach: "
                                "use a different tool, decompose the problem differently, or "
                                "report what you know so far and ask for clarification."
                            ),
                        ))
                        # Skip the current tool batch — let the model reconsider
                        return True

            return False  # proceed with tool execution

        return _pre_tool_hook

    def _build_post_tool_hook(self):
        """Build the post-tool hook function for todo reminder injection.

        Returns an async callable (messages, function_call_results) -> None, or None
        if no todo tool is registered.

        Mirrors CC's getTodoReminderAttachments: after each tool batch, count how many
        assistant turns have passed since the last write_todos call. If the count exceeds
        todo_reminder_interval and there are active todos, inject a gentle user-role
        reminder message containing the current todo list state.

        This is ephemeral -- the reminder appears in the in-flight messages only and
        does not persist to memory, avoiding permanent context pollution.
        """
        from agentica.tools.buildin_tools import BuiltinTodoTool

        # Check if agent has a BuiltinTodoTool registered
        has_todo_tool = False
        if self.tools:
            for tool in self.tools:
                if isinstance(tool, BuiltinTodoTool):
                    has_todo_tool = True
                    break

        if not has_todo_tool:
            return None

        reminder_interval = self.prompt_config.todo_reminder_interval
        if reminder_interval <= 0:
            return None

        agent_ref = self  # captured in closure

        async def _post_tool_hook(messages: list, function_call_results: list) -> None:
            # Count assistant turns since last write_todos call
            turns_since_write = 0
            turns_since_reminder = 0
            found_write = False
            found_reminder = False

            for m in reversed(messages):
                if m.role == "assistant":
                    if not found_write:
                        turns_since_write += 1
                    if not found_reminder:
                        turns_since_reminder += 1
                elif m.role == "tool" and m.tool_name == "write_todos" and not found_write:
                    found_write = True
                elif (
                    m.role == "user"
                    and isinstance(m.content, str)
                    and "[Todo Reminder]" in m.content
                    and not found_reminder
                ):
                    found_reminder = True

                if found_write and found_reminder:
                    break

            # Only inject if enough turns have passed since both last write and last reminder
            if turns_since_write < reminder_interval:
                return
            if turns_since_reminder < reminder_interval:
                return

            # Only inject if there are active (non-empty) todos
            todos = agent_ref.todos
            if not todos:
                return

            # Build reminder message (mirrors CC's todo_reminder attachment content)
            todo_items = "\n".join(
                f"  {i + 1}. [{t.get('status', 'pending')}] {t.get('content', '')}"
                for i, t in enumerate(todos)
            )
            reminder_content = (
                "[Todo Reminder] The write_todos tool hasn't been used recently. "
                "If you're working on tasks that would benefit from tracking progress, "
                "consider using the write_todos tool to update your progress. "
                "Also consider cleaning up the todo list if it has become stale. "
                "Only use it if relevant to the current work.\n\n"
                f"Current todo list:\n{todo_items}"
            )
            messages.append(Message(role="user", content=reminder_content))
            logger.debug(f"Injected todo reminder ({len(todos)} items, {turns_since_write} turns since write)")

        return _post_tool_hook

    def _filter_model_functions(self) -> None:
        """Filter disabled functions from the model.

        Removes functions that are disabled via agent-level config or query-level whitelist.
        This is called after update_model() adds all tools, so we filter at the function level.
        """
        if self.model is None or self.model.functions is None:
            return

        # If no filtering configured, skip
        if self._enabled_tools is None and not self._tool_runtime_configs:
            return

        disabled_funcs = []
        for func_name in list(self.model.functions.keys()):
            if not self._is_tool_enabled(func_name):
                disabled_funcs.append(func_name)

        if not disabled_funcs:
            return

        for func_name in disabled_funcs:
            del self.model.functions[func_name]

        # Rebuild model.tools list to match remaining functions
        if self.model.tools is not None:
            self.model.tools = [
                t for t in self.model.tools
                if not (isinstance(t, dict) and t.get("type") == "function"
                        and t.get("function", {}).get("name") in disabled_funcs)
            ]

        logger.debug(f"Filtered {len(disabled_funcs)} disabled tools: {disabled_funcs}")

    # =========================================================================
    # Run API — delegates to self._runner (public API unchanged)
    # =========================================================================

    async def run(
        self,
        message: Optional[Union[str, List, Dict, Message]] = None,
        *,
        audio: Optional[Any] = None,
        images: Optional[Sequence[Any]] = None,
        videos: Optional[Sequence[Any]] = None,
        messages: Optional[Sequence[Union[Dict, Message]]] = None,
        add_messages: Optional[List[Union[Dict, Message]]] = None,
        config: Optional[RunConfig] = None,
        **kwargs: Any,
    ) -> RunResponse:
        """Run the Agent and return the final response (non-streaming)."""
        return await self._runner.run(
            message=message,
            audio=audio,
            images=images,
            videos=videos,
            messages=messages,
            add_messages=add_messages,
            config=config,
            **kwargs,
        )

    async def run_stream(
        self,
        message: Optional[Union[str, List, Dict, Message]] = None,
        *,
        audio: Optional[Any] = None,
        images: Optional[Sequence[Any]] = None,
        videos: Optional[Sequence[Any]] = None,
        messages: Optional[Sequence[Union[Dict, Message]]] = None,
        add_messages: Optional[List[Union[Dict, Message]]] = None,
        config: Optional[RunConfig] = None,
        **kwargs: Any,
    ) -> AsyncIterator[RunResponse]:
        """Run the Agent and stream incremental responses."""
        async for chunk in self._runner.run_stream(
            message=message,
            audio=audio,
            images=images,
            videos=videos,
            messages=messages,
            add_messages=add_messages,
            config=config,
            **kwargs,
        ):
            yield chunk

    def run_sync(
        self,
        message: Optional[Union[str, List, Dict, Message]] = None,
        *,
        audio: Optional[Any] = None,
        images: Optional[Sequence[Any]] = None,
        videos: Optional[Sequence[Any]] = None,
        messages: Optional[Sequence[Union[Dict, Message]]] = None,
        add_messages: Optional[List[Union[Dict, Message]]] = None,
        config: Optional[RunConfig] = None,
        **kwargs: Any,
    ) -> RunResponse:
        """Synchronous wrapper for `run()` (non-streaming only)."""
        return self._runner.run_sync(
            message=message,
            audio=audio,
            images=images,
            videos=videos,
            messages=messages,
            add_messages=add_messages,
            config=config,
            **kwargs,
        )

    def run_stream_sync(
        self,
        message: Optional[Union[str, List, Dict, Message]] = None,
        *,
        audio: Optional[Any] = None,
        images: Optional[Sequence[Any]] = None,
        videos: Optional[Sequence[Any]] = None,
        messages: Optional[Sequence[Union[Dict, Message]]] = None,
        add_messages: Optional[List[Union[Dict, Message]]] = None,
        config: Optional[RunConfig] = None,
        **kwargs: Any,
    ) -> Iterator[RunResponse]:
        """Synchronous wrapper for `run_stream()`."""
        return self._runner.run_stream_sync(
            message=message,
            audio=audio,
            images=images,
            videos=videos,
            messages=messages,
            add_messages=add_messages,
            config=config,
            **kwargs,
        )
