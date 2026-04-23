# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: E2B remote sandbox execution tool.

Tool-level integration with the E2B cloud code-interpreter. Lets an Agent run
Python code or shell commands inside an isolated remote sandbox, without
mutating ``Runner`` or pulling in a full sandbox-runtime abstraction.

Install: ``pip install agentica[e2b]``
Auth:    set ``E2B_API_KEY`` (or pass ``api_key=`` explicitly).

Two callables are registered:
  * ``run_python(code, timeout=None)`` — executes Python in the sandbox kernel
    and returns stdout + main-result text + error traceback.
  * ``execute(command, timeout=None)`` — runs a shell command in the sandbox
    and returns combined stdout/stderr with the exit code.

The remote :class:`Sandbox` is created lazily on first call and reused across
calls. Use :meth:`E2BExecuteTool.close` (or rely on ``__del__``) to terminate
the remote VM.
"""
import asyncio
import os
from typing import Any, Optional

from agentica.tools.base import Tool
from agentica.utils.log import logger

_DEFAULT_TIMEOUT = 120
_DEFAULT_MAX_OUTPUT_LENGTH = 20_000
_INSTALL_HINT = (
    "E2B sandbox SDK is not installed. "
    "Install with `pip install agentica[e2b]` (pulls in `e2b_code_interpreter`)."
)


def _load_sandbox_class() -> Any:
    """Import :class:`e2b_code_interpreter.Sandbox` lazily.

    Isolated as a module-level function so tests can patch the import path
    without monkey-patching ``importlib``.
    """
    from e2b_code_interpreter import Sandbox  # type: ignore[import-not-found]
    return Sandbox


def _join_logs(parts: Any) -> str:
    if not parts:
        return ""
    if isinstance(parts, (list, tuple)):
        return "".join(str(p) for p in parts)
    return str(parts)


class E2BExecuteTool(Tool):
    """Execute Python code or shell commands inside an E2B remote sandbox.

    Lifecycle: a single :class:`Sandbox` instance is created on first call
    and reused for the tool's lifetime. Call :meth:`close` to release the
    remote VM early; otherwise it is killed when the tool object is GC'd.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        template: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
        max_output_length: int = _DEFAULT_MAX_OUTPUT_LENGTH,
        sandbox_timeout: Optional[int] = None,
    ):
        """
        Args:
            api_key: E2B API key. Falls back to ``E2B_API_KEY`` env var.
            template: Optional sandbox template id (defaults to E2B's
                code-interpreter image).
            timeout: Per-call wall clock for ``run_python`` / ``execute``.
            max_output_length: Truncate combined output beyond this many chars.
            sandbox_timeout: Sandbox VM lifetime in seconds. ``None`` uses
                E2B's default (typically 5 minutes).
        """
        super().__init__(name="e2b_execute_tool")
        self.api_key = api_key or os.environ.get("E2B_API_KEY")
        self.template = template
        self.timeout = timeout
        self.max_output_length = max_output_length
        self.sandbox_timeout = sandbox_timeout
        self._sandbox: Optional[Any] = None

        self.register(self.run_python, is_destructive=True)
        self.register(self.execute, is_destructive=True)

    # ------------------------------------------------------------------
    # Sandbox lifecycle
    # ------------------------------------------------------------------

    def _get_sandbox(self) -> Any:
        """Lazily instantiate (or reuse) the remote sandbox."""
        if self._sandbox is not None:
            return self._sandbox
        try:
            sandbox_cls = _load_sandbox_class()
        except ImportError as exc:
            raise ImportError(_INSTALL_HINT) from exc

        kwargs: dict = {"api_key": self.api_key}
        if self.template is not None:
            kwargs["template"] = self.template
        if self.sandbox_timeout is not None:
            kwargs["timeout"] = self.sandbox_timeout

        logger.debug(f"E2BExecuteTool: creating sandbox (template={self.template})")
        self._sandbox = sandbox_cls(**kwargs)
        return self._sandbox

    def close(self) -> None:
        """Terminate the remote sandbox if one exists."""
        if self._sandbox is None:
            return
        try:
            self._sandbox.kill()
        except Exception as exc:  # noqa: BLE001 — remote VM may already be gone
            logger.warning(f"E2BExecuteTool.close: kill failed ({exc})")
        finally:
            self._sandbox = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Tool-exposed callables
    # ------------------------------------------------------------------

    async def run_python(self, code: str, timeout: Optional[int] = None) -> str:
        """Run Python code inside the E2B sandbox kernel.

        Use this for stateful interactive computation (matplotlib, pandas,
        intermediate variables persist across calls within the same
        ``E2BExecuteTool`` instance).

        Args:
            code: Python source to execute.
            timeout: Per-call timeout (seconds). Defaults to the tool's
                configured timeout.

        Returns:
            Concatenation of stdout, stderr, the main-result text (the value
            of the last expression), and any execution error traceback.
        """
        effective_timeout = timeout if timeout is not None else self.timeout
        sandbox = self._get_sandbox()

        execution = await asyncio.to_thread(
            sandbox.run_code,
            code,
            timeout=float(effective_timeout) if effective_timeout else None,
        )

        parts: list[str] = []
        stdout = _join_logs(execution.logs.stdout)
        stderr = _join_logs(execution.logs.stderr)
        if stdout:
            parts.append(stdout.rstrip("\n"))
        if stderr:
            parts.append(f"[stderr]\n{stderr.rstrip(chr(10))}")
        if execution.text:
            parts.append(f"[result]\n{execution.text}")
        if execution.error is not None:
            err = execution.error
            parts.append(
                f"[error] {err.name}: {err.value}\n{err.traceback}".rstrip()
            )

        output = "\n".join(p for p in parts if p).strip()
        return self._truncate(output) or "(no output)"

    async def execute(self, command: str, timeout: Optional[int] = None) -> str:
        """Run a shell command inside the E2B sandbox.

        Use this for non-Python tasks (apt, curl, git, build commands, etc.).

        Args:
            command: Shell command to execute.
            timeout: Per-call timeout (seconds). Defaults to the tool's
                configured timeout.

        Returns:
            Combined stdout/stderr with the exit code appended on non-zero.
        """
        effective_timeout = timeout if timeout is not None else self.timeout
        sandbox = self._get_sandbox()

        result = await asyncio.to_thread(
            sandbox.commands.run,
            command,
            timeout=float(effective_timeout) if effective_timeout else None,
        )

        stdout = (result.stdout or "").rstrip("\n")
        stderr = (result.stderr or "").rstrip("\n")
        exit_code = int(result.exit_code or 0)

        parts: list[str] = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"[stderr]\n{stderr}")
        output = "\n".join(parts).strip()
        output = self._truncate(output)
        if exit_code != 0:
            output = f"{output}\n\n[Exit code: {exit_code}]" if output else f"[Exit code: {exit_code}]"
        return output or f"Command exited with code {exit_code}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output_length:
            return text
        return text[: self.max_output_length] + "\n... (output truncated)"


__all__ = ["E2BExecuteTool"]
