# -*- coding: utf-8 -*-
"""
ToolOrigin metadata: where did a tool callable come from?

Five canonical sources (matches openai-agents-python's classification, minus
the redundant ``subtype``):
  * ``builtin`` — agentica-shipped toolkits (file/exec/web_search/...).
  * ``function`` — user-supplied Python callable (``Tool.register`` /
    ``@tool``).
  * ``mcp``      — wrapped from an MCP server tool descriptor.
  * ``agent``    — another agent exposed via :py:meth:`Agent.as_tool`.
  * ``model``    — provider-side tool (web_search / file_search /
    code_interpreter), executed by the LLM provider, not by us.

The metadata flows down into:
  * :py:class:`agentica.tools.base.Function.origin`
  * :py:class:`agentica.tools.base.ModelTool.origin`
  * Session log entries (``origin_type`` / ``origin_provider_name`` / ...)
  * Tool start/end hook payloads
"""
from dataclasses import dataclass
from typing import Literal, Optional

ToolOriginType = Literal["builtin", "function", "mcp", "agent", "model"]


@dataclass(frozen=True)
class ToolOrigin:
    """Where a tool callable came from.

    Attributes:
        type: One of the five :data:`ToolOriginType` values.
        provider_name: Human-readable provider, e.g. MCP server name
            (``mcp``) or model provider (``model``). ``None`` otherwise.
        agent_name: When ``type == "agent"``, the source agent's name.
        source_tool_name: Original tool name BEFORE
            :func:`agentica.tools.base.normalize_tool_name` rewrote it.
            Useful for round-tripping back to the upstream identifier.
    """
    type: ToolOriginType
    provider_name: Optional[str] = None
    agent_name: Optional[str] = None
    source_tool_name: Optional[str] = None


__all__ = ["ToolOrigin", "ToolOriginType"]
