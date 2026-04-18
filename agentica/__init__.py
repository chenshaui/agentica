# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Agentica - Build AI Agents with ease.

═══════════════════════════════════════════════════════════════
v1.3.6+ Recommended Import Style (clearer; aligns with v2.0 plan)
═══════════════════════════════════════════════════════════════

Core (always default-installed)::

    from agentica import Agent, tool                          # core SDK
    from agentica.model.openai import OpenAIChat              # OpenAI / DeepSeek / Moonshot etc
    from agentica.model.anthropic.claude import Claude        # Claude (default)
    from agentica.workspace import Workspace                  # persistent workspace
    from agentica.tools.shell_tool import ShellTool           # specific tools

Optional extras (need ``pip install agentica[xxx]``)::

    from agentica.knowledge import Knowledge       # pip install agentica[rag]
    from agentica.vectordb import InMemoryVectorDb # pip install agentica[rag]
    from agentica.mcp import MCPClient             # pip install agentica[mcp]
    from agentica.acp import ACPServer             # pip install agentica[acp]
    from agentica.gateway.main import app          # pip install agentica[gateway]
    from agentica.db import SqliteDb               # pip install agentica[sql]

═══════════════════════════════════════════════════════════════
Backward Compatibility
═══════════════════════════════════════════════════════════════

Old-style top-level imports (e.g. ``from agentica import Knowledge``) STILL
work in v1.x but emit ``DeprecationWarning`` to encourage migration to
explicit sub-module paths above. Will be removed in v2.0.

