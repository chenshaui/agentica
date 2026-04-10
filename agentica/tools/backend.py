# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Tool execution backend abstraction.

Decouples WHERE tools execute from HOW they are dispatched.
Default InProcessBackend runs tools in the current process (existing behavior).
SubprocessBackend and DockerBackend can be added for isolation.

Based on Anthropic's "brain vs hands" principle: the harness (Runner) decides
what to do, the backend decides how and where to execute it.
"""
import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol


@dataclass
class ToolExecutionContext:
    """Context passed to execution backend for each tool call."""

    agent_name: str
    run_id: str
    session_id: Optional[str] = None
    # Credential isolation: only these env vars are visible to the backend.
    # None = inherit all (default for InProcessBackend).
    allowed_env: Optional[Dict[str, str]] = None


@dataclass
class ToolExecutionResult:
    """Structured result from tool execution.

    Preserves type information instead of collapsing to a plain string.
    Includes error info, retry hints, artifacts, and timing.
    """

    content: str
    error: Optional[str] = None
    is_retryable: bool = False
    artifacts: Optional[List[Dict[str, Any]]] = None
    timing_ms: Optional[float] = None
    backend_name: str = "in_process"

    @property
    def success(self) -> bool:
        """True if execution completed without error."""
        return self.error is None


class ToolExecutionBackend(Protocol):
    """Protocol for tool execution environments.

    Implementations:
    - InProcessBackend: execute in current process (default, backward-compatible)
    - SubprocessBackend: execute in isolated subprocess (future)
    - DockerBackend: execute in Docker container (future)
    """

    async def execute(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """Execute a tool call and return structured result."""
        ...


class InProcessBackend:
    """Default backend: execute tools in the current process.

    This preserves existing Agentica behavior. Tools are called directly
    via their entrypoint functions.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, Callable] = {}

    def register(self, name: str, handler: Callable) -> None:
        """Register a tool handler by name."""
        self._handlers[name] = handler

    async def execute(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """Execute tool in current process.

        Args:
            tool_name: Name of the tool to execute.
            tool_args: Arguments dict to pass to the tool handler.
            context: Execution context (reserved for future isolated backends).
        """
        _ = context  # Reserved for SubprocessBackend / DockerBackend
        handler = self._handlers.get(tool_name)
        if not handler:
            return ToolExecutionResult(
                content="",
                error=f"Unknown tool: {tool_name}",
                backend_name="in_process",
            )

        start = time.time()
        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(**tool_args)
            else:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: handler(**tool_args)
                )
            elapsed = (time.time() - start) * 1000
            return ToolExecutionResult(
                content=str(result) if result is not None else "",
                timing_ms=elapsed,
                backend_name="in_process",
            )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return ToolExecutionResult(
                content="",
                error=f"{type(e).__name__}: {e}",
                is_retryable=not isinstance(e, (ValueError, TypeError)),
                timing_ms=elapsed,
                backend_name="in_process",
            )
