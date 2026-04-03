# -*- encoding: utf-8 -*-
"""
@author: orange-crow, XuMing(xuming624@qq.com)
@description:
part of the code is from phidata
"""
import asyncio
import collections.abc
import io
import base64
import weakref
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from types import GeneratorType
from typing import List, Iterator, AsyncIterator, Optional, Dict, Any, Callable, Union, Sequence

from agentica.utils.log import logger
from agentica.model.message import Message
from agentica.model.metrics import Metrics
from agentica.model.response import ModelResponse, ModelResponseEvent
from agentica.model.usage import Usage, RequestUsage, TokenDetails
from agentica.tools.base import ModelTool, Tool, Function, FunctionCall, ToolCallException, get_function_call_for_tool_call
from agentica.utils.timer import Timer
from agentica.cost_tracker import CostTracker


@dataclass
class Model(ABC):
    """LLM 模型抽象基类。子类必须实现 invoke/invoke_stream/response/response_stream。"""

    # ID of the model to use.
    id: str = "not-provided"
    # Name for this Model. This is not sent to the Model API.
    name: Optional[str] = None
    # Provider for this Model. This is not sent to the Model API.
    provider: Optional[str] = None
    # Metrics collected for this Model. This is not sent to the Model API.
    metrics: Dict[str, Any] = field(default_factory=dict)
    # Structured usage tracking (cross-request aggregation).
    usage: Usage = field(default_factory=Usage)
    response_format: Optional[Any] = None

    # -*- Model capability limits (not sent to the API) -*-
    context_window: int = 128000
    max_output_tokens: Optional[int] = None

    # A list of tools provided to the Model.
    tools: Optional[List[Union[ModelTool, Dict]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    run_tools: bool = True
    tool_call_limit: Optional[int] = None
    max_concurrent_tools: int = 10

    # -*- Functions available to the Model to call -*-
    functions: Optional[Dict[str, Function]] = None
    function_call_stack: Optional[List[FunctionCall]] = None

    # System prompt from the model added to the Agent.
    system_prompt: Optional[str] = None
    # Instructions from the model added to the Agent.
    instructions: Optional[List[str]] = None

    # Session ID of the calling Agent or Workflow.
    session_id: Optional[str] = None
    # User ID of the calling Agent.
    user_id: Optional[str] = None
    # Agent name for tracing.
    agent_name: Optional[str] = None
    # Whether to use the structured outputs with this Model.
    structured_outputs: Optional[bool] = None
    # Whether the Model supports structured outputs.
    supports_structured_outputs: bool = False

    # --- Private fields (not in __init__ signature, used internally) ---
    _current_messages: Optional[List[Message]] = field(init=False, repr=False, default=None)
    _agent_ref: Optional[weakref.ref] = field(init=False, repr=False, default=None)

    # Cost tracker (v3): accumulates USD cost across all invoke() calls in a run.
    # Reset to a fresh CostTracker at the start of each Agent.run().
    _cost_tracker: Optional[CostTracker] = field(init=False, repr=False, default=None)

    # Sentinel flag for the agentic loop: when True, provider response() methods
    # should NOT call agentic_loop() — they just execute tools and return, letting
    # the outer while-loop drive the next iteration.
    _in_agentic_loop: bool = field(init=False, repr=False, default=False)

    # Model-layer lifecycle hooks (injected by Agent.update_model()).
    # _pre_tool_hook:  called before each batch of tool calls with (messages, function_calls).
    #                  May mutate messages (e.g. truncate context, inject reflection).
    #                  Returns True to proceed, False to skip the tool batch.
    # _post_tool_hook: called after tool results are appended to messages.
    #                  May mutate messages (e.g. inject a reflection prompt).
    _pre_tool_hook: Optional[Callable] = field(init=False, repr=False, default=None)
    _post_tool_hook: Optional[Callable] = field(init=False, repr=False, default=None)

    def __post_init__(self):
        # Auto-set provider if not provided
        if self.provider is None:
            self.provider = f"{self.name} ({self.id})" if self.name else self.id

    @property
    @abstractmethod
    def request_kwargs(self) -> Dict[str, Any]:
        """构建 API 请求参数字典，子类必须实现。"""
        ...

    def to_dict(self) -> Dict[str, Any]:
        _dict = {"name": self.name, "id": self.id, "provider": self.provider, "metrics": self.metrics}
        if self.functions:
            _dict["functions"] = {k: v.to_dict() for k, v in self.functions.items()}
            _dict["tool_call_limit"] = self.tool_call_limit
        return _dict

    def __repr__(self) -> str:
        """Concise representation for logging."""
        tools_count = len(self.tools) if self.tools else 0
        # Show first 3 + *** + last 4 chars of api_key for readability
        api_key = getattr(self, 'api_key', None) or ""
        if api_key and len(api_key) >= 8:
            key_hint = f"{api_key[:3]}***{api_key[-4:]}"
        elif api_key and len(api_key) >= 4:
            key_hint = f"***{api_key[-4:]}"
        else:
            key_hint = ""
        # Show base_url
        base_url = getattr(self, 'base_url', None) or ""
        parts = [f"id={self.id!r}"]
        if base_url:
            parts.append(f"base_url={str(base_url)!r}")
        if key_hint:
            parts.append(f"api_key='{key_hint}'")
        parts.append(f"tools={tools_count}")
        return f"{self.name or self.__class__.__name__}({', '.join(parts)})"

    def __str__(self) -> str:
        return self.__repr__()

    # --- Async-only abstract methods (subclasses must implement) ---

    @abstractmethod
    async def invoke(self, messages: List[Message]) -> Any:
        """调用 LLM API，返回原始 SDK 响应。"""
        ...

    @abstractmethod
    async def invoke_stream(self, messages: List[Message]) -> Any:
        """流式调用 LLM API，yield 原始 SDK chunk。"""
        ...

    @abstractmethod
    async def response(self, messages: List[Message]) -> ModelResponse:
        """完整响应（含工具调用循环），返回 ModelResponse。"""
        ...

    @abstractmethod
    async def response_stream(self, messages: List[Message]) -> AsyncIterator[ModelResponse]:
        """流式响应（含工具调用循环），yield ModelResponse。"""
        ...

    @staticmethod
    def sanitize_messages(messages: List[Message]) -> List[Message]:
        """Validate and fix tool call message sequences.

        OpenAI API requires that every assistant message with 'tool_calls' must be
        followed by tool messages responding to each 'tool_call_id'. If any tool
        response is missing (e.g. due to an interrupted execution or corrupted
        history), this method adds a placeholder tool response so the API call
        does not fail.

        The messages list is modified **in-place** and also returned.

        Args:
            messages: The list of messages to sanitize.

        Returns:
            The same list of messages after sanitization.
        """
        i = 0
        while i < len(messages):
            msg = messages[i]
            # Only process assistant messages that have tool_calls
            if msg.role == "assistant" and msg.tool_calls:
                expected_ids = {}
                for tc in msg.tool_calls:
                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if tc_id:
                        expected_ids[tc_id] = tc

                # Scan the following messages for matching tool responses.
                # We scan all messages until the next assistant message (or end),
                # because additional non-tool messages (e.g. from ToolCallException)
                # may be interleaved between tool responses.
                j = i + 1
                first_non_tool_pos = None
                while j < len(messages):
                    next_msg = messages[j]
                    if next_msg.role == "tool" and next_msg.tool_call_id in expected_ids:
                        del expected_ids[next_msg.tool_call_id]
                        j += 1
                    elif next_msg.role == "assistant":
                        # Reached the next assistant turn — stop scanning
                        break
                    else:
                        # Track first non-tool position for placeholder insertion
                        if first_non_tool_pos is None:
                            first_non_tool_pos = j
                        j += 1

                # Insert placeholder responses for any missing tool_call_ids.
                # Insert right after the assistant message + existing tool responses,
                # before any non-tool messages.
                if expected_ids:
                    insert_pos = first_non_tool_pos if first_non_tool_pos is not None else j
                    for tc_id, tc in expected_ids.items():
                        func_info = tc.get("function", {}) if isinstance(tc, dict) else {}
                        func_name = func_info.get("name", "unknown") if isinstance(func_info, dict) else "unknown"
                        logger.debug(
                            f"Missing tool response for tool_call_id={tc_id} "
                            f"(function={func_name}), inserting placeholder."
                        )
                        placeholder = Message(
                            role="tool",
                            tool_call_id=tc_id,
                            content=f"Error: tool call '{func_name}' did not return a response (execution may have been interrupted).",
                        )
                        messages.insert(insert_pos, placeholder)
                        insert_pos += 1
                    # Re-scan from current position since we inserted messages
                    continue
            i += 1
        return messages

    def _log_messages(self, messages: List[Message]) -> None:
        """
        Log messages for debugging.
        """
        for m in messages:
            m.log()

    def get_tools_for_api(self) -> Optional[List[Dict[str, Any]]]:
        if self.tools is None:
            return None

        tools_for_api = []
        for tool in self.tools:
            if isinstance(tool, ModelTool):
                tools_for_api.append(tool.to_dict())
            elif isinstance(tool, Dict):
                tools_for_api.append(tool)
        return tools_for_api

    def add_tool(
            self, tool: Union[ModelTool, Tool, Callable, Dict, Function], strict: bool = False,
            agent: Optional[Any] = None
    ) -> None:
        if self.tools is None:
            self.tools = []

        # If the tool is a Tool or Dict, add it directly to the Model
        if isinstance(tool, ModelTool) or isinstance(tool, Dict):
            if tool not in self.tools:
                self.tools.append(tool)
                logger.debug(f"Added tool {tool} to model.")

        # If the tool is a Callable or Toolkit, process and add to the Model
        elif callable(tool) or isinstance(tool, Tool) or isinstance(tool, Function):
            if self.functions is None:
                self.functions = {}

            if isinstance(tool, Tool):
                # For each function in the toolkit, process entrypoint and add to self.tools
                for name, func in tool.functions.items():
                    # If the function does not exist in self.functions, add to self.tools
                    if name not in self.functions:
                        func._agent = agent
                        func.process_entrypoint(strict=strict)
                        if strict and self.supports_structured_outputs:
                            func.strict = True
                        self.functions[name] = func
                        self.tools.append({"type": "function", "function": func.to_dict()})
                        logger.debug(f"Function {name} from {tool.name} added to model.")

            elif isinstance(tool, Function):
                if tool.name not in self.functions:
                    tool._agent = agent
                    tool.process_entrypoint(strict=strict)
                    if strict and self.supports_structured_outputs:
                        tool.strict = True
                    self.functions[tool.name] = tool
                    self.tools.append({"type": "function", "function": tool.to_dict()})
                    logger.debug(f"Function {tool.name} added to model.")

            elif callable(tool):
                try:
                    function_name = tool.__name__
                    if function_name not in self.functions:
                        func = Function.from_callable(tool, strict=strict)
                        func._agent = agent
                        if strict and self.supports_structured_outputs:
                            func.strict = True
                        self.functions[func.name] = func
                        self.tools.append({"type": "function", "function": func.to_dict()})
                        logger.debug(f"Function {func.name} added to model.")
                except Exception as e:
                    logger.warning(f"Could not add function {tool}: {e}")

    def deactivate_function_calls(self) -> None:
        # Deactivate tool calls by setting future tool calls to "none"
        # This is triggered when the function call limit is reached.
        self.tool_choice = "none"

    async def run_function_calls(
            self, function_calls: List[FunctionCall], function_call_results: List[Message], tool_role: str = "tool"
    ) -> AsyncIterator[ModelResponse]:
        """Execute tool calls with concurrency-split execution.

        Strategy (mirrors CC's StreamingToolExecutor):
        - concurrency_safe=True  tools run in parallel with each other.
        - concurrency_safe=False tools run sequentially, one at a time.
        - A *bash/execute* error aborts any remaining unsafe tools
          (sibling_error pattern from CC).

        Phase 0: _pre_tool_hook
        Phase 1: Emit tool_call_started events (in order)
        Phase 2a: Execute safe tools in parallel (asyncio.gather)
        Phase 2b: Execute unsafe tools sequentially
        Phase 3: Process results in original order
        Phase 4: _post_tool_hook
        """
        if self.function_call_stack is None:
            self.function_call_stack = []

        # Phase 0: pre-tool hook (context overflow + repetition detection)
        if self._pre_tool_hook is not None:
            messages = self._current_messages or []
            skip = await self._pre_tool_hook(messages, function_calls)
            if skip:
                # Hook requested to skip this tool batch (e.g. injected a strategy-change message)
                return

        # Phase 1: Emit started events for all function calls
        _agent = self._agent_ref() if self._agent_ref is not None else None
        for function_call in function_calls:

            # --- Lifecycle: tool start ---
            if _agent is not None and hasattr(_agent, '_run_hooks') and _agent._run_hooks is not None:
                await _agent._run_hooks.on_tool_start(
                    agent=_agent,
                    tool_name=function_call.function.name,
                    tool_call_id=function_call.call_id or "",
                    tool_args=function_call.arguments,
                )

            yield ModelResponse(
                content=function_call.get_call_str(),
                tool_call={
                    "role": tool_role,
                    "tool_call_id": function_call.call_id,
                    "tool_name": function_call.function.name,
                    "tool_args": function_call.arguments,
                },
                event=ModelResponseEvent.tool_call_started.value,
            )

        # Phase 2: Concurrency-split execution
        # -----------------------------------------------------------------
        # Split into safe (read-only, parallel-ok) vs unsafe (write/shell, serial).
        # Maintains original ordering so Phase 3 can index by position.
        # -----------------------------------------------------------------
        _SHELL_TOOL_NAMES = {"execute", "bash", "shell", "run_command"}
        timers = [Timer() for _ in function_calls]
        exceptions: List[Optional[BaseException]] = [None] * len(function_calls)
        results: List[bool] = [False] * len(function_calls)

        safe_indices   = [i for i, fc in enumerate(function_calls) if fc.function.concurrency_safe]
        unsafe_indices = [i for i, fc in enumerate(function_calls) if not fc.function.concurrency_safe]

        # Default timeout for tool execution (seconds)
        _DEFAULT_TOOL_TIMEOUT = 120

        # Phase 2a: run safe tools in parallel
        async def _execute_safe(idx: int, fc: FunctionCall) -> None:
            timers[idx].start()
            try:
                if fc.function.manages_own_timeout:
                    results[idx] = await fc.execute()
                else:
                    _timeout = fc.function.timeout or _DEFAULT_TOOL_TIMEOUT
                    results[idx] = await asyncio.wait_for(fc.execute(), timeout=_timeout)
            except asyncio.TimeoutError:
                _timeout = fc.function.timeout or _DEFAULT_TOOL_TIMEOUT
                exceptions[idx] = TimeoutError(
                    f"Tool '{fc.function.name}' timed out after {_timeout}s"
                )
                results[idx] = False
            except ToolCallException as tce:
                exceptions[idx] = tce
                results[idx] = False
            except Exception as exc:
                exceptions[idx] = exc
                results[idx] = False
            finally:
                timers[idx].stop()

        if safe_indices:
            await asyncio.gather(
                *[_execute_safe(i, function_calls[i]) for i in safe_indices],
                return_exceptions=False,
            )

        # Phase 2b: run unsafe tools serially; bash error → cancel rest
        bash_errored = False
        for idx in unsafe_indices:
            fc = function_calls[idx]
            if bash_errored:
                # Sibling-error cancellation (mirrors CC's siblingAbortController)
                exceptions[idx] = RuntimeError(
                    f"Cancelled: sibling bash/execute tool errored"
                )
                results[idx] = False
                timers[idx].start()
                timers[idx].stop()
                continue
            timers[idx].start()
            try:
                if fc.function.manages_own_timeout:
                    results[idx] = await fc.execute()
                else:
                    _timeout = fc.function.timeout or _DEFAULT_TOOL_TIMEOUT
                    results[idx] = await asyncio.wait_for(fc.execute(), timeout=_timeout)
            except asyncio.TimeoutError:
                exceptions[idx] = TimeoutError(
                    f"Tool '{fc.function.name}' timed out after {fc.function.timeout or _DEFAULT_TOOL_TIMEOUT}s"
                )
                results[idx] = False
                if fc.function.name in _SHELL_TOOL_NAMES:
                    bash_errored = True
            except ToolCallException as tce:
                exceptions[idx] = tce
                results[idx] = False
                if fc.function.name in _SHELL_TOOL_NAMES:
                    bash_errored = True
            except Exception as exc:
                exceptions[idx] = exc
                results[idx] = False
                if fc.function.name in _SHELL_TOOL_NAMES:
                    bash_errored = True
            finally:
                timers[idx].stop()

        # Phase 3: Process results in original order
        for i, function_call in enumerate(function_calls):
            function_call_success = results[i] if not isinstance(results[i], Exception) else False
            stop_execution_after_tool_call = False
            additional_messages_from_function_call = []

            # Handle exceptions captured during execution
            exc = exceptions[i]
            if exc is not None:
                if isinstance(exc, ToolCallException):
                    tce = exc
                    if tce.user_message is not None:
                        if isinstance(tce.user_message, str):
                            additional_messages_from_function_call.append(Message(role="user", content=tce.user_message))
                        else:
                            additional_messages_from_function_call.append(tce.user_message)
                    if tce.agent_message is not None:
                        if isinstance(tce.agent_message, str):
                            additional_messages_from_function_call.append(
                                Message(role="assistant", content=tce.agent_message)
                            )
                        else:
                            additional_messages_from_function_call.append(tce.agent_message)
                    if tce.messages is not None and len(tce.messages) > 0:
                        for m in tce.messages:
                            if isinstance(m, Message):
                                additional_messages_from_function_call.append(m)
                            elif isinstance(m, dict):
                                try:
                                    additional_messages_from_function_call.append(Message(**m))
                                except Exception as e:
                                    logger.warning(f"Failed to convert dict to Message: {e}")
                    if tce.stop_execution:
                        stop_execution_after_tool_call = True
                        if len(additional_messages_from_function_call) > 0:
                            for m in additional_messages_from_function_call:
                                m.stop_after_tool_call = True
                else:
                    # Generic exception — treat as tool failure
                    function_call.error = str(exc)
                    logger.warning(f"Tool {function_call.function.name} failed: {exc}")

            function_call_output: Optional[Union[List[Any], str]] = ""
            if isinstance(function_call.result, (GeneratorType, collections.abc.Iterator)):
                for item in function_call.result:
                    function_call_output += str(item)
                    if function_call.function.show_result:
                        yield ModelResponse(content=str(item))
            else:
                function_call_output = function_call.result
                # Ensure output is str or list for Message.content validation
                if function_call_output is not None and not isinstance(function_call_output, (str, list)):
                    function_call_output = str(function_call_output)
                if function_call.function.show_result:
                    yield ModelResponse(content=function_call_output)

            # --- Layer 1: per-tool large result persistence ---
            # Persist to ~/.agentica/projects/<project-hash>/<session-id>/tool-results/
            if (
                function_call_success
                and isinstance(function_call_output, str)
                and function_call.function.max_result_size_chars is not None
            ):
                try:
                    from agentica.compression.tool_result_storage import maybe_persist_result
                    _agent = self._agent_ref() if self._agent_ref else None
                    _sid = getattr(_agent, 'run_id', 'default') if _agent else 'default'
                    function_call_output = maybe_persist_result(
                        tool_name=function_call.function.name,
                        tool_use_id=function_call.call_id or f"call_{i}",
                        content=function_call_output,
                        session_id=_sid,
                        max_result_size_chars=function_call.function.max_result_size_chars,
                    )
                except Exception as _persist_err:
                    logger.debug(f"Tool result persistence skipped: {_persist_err}")

            function_call_result = Message(
                role=tool_role,
                content=function_call_output if function_call_success else function_call.error,
                tool_call_id=function_call.call_id,
                tool_name=function_call.function.name,
                tool_args=function_call.arguments,
                tool_call_error=not function_call_success,
                stop_after_tool_call=function_call.function.stop_after_tool_call or stop_execution_after_tool_call,
                metrics={"time": timers[i].elapsed},
            )

            yield ModelResponse(
                content=f"{function_call.get_call_str()} completed in {timers[i].elapsed:.4f}s.",
                tool_call=function_call_result.model_dump(
                    include={
                        "content",
                        "tool_call_id",
                        "tool_name",
                        "tool_args",
                        "tool_call_error",
                        "metrics",
                        "created_at",
                    }
                ),
                event=ModelResponseEvent.tool_call_completed.value,
            )

            # --- Lifecycle: tool end ---
            if _agent is not None and hasattr(_agent, '_run_hooks') and _agent._run_hooks is not None:
                await _agent._run_hooks.on_tool_end(
                    agent=_agent,
                    tool_name=function_call.function.name,
                    tool_call_id=function_call.call_id or "",
                    tool_args=function_call.arguments,
                    result=function_call_output if function_call_success else function_call.error,
                    is_error=not function_call_success,
                    elapsed=timers[i].elapsed,
                )

            if "tool_call_times" not in self.metrics:
                self.metrics["tool_call_times"] = {}
            if function_call.function.name not in self.metrics["tool_call_times"]:
                self.metrics["tool_call_times"][function_call.function.name] = []
            self.metrics["tool_call_times"][function_call.function.name].append(timers[i].elapsed)

            function_call_results.append(function_call_result)
            if len(additional_messages_from_function_call) > 0:
                function_call_results.extend(additional_messages_from_function_call)
            self.function_call_stack.append(function_call)

        # Check tool_call_limit after processing all results in the current batch.
        # Moving this outside the loop ensures every tool_call_id from the assistant
        # message gets a corresponding tool result message (required by OpenAI API).
        if self.tool_call_limit and len(self.function_call_stack) >= self.tool_call_limit:
            self.deactivate_function_calls()

        # --- Layer 2: per-message budget enforcement ---
        # If the total tool results in this batch exceed the budget, persist the
        # largest ones to disk until under budget.
        try:
            from agentica.compression.tool_result_storage import enforce_tool_result_budget
            _agent = self._agent_ref() if self._agent_ref else None
            _sid = getattr(_agent, 'run_id', 'default') if _agent else 'default'
            enforce_tool_result_budget(
                tool_results=function_call_results,
                session_id=_sid,
            )
        except Exception as _budget_err:
            logger.warning(f"Tool result budget enforcement failed: {_budget_err}")

        # Phase 4: post-tool hook (optional reflection / summary injection)
        if self._post_tool_hook is not None:
            messages = self._current_messages or []
            await self._post_tool_hook(messages, function_call_results)

    async def _maybe_compress_messages(self, messages: List[Message]) -> None:
        """Run the three-layer compression pipeline before each LLM call.

        Layer 1 — Micro-compact (always, zero cost):
            Truncate old tool-result content to a short placeholder.
            Mirrors CC's microcompactMessages() time-based path.

        Layer 2 — Auto-compact (token-threshold, LLM summarisation):
            If the ConpressionManager is configured AND the context is above
            the trigger threshold, LLM-summarise the conversation.
            Mirrors CC's autoCompactIfNeeded().

        Layer 3 — Reactive compact (prompt_too_long guard):
            Handled separately in agentic_loop() / _call_with_retry() via the
            CompressionManager.auto_compact() circuit-breaker path.
        """
        # ------------------------------------------------------------------
        # Layer 1: micro-compact (every turn, free)
        # ------------------------------------------------------------------
        try:
            from agentica.compression.micro import micro_compact
            n = micro_compact(messages)
            if n:
                logger.debug(f"Micro-compact: cleared {n} old tool result(s)")
        except Exception as _mc_err:
            logger.debug(f"Micro-compact skipped: {_mc_err}")

        # ------------------------------------------------------------------
        # Layer 2: auto-compact via CompressionManager (token-threshold)
        # ------------------------------------------------------------------
        agent = self._agent_ref() if self._agent_ref else None
        if agent is None:
            return
        tool_config = getattr(agent, 'tool_config', None)
        if tool_config is None or not getattr(tool_config, 'compress_tool_results', False):
            return
        cm = getattr(tool_config, 'compression_manager', None)
        if cm is None:
            return

        # Helper: fire compact hooks on the agent
        async def _fire_compact_hooks(event: str) -> None:
            _hooks = getattr(agent, '_run_hooks', None)
            if _hooks is not None:
                fn = getattr(_hooks, event, None)
                if fn is not None:
                    await fn(agent=agent, messages=messages)

        # Try auto_compact first (token-based, with circuit-breaker)
        _auto_compact = getattr(cm, 'auto_compact', None)
        if _auto_compact is not None:
            try:
                if cm._should_auto_compact(messages, model=self):
                    await _fire_compact_hooks('on_pre_compact')
                compacted = await _auto_compact(messages, model=self)
                if compacted:
                    logger.info("Auto-compact triggered: context compressed")
                    await _fire_compact_hooks('on_post_compact')
                    return  # messages replaced — skip rule-based compress
            except Exception as _ac_err:
                logger.warning(f"Auto-compact failed: {_ac_err}")

        # Fallback: rule-based compress (truncate + drop old rounds)
        if cm.should_compress(messages, tools=self.tools, model=self):
            await _fire_compact_hooks('on_pre_compact')
            logger.info("Compressing tool results to reduce context size")
            await cm.compress(messages, tools=self.tools, model=self)
            await _fire_compact_hooks('on_post_compact')


    # ------------------------------------------------------------------
    # Agentic Loop: shared safety helpers
    # ------------------------------------------------------------------

    def _check_agent_cancelled(self, agent: Any) -> None:
        """Check if the agent has been cancelled. Raises AgentCancelledError."""
        if agent is not None and getattr(agent, '_cancelled', False):
            agent._cancelled = False
            raise RuntimeError("Agent run cancelled by user")

    def _check_death_spiral(self, messages: List[Message], state: "LoopState") -> bool:
        """Check if all recent tool results are errors. Returns True to stop."""
        recent_tool_results: List[Message] = []
        for m in reversed(messages):
            if m.role == "tool" and hasattr(m, 'tool_call_error'):
                recent_tool_results.append(m)
            elif m.role == "assistant":
                break
        if recent_tool_results and all(
            getattr(m, 'tool_call_error', False) for m in recent_tool_results
        ):
            state.consecutive_all_error_turns += 1
        else:
            state.consecutive_all_error_turns = 0

        if state.consecutive_all_error_turns >= state.death_spiral_threshold:
            logger.warning(
                f"[DeathSpiral] {state.consecutive_all_error_turns} consecutive turns "
                f"with ALL tool calls failing -- stopping to prevent infinite error loop."
            )
            return True
        return False

    def _check_cost_budget(self) -> Optional[str]:
        """Check cost budget. Returns warning message string if exceeded, None otherwise."""
        _max_cost = getattr(self, '_max_cost_usd', None)
        if _max_cost is not None and self._cost_tracker is not None:
            if self._cost_tracker.total_cost_usd >= _max_cost:
                logger.warning(
                    f"[BudgetExceeded] ${self._cost_tracker.total_cost_usd:.4f} >= "
                    f"${_max_cost:.4f} -- stopping run."
                )
                return (
                    f"\n\n[Warning: cost budget exceeded "
                    f"(${self._cost_tracker.total_cost_usd:.4f} >= ${_max_cost:.4f})]"
                )
        return None

    def _response_has_tool_calls(self, messages: List[Message]) -> bool:
        """Check if the last assistant message triggered tool calls (results in messages).

        Walk backwards: if we find tool-role messages before the next assistant message,
        it means tools were executed and the loop should continue.
        """
        for m in reversed(messages):
            if m.role == "tool":
                return True
            if m.role == "assistant":
                return bool(m.tool_calls)
        return False

    async def _try_reactive_compact(self, messages: List[Message]) -> bool:
        """Attempt emergency compression on prompt_too_long. Returns True if compacted."""
        agent = self._agent_ref() if self._agent_ref else None
        cm = None
        if agent is not None:
            tc = getattr(agent, 'tool_config', None)
            if tc is not None:
                cm = getattr(tc, 'compression_manager', None)
        _auto_compact_fn = getattr(cm, 'auto_compact', None) if cm else None
        if _auto_compact_fn is not None:
            try:
                compacted = await _auto_compact_fn(messages, model=self, force=True)
                if compacted:
                    logger.info("Reactive compact triggered (prompt_too_long) -- retrying")
                    return True
            except Exception as _rc_err:
                logger.warning(f"Reactive compact failed: {_rc_err}")
        return False

    async def _call_with_retry(
        self, messages: List[Message], state: "LoopState"
    ) -> ModelResponse:
        """Call self.response() with retry and reactive compact.

        Sets _in_agentic_loop=True so provider response() does NOT recurse.
        """
        import random as _random

        self._in_agentic_loop = True
        try:
            for _attempt in range(state.max_api_retry):
                try:
                    return await self.response(messages=messages)
                except Exception as _exc:
                    _err = str(_exc).lower()

                    # Reactive compact: prompt_too_long -> emergency compress
                    _is_too_long = any(h in _err for h in state.PROMPT_TOO_LONG_HINTS)
                    if _is_too_long and not state.reactive_compact_done:
                        state.reactive_compact_done = True
                        if await self._try_reactive_compact(messages):
                            continue  # retry with compacted context

                    # Retryable errors: exponential back-off
                    _is_retryable = any(r in _err for r in state.RETRYABLE_SUBSTRINGS)
                    if _is_retryable and _attempt < state.max_api_retry - 1:
                        _wait = (2 ** _attempt) + _random.uniform(0.0, 1.0)
                        logger.warning(
                            f"[APIRetry] attempt {_attempt + 1}/{state.max_api_retry}, "
                            f"retrying in {_wait:.1f}s: {_exc}"
                        )
                        await asyncio.sleep(_wait)
                        continue

                    raise  # non-retryable or exhausted retries

            logger.error(f"[APIRetry] All {state.max_api_retry} attempts failed")
            raise RuntimeError(f"LLM API call failed after {state.max_api_retry} retries")
        finally:
            self._in_agentic_loop = False

    # ------------------------------------------------------------------
    # Agentic Loop: iterative while-loop (replaces recursive
    # handle_post_tool_call_messages / handle_post_tool_call_messages_stream)
    # ------------------------------------------------------------------

    async def agentic_loop(
        self,
        messages: List[Message],
        model_response: ModelResponse,
    ) -> ModelResponse:
        """Iterative agentic tool loop (non-streaming).

        Called by provider response() when tool calls are detected.
        Uses while-loop instead of recursion to avoid stack overflow.
        Unifies all safety checks: death spiral, cost budget, compression,
        max_tokens recovery, API retry with reactive compact.
        """
        from agentica.model.loop_state import LoopState

        agent = self._agent_ref() if self._agent_ref else None
        state = LoopState(
            max_tokens_recovery_limit=getattr(self, '_max_tokens_recovery', 3),
            max_api_retry=getattr(self, '_max_api_retry', 3),
            death_spiral_threshold=getattr(self, '_death_spiral_threshold', 5),
        )

        while True:
            # -- Cancellation checkpoint 1 (top of loop) --
            self._check_agent_cancelled(agent)

            # -- stop_after_tool_call --
            last_message = messages[-1]
            if last_message.stop_after_tool_call:
                logger.debug("Stopping execution as stop_after_tool_call=True")
                if (
                    last_message.role == "assistant"
                    and last_message.content is not None
                    and isinstance(last_message.content, str)
                ):
                    if model_response.content is None:
                        model_response.content = ""
                    model_response.content += last_message.content
                return model_response

            state.turn_count += 1

            # -- Death spiral detection --
            if self._check_death_spiral(messages, state):
                if model_response.content is None:
                    model_response.content = ""
                model_response.content += (
                    f"\n\n[Error: stopped after {state.consecutive_all_error_turns} consecutive "
                    f"turns of all tool calls failing. This appears to be an unrecoverable error loop.]"
                )
                return model_response

            # -- Cost budget check --
            budget_msg = self._check_cost_budget()
            if budget_msg is not None:
                if model_response.content is None:
                    model_response.content = ""
                model_response.content += budget_msg
                return model_response

            # -- Compression --
            await self._maybe_compress_messages(messages)

            # -- Cancellation checkpoint 2 (before API call) --
            self._check_agent_cancelled(agent)

            # -- API call with retry --
            response_after = await self._call_with_retry(messages, state)

            # -- max_tokens recovery --
            finish = getattr(response_after, '_finish_reason', None)
            if (
                finish == "length"
                and state.max_tokens_recovery_count < state.max_tokens_recovery_limit
            ):
                state.max_tokens_recovery_count += 1
                logger.info(
                    f"[MaxTokensRecovery] output truncated, "
                    f"continuing ({state.max_tokens_recovery_count}/{state.max_tokens_recovery_limit})"
                )
                # Merge truncated content before continuing
                if response_after.content is not None:
                    if model_response.content is None:
                        model_response.content = ""
                    model_response.content += response_after.content
                messages.append(Message(role="user", content="Continue from where you left off."))
                continue  # next iteration handles the continuation

            # -- Merge sub-response --
            if response_after.content is not None:
                if model_response.content is None:
                    model_response.content = ""
                model_response.content += response_after.content
            if response_after.parsed is not None:
                model_response.parsed = response_after.parsed
            if response_after.audio is not None:
                model_response.audio = response_after.audio

            # -- Check if response triggered more tool calls --
            if not self._response_has_tool_calls(messages):
                return model_response
            # else: continue the while loop for the next tool round

    async def agentic_loop_stream(
        self,
        messages: List[Message],
    ) -> AsyncIterator[ModelResponse]:
        """Iterative agentic tool loop (streaming).

        Shares the same safety checks as non-streaming agentic_loop().
        Previously the stream path (handle_post_tool_call_messages_stream)
        lacked max_tokens recovery and API retry — now unified.
        """
        from agentica.model.loop_state import LoopState

        agent = self._agent_ref() if self._agent_ref else None
        state = LoopState(
            death_spiral_threshold=getattr(self, '_death_spiral_threshold', 5),
        )

        while True:
            # -- Cancellation checkpoint 1 --
            self._check_agent_cancelled(agent)

            # -- stop_after_tool_call --
            last_message = messages[-1]
            if last_message.stop_after_tool_call:
                logger.debug("Stopping execution as stop_after_tool_call=True")
                if (
                    last_message.role == "assistant"
                    and last_message.content is not None
                    and isinstance(last_message.content, str)
                ):
                    yield ModelResponse(content=last_message.content)
                return

            state.turn_count += 1

            # -- Death spiral detection --
            if self._check_death_spiral(messages, state):
                yield ModelResponse(
                    content=f"\n\n[Error: stopped after {state.consecutive_all_error_turns} "
                    f"consecutive turns of all tool calls failing. "
                    f"Unrecoverable error loop detected.]"
                )
                return

            # -- Cost budget check --
            budget_msg = self._check_cost_budget()
            if budget_msg is not None:
                yield ModelResponse(content=budget_msg)
                return

            # -- Compression --
            await self._maybe_compress_messages(messages)

            # -- Cancellation checkpoint 2 --
            self._check_agent_cancelled(agent)

            # -- Stream API call (with sentinel flag) --
            self._in_agentic_loop = True
            try:
                async for chunk in self.response_stream(messages=messages):
                    yield chunk
            finally:
                self._in_agentic_loop = False

            # -- Check if tool calls happened --
            if not self._response_has_tool_calls(messages):
                return
            # else: continue loop

    # ── Default tool call handling (OpenAI-compatible protocol) ──────────────
    # Providers using a different protocol (e.g. Anthropic) override these.

    async def handle_tool_calls(
            self,
            assistant_message: Message,
            messages: List[Message],
            model_response: ModelResponse,
            tool_role: str = "tool",
    ) -> Optional[ModelResponse]:
        """Handle tool calls in the assistant message (OpenAI-compatible default).

        Providers with a different tool-call protocol (e.g. Claude) should override.
        """
        if assistant_message.tool_calls is not None and len(assistant_message.tool_calls) > 0 and self.run_tools:
            self._current_messages = messages
            if model_response.content is None:
                model_response.content = ""
            function_call_results: List[Message] = []
            function_calls_to_run: List[FunctionCall] = []
            for tool_call in assistant_message.tool_calls:
                _tool_call_id = tool_call.get("id")
                _function_call = get_function_call_for_tool_call(tool_call, self.functions)
                if _function_call is None:
                    messages.append(
                        Message(role=tool_role, tool_call_id=_tool_call_id, content="Could not find function to call.")
                    )
                    continue
                if _function_call.error is not None:
                    messages.append(
                        Message(role=tool_role, tool_call_id=_tool_call_id, content=_function_call.error)
                    )
                    continue
                function_calls_to_run.append(_function_call)

            async for tool_response in self.run_function_calls(
                    function_calls=function_calls_to_run, function_call_results=function_call_results,
                    tool_role=tool_role
            ):
                pass

            if len(function_call_results) > 0:
                messages.extend(function_call_results)

            return model_response
        return None

    async def handle_stream_tool_calls(
            self,
            assistant_message: Message,
            messages: List[Message],
            tool_role: str = "tool",
    ) -> AsyncIterator[ModelResponse]:
        """Handle tool calls for response stream (OpenAI-compatible default).

        Providers with a different tool-call protocol (e.g. Claude, Ollama) should override.
        """
        if assistant_message.tool_calls is not None and len(assistant_message.tool_calls) > 0 and self.run_tools:
            self._current_messages = messages
            function_calls_to_run: List[FunctionCall] = []
            function_call_results: List[Message] = []
            for tool_call in assistant_message.tool_calls:
                _tool_call_id = tool_call.get("id")
                _function_call = get_function_call_for_tool_call(tool_call, self.functions)
                if _function_call is None:
                    messages.append(
                        Message(role=tool_role, tool_call_id=_tool_call_id, content="Could not find function to call.")
                    )
                    continue
                if _function_call.error is not None:
                    messages.append(
                        Message(role=tool_role, tool_call_id=_tool_call_id, content=_function_call.error)
                    )
                    continue
                function_calls_to_run.append(_function_call)

            async for function_call_response in self.run_function_calls(
                    function_calls=function_calls_to_run, function_call_results=function_call_results,
                    tool_role=tool_role
            ):
                yield function_call_response

            if len(function_call_results) > 0:
                messages.extend(function_call_results)

    # ── Default usage metrics update (shared across providers) ───────────────

    def update_usage_metrics(
            self, assistant_message: Message, metrics: Metrics, response_usage: Optional[Any]
    ) -> None:
        """Update usage metrics from a non-streaming response.

        Default implementation handles OpenAI-style CompletionUsage.
        Providers with different usage formats (Anthropic, Ollama) override this.
        """
        assistant_message.metrics["time"] = metrics.response_timer.elapsed
        self.metrics.setdefault("response_times", []).append(metrics.response_timer.elapsed)
        if response_usage:
            prompt_tokens = getattr(response_usage, 'prompt_tokens', None) or response_usage.get("prompt_eval_count", 0) if isinstance(response_usage, dict) else getattr(response_usage, 'prompt_tokens', 0)
            completion_tokens = getattr(response_usage, 'completion_tokens', None) or response_usage.get("eval_count", 0) if isinstance(response_usage, dict) else getattr(response_usage, 'completion_tokens', 0)
            total_tokens = getattr(response_usage, 'total_tokens', None) or (prompt_tokens + completion_tokens)

            if prompt_tokens:
                metrics.input_tokens = prompt_tokens
                metrics.prompt_tokens = prompt_tokens
                assistant_message.metrics["input_tokens"] = prompt_tokens
                assistant_message.metrics["prompt_tokens"] = prompt_tokens
                self.metrics["input_tokens"] = self.metrics.get("input_tokens", 0) + prompt_tokens
                self.metrics["prompt_tokens"] = self.metrics.get("prompt_tokens", 0) + prompt_tokens
            if completion_tokens:
                metrics.output_tokens = completion_tokens
                metrics.completion_tokens = completion_tokens
                assistant_message.metrics["output_tokens"] = completion_tokens
                assistant_message.metrics["completion_tokens"] = completion_tokens
                self.metrics["output_tokens"] = self.metrics.get("output_tokens", 0) + completion_tokens
                self.metrics["completion_tokens"] = self.metrics.get("completion_tokens", 0) + completion_tokens
            if total_tokens:
                metrics.total_tokens = total_tokens
                assistant_message.metrics["total_tokens"] = total_tokens
                self.metrics["total_tokens"] = self.metrics.get("total_tokens", 0) + total_tokens

            entry = RequestUsage(
                input_tokens=metrics.input_tokens,
                output_tokens=metrics.output_tokens,
                total_tokens=metrics.total_tokens,
                response_time=metrics.response_timer.elapsed,
            )

            # Parse prompt_tokens_details
            prompt_details = getattr(response_usage, 'prompt_tokens_details', None)
            if prompt_details is not None:
                from pydantic import BaseModel as PydanticBaseModel
                if isinstance(prompt_details, dict):
                    metrics.prompt_tokens_details = prompt_details
                elif isinstance(prompt_details, PydanticBaseModel):
                    metrics.prompt_tokens_details = prompt_details.model_dump(exclude_none=True)
                assistant_message.metrics["prompt_tokens_details"] = metrics.prompt_tokens_details
                if metrics.prompt_tokens_details is not None:
                    entry.input_tokens_details = TokenDetails(
                        cached_tokens=metrics.prompt_tokens_details.get("cached_tokens", 0),
                    )
                    if "prompt_tokens_details" not in self.metrics:
                        self.metrics["prompt_tokens_details"] = {}
                    for k, v in metrics.prompt_tokens_details.items():
                        self.metrics["prompt_tokens_details"][k] = self.metrics["prompt_tokens_details"].get(k, 0) + v

            # Parse completion_tokens_details
            completion_details = getattr(response_usage, 'completion_tokens_details', None)
            if completion_details is not None:
                from pydantic import BaseModel as PydanticBaseModel
                if isinstance(completion_details, dict):
                    metrics.completion_tokens_details = completion_details
                elif isinstance(completion_details, PydanticBaseModel):
                    metrics.completion_tokens_details = completion_details.model_dump(exclude_none=True)
                assistant_message.metrics["completion_tokens_details"] = metrics.completion_tokens_details
                if metrics.completion_tokens_details is not None:
                    entry.output_tokens_details = TokenDetails(
                        reasoning_tokens=metrics.completion_tokens_details.get("reasoning_tokens", 0),
                    )
                    if "completion_tokens_details" not in self.metrics:
                        self.metrics["completion_tokens_details"] = {}
                    for k, v in metrics.completion_tokens_details.items():
                        self.metrics["completion_tokens_details"][k] = self.metrics["completion_tokens_details"].get(k, 0) + v

            self.usage.add(entry)

            # Cost tracking (v3): record USD cost for this invoke()
            if self._cost_tracker is not None:
                cache_read = 0
                prompt_details_dict = metrics.prompt_tokens_details or {}
                if isinstance(prompt_details_dict, dict):
                    cache_read = prompt_details_dict.get("cached_tokens", 0)
                self._cost_tracker.record(
                    model_id=self.id,
                    input_tokens=metrics.input_tokens,
                    output_tokens=metrics.output_tokens,
                    cache_read_tokens=cache_read,
                )

    def update_stream_metrics(self, assistant_message: Message, metrics: Metrics) -> None:
        """Update usage metrics from a streaming response.

        Shared across all providers that use streaming.
        """
        assistant_message.metrics["time"] = metrics.response_timer.elapsed
        self.metrics.setdefault("response_times", []).append(metrics.response_timer.elapsed)

        if metrics.time_to_first_token is not None:
            assistant_message.metrics["time_to_first_token"] = metrics.time_to_first_token
            self.metrics.setdefault("time_to_first_token", []).append(metrics.time_to_first_token)

        if metrics.input_tokens:
            assistant_message.metrics["input_tokens"] = metrics.input_tokens
            self.metrics["input_tokens"] = self.metrics.get("input_tokens", 0) + metrics.input_tokens
        if metrics.output_tokens:
            assistant_message.metrics["output_tokens"] = metrics.output_tokens
            self.metrics["output_tokens"] = self.metrics.get("output_tokens", 0) + metrics.output_tokens
        if metrics.prompt_tokens:
            assistant_message.metrics["prompt_tokens"] = metrics.prompt_tokens
            self.metrics["prompt_tokens"] = self.metrics.get("prompt_tokens", 0) + metrics.prompt_tokens
        if metrics.completion_tokens:
            assistant_message.metrics["completion_tokens"] = metrics.completion_tokens
            self.metrics["completion_tokens"] = self.metrics.get("completion_tokens", 0) + metrics.completion_tokens
        if metrics.total_tokens:
            assistant_message.metrics["total_tokens"] = metrics.total_tokens
            self.metrics["total_tokens"] = self.metrics.get("total_tokens", 0) + metrics.total_tokens

        entry = RequestUsage(
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            total_tokens=metrics.total_tokens,
            response_time=metrics.response_timer.elapsed,
        )
        if metrics.prompt_tokens_details is not None:
            assistant_message.metrics["prompt_tokens_details"] = metrics.prompt_tokens_details
            entry.input_tokens_details = TokenDetails(
                cached_tokens=metrics.prompt_tokens_details.get("cached_tokens", 0),
            )
            if "prompt_tokens_details" not in self.metrics:
                self.metrics["prompt_tokens_details"] = {}
            for k, v in metrics.prompt_tokens_details.items():
                self.metrics["prompt_tokens_details"][k] = self.metrics["prompt_tokens_details"].get(k, 0) + v
        if metrics.completion_tokens_details is not None:
            assistant_message.metrics["completion_tokens_details"] = metrics.completion_tokens_details
            entry.output_tokens_details = TokenDetails(
                reasoning_tokens=metrics.completion_tokens_details.get("reasoning_tokens", 0),
            )
            if "completion_tokens_details" not in self.metrics:
                self.metrics["completion_tokens_details"] = {}
            for k, v in metrics.completion_tokens_details.items():
                self.metrics["completion_tokens_details"][k] = self.metrics["completion_tokens_details"].get(k, 0) + v
        self.usage.add(entry)

        # Cost tracking (v3): record USD cost for this streaming invoke()
        if self._cost_tracker is not None:
            cache_read = 0
            if metrics.prompt_tokens_details is not None:
                cache_read = metrics.prompt_tokens_details.get("cached_tokens", 0)
            self._cost_tracker.record(
                model_id=self.id,
                input_tokens=metrics.input_tokens,
                output_tokens=metrics.output_tokens,
                cache_read_tokens=cache_read,
            )

    def _process_string_image(self, image: str) -> Dict[str, Any]:
        """Process string-based image (base64, URL, or file path)."""

        # Process Base64 encoded image
        if image.startswith("data:image"):
            return {"type": "image_url", "image_url": {"url": image}}

        # Process URL image
        if image.startswith(("http://", "https://")):
            return {"type": "image_url", "image_url": {"url": image}}

        # Process local file image
        import mimetypes
        from pathlib import Path

        path = Path(image)
        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {image}")

        mime_type = mimetypes.guess_type(image)[0] or "image/jpeg"
        with open(path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode("utf-8")
            image_url = f"data:{mime_type};base64,{base64_image}"
            return {"type": "image_url", "image_url": {"url": image_url}}

    def _process_pil_image(self, image: 'PIL.Image.Image') -> Dict[str, Any]:
        """Process PIL Image data."""
        # Convert image to bytes
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()

        # Convert to base64
        base64_image = base64.b64encode(img_byte_arr).decode('utf-8')
        image_url = f"data:image/png;base64,{base64_image}"
        return {"type": "image_url", "image_url": {"url": image_url}}

    def _process_bytes_image(self, image: bytes) -> Dict[str, Any]:
        """Process bytes image data."""
        base64_image = base64.b64encode(image).decode("utf-8")
        image_url = f"data:image/jpeg;base64,{base64_image}"
        return {"type": "image_url", "image_url": {"url": image_url}}

    def process_image(self, image: Any) -> Optional[Dict[str, Any]]:
        """Process an image based on the format."""
        from PIL.Image import Image as PILImage
        if isinstance(image, dict):
            return {"type": "image_url", "image_url": image}

        if isinstance(image, str):
            return self._process_string_image(image)

        if isinstance(image, bytes):
            return self._process_bytes_image(image)

        if isinstance(image, PILImage):
            return self._process_pil_image(image)

        logger.warning(f"Unsupported image type: {type(image)}")
        return None

    def add_images_to_message(self, message: Message, images: Optional[Sequence[Any]] = None) -> Message:
        """
        Add images to a message for the model. By default, we use the OpenAI image format but other Models
        can override this method to use a different image format.
        Args:
            message: The message for the Model
            images: Sequence of images in various formats:
                - str: base64 encoded image, URL, or file path
                - Dict: pre-formatted image data
                - bytes: raw image data

        Returns:
            Message content with images added in the format expected by the model
        """
        # If no images are provided, return the message as is
        if images is None or len(images) == 0:
            return message

        # Ignore non-string message content
        # because we assume that the images/audio are already added to the message
        if not isinstance(message.content, str):
            return message

        # Create a default message content with text
        message_content_with_image: List[Dict[str, Any]] = [{"type": "text", "text": message.content}]

        # Add images to the message content
        for image in images:
            try:
                image_data = self.process_image(image)
                if image_data:
                    message_content_with_image.append(image_data)
            except Exception as e:
                logger.error(f"Failed to process image: {str(e)}")
                continue

        # Update the message content with the images
        message.content = message_content_with_image
        return message

    def add_audio_to_message(self, message: Message, audio: Optional[Any] = None) -> Message:
        """
        Add audio to a message for the model. By default, we use the OpenAI audio format but other Models
        can override this method to use a different audio format.
        Args:
            message: The message for the Model
            audio: Pre-formatted audio data like {
                        "data": encoded_string,
                        "format": "wav"
                    }

        Returns:
            Message content with audio added in the format expected by the model
        """
        if audio is None:
            return message

        # If `id` is in the audio, this means the audio is already processed
        # This is used in multi-turn conversations
        if "id" in audio:
            message.content = ""
            message.audio = {"id": audio["id"]}
        # If `data` is in the audio, this means the audio is raw data
        # And an input audio
        elif "data" in audio:
            # Create a message with audio
            message.content = [
                {"type": "text", "text": message.content},
                {"type": "input_audio", "input_audio": audio},
            ]
        return message

    def get_system_message_for_model(self) -> Optional[str]:
        return self.system_prompt

    def get_instructions_for_model(self) -> Optional[List[str]]:
        return self.instructions

    def clear(self) -> None:
        """Clears the Model's state."""
        self.metrics = {}
        self.usage = Usage()
        self.functions = None
        self.function_call_stack = None
        self.session_id = None
        self._in_agentic_loop = False