See ``docs/API.md`` for the Tier 1/2/3 stability contract.
"""

import importlib
import threading
import warnings
from typing import TYPE_CHECKING

# ── Version ──
from agentica.version import __version__  # noqa: F401

# ── Config ──
from agentica.config import (
    AGENTICA_HOME,
    AGENTICA_DOTENV_PATH,
    AGENTICA_LOG_LEVEL,
    AGENTICA_LOG_FILE,
    AGENTICA_WORKSPACE_DIR,
    AGENTICA_PROJECTS_DIR,
    AGENTICA_CRON_DIR,
)

# ── Logging ──
from agentica.utils.log import set_log_level_to_debug, logger, set_log_level_to_info
from agentica.utils.io import write_audio_to_file

# ── Core Model (fast import) ──
from agentica.model.base import Model
from agentica.model.message import Message, MessageReferences, UserMessage, AssistantMessage, SystemMessage, ToolMessage
from agentica.model.content import Media, Video, Audio, Image
from agentica.model.response import ModelResponse, FileType
from agentica.model.usage import Usage, RequestUsage, TokenDetails
from agentica.model.openai.chat import OpenAIChat
from agentica.model.openai.like import OpenAILike
from agentica.model.azure.openai_chat import AzureOpenAIChat
from agentica.model.providers import create_provider, list_providers, get_supported_models

# ── Backward-compatible provider aliases ──
def DeepSeekChat(**kwargs):
    return create_provider("deepseek", **kwargs)

DeepSeek = DeepSeekChat

def MoonshotChat(**kwargs):
    return create_provider("moonshot", **kwargs)

Moonshot = MoonshotChat

def DoubaoChat(**kwargs):
    return create_provider("doubao", **kwargs)

Doubao = DoubaoChat

def TogetherChat(**kwargs):
    return create_provider("together", **kwargs)

Together = TogetherChat

def GrokChat(**kwargs):
    return create_provider("xai", **kwargs)

Grok = GrokChat

def YiChat(**kwargs):
    return create_provider("yi", **kwargs)

Yi = YiChat

def QwenChat(**kwargs):
    return create_provider("qwen", **kwargs)

Qwen = QwenChat

def ZhipuAIChat(**kwargs):
    return create_provider("zhipuai", **kwargs)

ZhipuAI = ZhipuAIChat

# ── Memory ──
from agentica.memory import (
    AgentRun, SessionSummary, MemorySummarizer, WorkingMemory,
    MemoryType, MemoryEntry,
    WorkflowRun, WorkflowMemory,
)

# ── Database (base types only) ──
from agentica.db.base import BaseDb, SessionRow, MemoryRow, MetricsRow

# ── Run Response ──
from agentica.run_response import RunResponse, RunEvent, RunResponseExtraData, ToolCallInfo, pprint_run_response

# ── Document ──
from agentica.document import Document

# ── Tool base ──
from agentica.tools.base import Tool, ModelTool, Function, FunctionCall
from agentica.tools.decorators import tool  # @tool decorator for defining tool functions

# ── Compression ──
from agentica.compression import CompressionManager

# ── Token counting ──
from agentica.utils.tokens import count_tokens, count_text_tokens, count_image_tokens, count_message_tokens, count_tool_tokens

# ── Agent (core) ──
from agentica.agent import Agent, AgentCancelledError
from agentica.agent.deep import DeepAgent
from agentica.agent.config import PromptConfig, ToolConfig, WorkspaceMemoryConfig, TeamConfig, SandboxConfig, ToolRuntimeConfig, SkillRuntimeConfig, ExperienceConfig, SkillUpgradeConfig
from agentica.run_config import RunConfig
from agentica.workflow import Workflow, WorkflowSession
from agentica.hooks import AgentHooks, RunHooks, ConversationArchiveHooks, MemoryExtractHooks, ExperienceCaptureHooks

# ── Experience system ──
from agentica.experience import ExperienceEventStore, ExperienceCompiler, CompiledExperienceStore, SkillEvolutionManager

# ── Workspace ──
from agentica.workspace import Workspace, WorkspaceConfig

# ============================================================================
# Lazy imports - loaded on demand to improve startup time
# ============================================================================

_LAZY_IMPORTS = {
    # database implementations
    "SqliteDb": "agentica.db.sqlite",
    "PostgresDb": "agentica.db.postgres",
    "InMemoryDb": "agentica.db.memory",
    "JsonDb": "agentica.db.json",
    "MysqlDb": "agentica.db.mysql",
    "RedisDb": "agentica.db.redis",

    # model providers (heavy dependencies)
    "LiteLLMChat": "agentica.model.litellm.chat",
    "LiteLLM": "agentica.model.litellm.chat",
    "KimiChat": "agentica.model.kimi.chat",
    "Claude": "agentica.model.anthropic.claude",
    "Ollama": "agentica.model.ollama.chat",

    # knowledge
    "Knowledge": "agentica.knowledge.base",
    "LlamaIndexKnowledge": "agentica.knowledge.llamaindex_knowledge",
    "LangChainKnowledge": "agentica.knowledge.langchain_knowledge",

    # vectordb
    "SearchType": "agentica.vectordb.base",
    "Distance": "agentica.vectordb.base",
    "VectorDb": "agentica.vectordb.base",
    "InMemoryVectorDb": "agentica.vectordb.memory_vectordb",

    # embeddings
    "Embedding": "agentica.embedding.base",
    "OpenAIEmbedding": "agentica.embedding.openai",
    "AzureOpenAIEmbedding": "agentica.embedding.azure_openai",
    "HashEmbedding": "agentica.embedding.hash",
    "OllamaEmbedding": "agentica.embedding.ollama",
    "TogetherEmbedding": "agentica.embedding.together",
    "FireworksEmbedding": "agentica.embedding.fireworks",
    "ZhipuAIEmbedding": "agentica.embedding.zhipuai",
    "HttpEmbedding": "agentica.embedding.http",
    "JinaEmbedding": "agentica.embedding.jina",
    "GeminiEmbedding": "agentica.embedding.gemini",
    "HuggingfaceEmbedding": "agentica.embedding.huggingface",
    "MulanAIEmbedding": "agentica.embedding.mulanai",

    # rerank
    "Rerank": "agentica.rerank.base",
    "JinaRerank": "agentica.rerank.jina",
    "ZhipuAIRerank": "agentica.rerank.zhipuai",

    # skills
    "Skill": "agentica.skills",
    "SkillRegistry": "agentica.skills",
    "SkillLoader": "agentica.skills",
    "get_skill_registry": "agentica.skills",
    "reset_skill_registry": "agentica.skills",
    "load_skills": "agentica.skills",
    "get_available_skills": "agentica.skills",
    "register_skill": "agentica.skills",
    "register_skills": "agentica.skills",
    "list_skill_files": "agentica.skills",
    "read_skill_file": "agentica.skills",

    # guardrails (agent-level)
    "GuardrailFunctionOutput": "agentica.guardrails",
    "InputGuardrail": "agentica.guardrails",
    "OutputGuardrail": "agentica.guardrails",
    "InputGuardrailResult": "agentica.guardrails",
    "OutputGuardrailResult": "agentica.guardrails",
    "input_guardrail": "agentica.guardrails",
    "output_guardrail": "agentica.guardrails",
    "InputGuardrailTripwireTriggered": "agentica.guardrails",
    "OutputGuardrailTripwireTriggered": "agentica.guardrails",

    # guardrails (tool-level)
    "ToolGuardrailFunctionOutput": "agentica.guardrails",
    "ToolInputGuardrail": "agentica.guardrails",
    "ToolOutputGuardrail": "agentica.guardrails",
    "ToolInputGuardrailData": "agentica.guardrails",
    "ToolOutputGuardrailData": "agentica.guardrails",
    "ToolContext": "agentica.guardrails",
    "tool_input_guardrail": "agentica.guardrails",
    "tool_output_guardrail": "agentica.guardrails",
    "ToolInputGuardrailTripwireTriggered": "agentica.guardrails",
    "ToolOutputGuardrailTripwireTriggered": "agentica.guardrails",
    "ToolGuardrailTripwireTriggered": "agentica.guardrails",
    "run_input_guardrails": "agentica.guardrails",
    "run_output_guardrails": "agentica.guardrails",
    "run_tool_input_guardrails": "agentica.guardrails",
    "run_tool_output_guardrails": "agentica.guardrails",

    # tools (external dependencies)
    "CronTool": "agentica.tools.cron_tool",
    "check_command_safety": "agentica.tools.safety",
    "redact_sensitive_text": "agentica.tools.safety",
    "set_interrupt": "agentica.tools.interrupt",
    "is_interrupted": "agentica.tools.interrupt",
    "tool_error": "agentica.tools.helpers",
    "tool_result": "agentica.tools.helpers",
    "SearchSerperTool": "agentica.tools.search_serper_tool",
    "BaiduSearchTool": "agentica.tools.baidu_search_tool",
    "ImageAnalysisTool": "agentica.tools.image_analysis_tool",
    "DalleTool": "agentica.tools.dalle_tool",
    "HackerNewsTool": "agentica.tools.hackernews_tool",
    "JinaTool": "agentica.tools.jina_tool",
    "ShellTool": "agentica.tools.shell_tool",
    "SkillTool": "agentica.tools.skill_tool",
    "WeatherTool": "agentica.tools.weather_tool",
    "CodeTool": "agentica.tools.code_tool",
    "PatchTool": "agentica.tools.patch_tool",

    # built-in tools
    "BuiltinFileTool": "agentica.tools.buildin_tools",
    "BuiltinExecuteTool": "agentica.tools.buildin_tools",
    "BuiltinWebSearchTool": "agentica.tools.buildin_tools",
    "BuiltinFetchUrlTool": "agentica.tools.buildin_tools",
    "BuiltinTodoTool": "agentica.tools.buildin_tools",
    "BuiltinTaskTool": "agentica.tools.builtin_task_tool",
    "BuiltinMemoryTool": "agentica.tools.buildin_tools",
    "get_builtin_tools": "agentica.tools.buildin_tools",

    # subagent system
    "SubagentType": "agentica.subagent",

    # swarm system
    "Swarm": "agentica.swarm",
    "SwarmResult": "agentica.swarm",
    "SubagentConfig": "agentica.subagent",
    "SubagentRun": "agentica.subagent",
    "SubagentRegistry": "agentica.subagent",
    "get_subagent_config": "agentica.subagent",
    "get_available_subagent_types": "agentica.subagent",
    "register_custom_subagent": "agentica.subagent",
    "unregister_custom_subagent": "agentica.subagent",
    "get_custom_subagent_configs": "agentica.subagent",

    # acp system
    "ACPServer": "agentica.acp",
    "ACPTool": "agentica.acp",
    "ACPToolCall": "agentica.acp",
    "ACPToolResult": "agentica.acp",
    "ACPRequest": "agentica.acp",
    "ACPResponse": "agentica.acp",
    "ACPErrorCode": "agentica.acp",
    "ACPMethod": "agentica.acp",
    "SessionManager": "agentica.acp",
    "ACPSession": "agentica.acp",
    "SessionStatus": "agentica.acp",

    # human-in-the-loop tool
    "UserInputTool": "agentica.tools.user_input_tool",
    "UserInputRequired": "agentica.tools.user_input_tool",

    # mcp
    "MCPConfig": "agentica.mcp.config",
    "McpTool": "agentica.tools.mcp_tool",
    "CompositeMultiMcpTool": "agentica.tools.mcp_tool",

}

_LAZY_CACHE = {}
_LAZY_LOCK = threading.Lock()


# Attribute name overrides: when the lazy-loaded symbol name differs from
# the actual attribute in the target module (e.g. LiteLLM is an alias for LiteLLMChat).
_LAZY_ATTR_OVERRIDES = {
    "LiteLLM": "LiteLLMChat",
    "DeepSeek": "DeepSeekChat",  # see provider aliases above
    "Moonshot": "MoonshotChat",
    "Doubao": "DoubaoChat",
    "Yi": "YiChat",
    "Together": "TogetherChat",
    "Xai": "XaiChat",
    "Nvidia": "NvidiaChat",
    "Sambanova": "SambanovaChat",
    "Groq": "GroqChat",
    "Cerebras": "CerebrasChat",
    "Mistral": "MistralChat",
}

# Symbols that emit DeprecationWarning when accessed via top-level `from agentica import X`.
# These are valid in v1.x for backward compat, but in v2.0 users should import from sub-modules.
# Format: {top_level_name: recommended_full_path}
_DEPRECATED_TOP_LEVEL = {
    # Knowledge / RAG
    "Knowledge": "agentica.knowledge.Knowledge",
    "LangChainKnowledge": "agentica.knowledge.LangChainKnowledge",
    "LlamaIndexKnowledge": "agentica.knowledge.LlamaIndexKnowledge",
    # Vector DB
    "VectorDb": "agentica.vectordb.VectorDb",
    "Distance": "agentica.vectordb.Distance",
    "SearchType": "agentica.vectordb.SearchType",
    "InMemoryVectorDb": "agentica.vectordb.InMemoryVectorDb",
    # Embedding
    "Embedding": "agentica.embedding.Embedding",
    "OpenAIEmbedding": "agentica.embedding.openai.OpenAIEmbedding",
    "AzureOpenAIEmbedding": "agentica.embedding.azure_openai.AzureOpenAIEmbedding",
    "OllamaEmbedding": "agentica.embedding.ollama.OllamaEmbedding",
    "TogetherEmbedding": "agentica.embedding.together.TogetherEmbedding",
    "FireworksEmbedding": "agentica.embedding.fireworks.FireworksEmbedding",
    "ZhipuAIEmbedding": "agentica.embedding.zhipuai.ZhipuAIEmbedding",
    "JinaEmbedding": "agentica.embedding.jina.JinaEmbedding",
    "GeminiEmbedding": "agentica.embedding.gemini.GeminiEmbedding",
    "HuggingfaceEmbedding": "agentica.embedding.huggingface.HuggingfaceEmbedding",
    "MulanAIEmbedding": "agentica.embedding.mulanai.MulanAIEmbedding",
    "HashEmbedding": "agentica.embedding.hash.HashEmbedding",
    "HttpEmbedding": "agentica.embedding.http.HttpEmbedding",
    # Rerank
    "Rerank": "agentica.rerank.Rerank",
    "JinaRerank": "agentica.rerank.jina.JinaRerank",
    "ZhipuAIRerank": "agentica.rerank.zhipuai.ZhipuAIRerank",
    # Database
    "SqliteDb": "agentica.db.SqliteDb",
    "PostgresDb": "agentica.db.PostgresDb",
    "MysqlDb": "agentica.db.MysqlDb",
    "RedisDb": "agentica.db.RedisDb",
    "InMemoryDb": "agentica.db.InMemoryDb",
    "JsonDb": "agentica.db.JsonDb",
    # Non-OpenAI/Anthropic providers
    "Claude": "agentica.model.anthropic.claude.Claude",
    "Ollama": "agentica.model.ollama.chat.Ollama",
    "LiteLLM": "agentica.model.litellm.chat.LiteLLMChat",
    "LiteLLMChat": "agentica.model.litellm.chat.LiteLLMChat",
    "KimiChat": "agentica.model.kimi.chat.KimiChat",
    # MCP
    "MCPConfig": "agentica.mcp.MCPConfig",
    # Subagent / Swarm / Workflow (Tier 3 experimental)
    "Swarm": "agentica.swarm.Swarm",
    "SwarmResult": "agentica.swarm.SwarmResult",
    "SubagentType": "agentica.subagent.SubagentType",
    "SubagentConfig": "agentica.subagent.SubagentConfig",
    "SubagentRun": "agentica.subagent.SubagentRun",
    "SubagentRegistry": "agentica.subagent.SubagentRegistry",
}


def _emit_deprecation_warning(name: str, new_path: str) -> None:
    """Emit a DeprecationWarning for top-level access of a symbol."""
    warnings.warn(
        f"`from agentica import {name}` is deprecated and will be removed in v2.0. "
        f"Use `from {new_path.rsplit('.', 1)[0]} import {new_path.rsplit('.', 1)[1]}` instead.",
        DeprecationWarning,
        stacklevel=3,  # skip __getattr__ + caller's import frame
    )


def __getattr__(name: str):
    """Lazy import handler for optional modules.

    Emits DeprecationWarning when top-level import path is deprecated (see
    `_DEPRECATED_TOP_LEVEL`). Always still returns the symbol (backward compat).
    """
    if name in _LAZY_IMPORTS:
        if name in _DEPRECATED_TOP_LEVEL:
            _emit_deprecation_warning(name, _DEPRECATED_TOP_LEVEL[name])
        if name not in _LAZY_CACHE:
            with _LAZY_LOCK:
                if name not in _LAZY_CACHE:
                    module_path = _LAZY_IMPORTS[name]
                    module = importlib.import_module(module_path)
                    attr_name = _LAZY_ATTR_OVERRIDES.get(name, name)
                    try:
                        _LAZY_CACHE[name] = getattr(module, attr_name)
                    except AttributeError:
                        # Fallback: original name
                        _LAZY_CACHE[name] = getattr(module, name)
        return _LAZY_CACHE[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """List all available names including lazy imports."""
    eager_names = [name for name in globals() if not name.startswith('_')]
    return sorted(set(eager_names) | set(_LAZY_IMPORTS.keys()))


if TYPE_CHECKING:
    from agentica.db.sqlite import SqliteDb  # noqa: F401
    from agentica.db.postgres import PostgresDb  # noqa: F401
    from agentica.db.memory import InMemoryDb  # noqa: F401
    from agentica.db.json import JsonDb  # noqa: F401
    from agentica.db.mysql import MysqlDb  # noqa: F401
    from agentica.db.redis import RedisDb  # noqa: F401
    from agentica.model.litellm.chat import LiteLLMChat  # noqa: F401
    from agentica.model.litellm.chat import LiteLLMChat as LiteLLM  # noqa: F401
    from agentica.model.kimi.chat import KimiChat  # noqa: F401
    from agentica.model.anthropic.claude import Claude  # noqa: F401
    from agentica.model.ollama.chat import Ollama  # noqa: F401
    from agentica.knowledge.base import Knowledge  # noqa: F401
    from agentica.knowledge.llamaindex_knowledge import LlamaIndexKnowledge  # noqa: F401
    from agentica.knowledge.langchain_knowledge import LangChainKnowledge  # noqa: F401
    from agentica.vectordb.base import SearchType, Distance, VectorDb  # noqa: F401
    from agentica.vectordb.memory_vectordb import InMemoryVectorDb  # noqa: F401
    from agentica.embedding.base import Embedding  # noqa: F401
    from agentica.embedding.openai import OpenAIEmbedding  # noqa: F401
    from agentica.embedding.azure_openai import AzureOpenAIEmbedding  # noqa: F401
    from agentica.embedding.hash import HashEmbedding  # noqa: F401
    from agentica.embedding.ollama import OllamaEmbedding  # noqa: F401
    from agentica.embedding.together import TogetherEmbedding  # noqa: F401
    from agentica.embedding.fireworks import FireworksEmbedding  # noqa: F401
    from agentica.embedding.zhipuai import ZhipuAIEmbedding  # noqa: F401
    from agentica.embedding.http import HttpEmbedding  # noqa: F401
    from agentica.embedding.jina import JinaEmbedding  # noqa: F401
    from agentica.embedding.gemini import GeminiEmbedding  # noqa: F401
    from agentica.embedding.huggingface import HuggingfaceEmbedding  # noqa: F401
    from agentica.embedding.mulanai import MulanAIEmbedding  # noqa: F401
    from agentica.rerank.base import Rerank  # noqa: F401
    from agentica.rerank.jina import JinaRerank  # noqa: F401
    from agentica.rerank.zhipuai import ZhipuAIRerank  # noqa: F401
    from agentica.guardrails import (  # noqa: F401
        GuardrailFunctionOutput, InputGuardrail, OutputGuardrail,
        InputGuardrailResult, OutputGuardrailResult,
        input_guardrail, output_guardrail,
        InputGuardrailTripwireTriggered, OutputGuardrailTripwireTriggered,
        ToolGuardrailFunctionOutput, ToolInputGuardrail, ToolOutputGuardrail,
        ToolInputGuardrailData, ToolOutputGuardrailData, ToolContext,
        tool_input_guardrail, tool_output_guardrail,
        ToolInputGuardrailTripwireTriggered, ToolOutputGuardrailTripwireTriggered,
        run_input_guardrails, run_output_guardrails,
        run_tool_input_guardrails, run_tool_output_guardrails,
    )
    from agentica.tools.search_serper_tool import SearchSerperTool  # noqa: F401
    from agentica.tools.dalle_tool import DalleTool  # noqa: F401
    from agentica.tools.shell_tool import ShellTool  # noqa: F401
    from agentica.tools.code_tool import CodeTool  # noqa: F401
    from agentica.tools.mcp_tool import McpTool, CompositeMultiMcpTool  # noqa: F401
    from agentica.mcp.config import MCPConfig  # noqa: F401


__all__ = [
    "__version__",
    # config
    "AGENTICA_HOME", "AGENTICA_DOTENV_PATH", "AGENTICA_LOG_LEVEL",
    "AGENTICA_LOG_FILE", "AGENTICA_WORKSPACE_DIR", "AGENTICA_PROJECTS_DIR",
    "AGENTICA_CRON_DIR",
    # logging
    "set_log_level_to_debug", "set_log_level_to_info", "logger",
    # utils
    "write_audio_to_file",
    # models (eager)
    "Model", "Message", "MessageReferences", "UserMessage", "AssistantMessage",
    "SystemMessage", "ToolMessage", "Media", "Video", "Audio", "Image",
    "ModelResponse", "FileType", "Usage", "RequestUsage", "TokenDetails",
    "OpenAIChat", "OpenAILike", "AzureOpenAIChat",
    "create_provider", "list_providers", "get_supported_models",
    # provider aliases
    "DeepSeekChat", "DeepSeek", "MoonshotChat", "Moonshot",
    "DoubaoChat", "Doubao", "TogetherChat", "Together",
    "GrokChat", "Grok", "YiChat", "Yi", "QwenChat", "Qwen",
    "ZhipuAIChat", "ZhipuAI",
    # memory
    "AgentRun", "SessionSummary", "MemorySummarizer", "WorkingMemory",
    "MemoryType", "MemoryEntry",
    "WorkflowRun", "WorkflowMemory",
    # database
    "BaseDb", "SessionRow", "MemoryRow", "MetricsRow",
    # run response
    "RunResponse", "RunEvent", "RunResponseExtraData", "ToolCallInfo", "pprint_run_response",
    # document
    "Document",
    # tools
    "Tool", "ModelTool", "Function", "FunctionCall", "tool",
    # compression
    "CompressionManager",
    # token counting
    "count_tokens", "count_text_tokens", "count_image_tokens",
    "count_message_tokens", "count_tool_tokens",
    # agent
    "Agent", "AgentCancelledError",
    "PromptConfig", "ToolConfig", "WorkspaceMemoryConfig", "TeamConfig", "SandboxConfig",
    "ToolRuntimeConfig", "SkillRuntimeConfig", "ExperienceConfig", "SkillUpgradeConfig",
    "RunConfig", "Workflow", "WorkflowSession", "AgentHooks", "RunHooks",
    "ConversationArchiveHooks", "MemoryExtractHooks", "ExperienceCaptureHooks",
    # experience system
    "ExperienceEventStore", "ExperienceCompiler", "CompiledExperienceStore", "SkillEvolutionManager",
    # workspace
    "Workspace", "WorkspaceConfig",
    # lazy imports
    *_LAZY_IMPORTS.keys(),
]
