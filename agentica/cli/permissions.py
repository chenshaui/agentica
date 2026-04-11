# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: CLI permission / approval system for tool execution.

Three modes:
  - "allow-all"  : auto-approve everything (no prompts)
  - "auto"       : auto-approve read-only, prompt for write/execute
  - "strict"     : prompt for every tool call
"""
from typing import Set

# Tools that only read — never need approval in "auto" mode
READ_ONLY_TOOLS: Set[str] = frozenset({
    "ls", "read_file", "glob", "grep", "web_search", "fetch_url",
    "write_todos", "task",
})

# Tools that mutate files or run commands — need approval in "auto" mode
WRITE_TOOLS: Set[str] = frozenset({
    "write_file", "edit_file", "execute",
})


class PermissionManager:
    """Manages tool execution permissions.

    Attributes:
        mode:            "allow-all" | "auto" | "strict"
        session_allowed: tools the user has approved for the rest of this session
    """

    def __init__(self, mode: str = "auto"):
        if mode not in ("allow-all", "auto", "strict"):
            raise ValueError(f"Invalid permission mode: {mode!r}")
        self.mode = mode
        self.session_allowed: Set[str] = set()

    def needs_approval(self, tool_name: str) -> bool:
        """Return True if this tool call should be shown for user approval."""
        if self.mode == "allow-all":
            return False
        if tool_name in self.session_allowed:
            return False
        if self.mode == "auto":
            return tool_name in WRITE_TOOLS
        # strict: everything needs approval
        return True

    def prompt_user(self, console, tool_name: str, tool_args: dict) -> bool:
        """Display approval prompt and return whether user allowed.

        Returns True if allowed, False if denied.
        Also supports 'a' to allow-all for this tool for the rest of session.
        """
        console.print()
        console.print(f"[bold yellow]Permission required:[/bold yellow] {tool_name}", end="")

        # Show relevant context
        if tool_name == "execute":
            cmd = tool_args.get("command", "")
            if len(cmd) > 120:
                cmd = cmd[:117] + "..."
            console.print(f" [dim]{cmd}[/dim]")
        elif tool_name in ("write_file", "edit_file"):
            fpath = tool_args.get("file_path", "")
            console.print(f" [dim]{fpath}[/dim]")
        else:
            console.print()

        console.print("[dim]  (y)es / (n)o / (a)lways allow this tool[/dim] ", end="")

        try:
            response = input().strip().lower()
        except (KeyboardInterrupt, EOFError):
            return False

        if response in ("a", "always"):
            self.session_allowed.add(tool_name)
            return True
        return response in ("y", "yes")

    def check_and_approve(self, console, tool_name: str, tool_args: dict) -> bool:
        """Convenience: check if approval needed and prompt if so.

        Returns True if tool execution should proceed.
        """
        if not self.needs_approval(tool_name):
            return True
        return self.prompt_user(console, tool_name, tool_args)
