# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: CLI display utilities - colors, formatting, stream display manager
"""
import difflib
import json
import os
import re
from pathlib import Path
from typing import List, Optional

from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text

from agentica.cli.config import get_console, TOOL_ICONS, BUILTIN_TOOLS


# Rich console color scheme (unified - no separate ANSI codes)
COLORS = {
    "user": "bright_cyan",
    "agent": "bright_green",
    "thinking": "yellow",
    "tool": "cyan",
    "error": "red",
}


def print_header(model_provider: str, model_name: str, work_dir: Optional[str] = None,
                 extra_tools: Optional[List[str]] = None, shell_mode: bool = False):
    """Print the application header with version and model information"""
    box_width = min(get_console().width, 80)
    get_console().print("=" * box_width, style="bright_cyan")
    get_console().print("  Agentica CLI - Interactive AI Assistant")
    get_console().print(f"  Model: [bright_green]{model_provider}/{model_name}[/bright_green]")

    # Working directory
    cwd = work_dir or os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]
    if len(cwd) > 50:
        cwd = "..." + cwd[-47:]
    get_console().print(f"  Working Directory: {cwd}")

    # Built-in tools (always shown)
    get_console().print(f"  Built-in Tools: [white]{', '.join(BUILTIN_TOOLS)}[/white]")

    # Extra tools info
    if extra_tools:
        tools_str = ", ".join(extra_tools)
        if len(tools_str) > 55:
            tools_str = tools_str[:52] + "..."
        get_console().print(f"  Extra Tools: [bright_green]{tools_str}[/bright_green]")

    get_console().print("=" * box_width, style="bright_cyan")
    get_console().print()
    # Keyboard shortcuts
    get_console().print("  [bright_green]Enter[/bright_green]       Submit your message")
    get_console().print("  [bright_green]Ctrl+X[/bright_green]      Toggle Agent/Shell mode")
    get_console().print("  [bright_green]Ctrl+J[/bright_green]      Insert newline (Alt+Enter also works)")
    get_console().print("  [bright_green]Ctrl+D[/bright_green]      Exit")
    get_console().print("  [bright_green]Ctrl+C[/bright_green]      Interrupt current operation")
    get_console().print("  [bright_green]Alt+V[/bright_green]       Paste image from clipboard")
    get_console().print()
    # Input features
    get_console().print("  [bright_green]@filename[/bright_green]   Type @ to auto-complete files and inject content")
    get_console().print("  [bright_green]/paste[/bright_green]      Paste image from clipboard")
    get_console().print("  [bright_green]/image[/bright_green]      Attach local image: /image <path>")
    get_console().print("  [bright_green]/command[/bright_green]    Type / to see available commands (try /help)")
    get_console().print()


def parse_file_mentions(text: str) -> tuple[str, list[Path]]:
    """Parse @file mentions and return text with mentioned files.
    
    Uses lookbehind to avoid matching email addresses.
    """
    pattern = r"(?:^|(?<=\s))@([\w./-]+)"
    mentioned_files = []
    
    for match in re.finditer(pattern, text):
        file_path_str = match.group(1)
        file_path = Path(file_path_str).expanduser()
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        if file_path.exists() and file_path.is_file():
            mentioned_files.append(file_path)
    
    # Remove @ mentions from text for cleaner display
    processed_text = re.sub(pattern, r'\1', text)
    return processed_text, mentioned_files


def inject_file_contents(prompt_text: str, mentioned_files: list[Path]) -> str:
    """Inject file contents into the prompt."""
    if not mentioned_files:
        return prompt_text
    
    context_parts = [prompt_text, "\n\n## Referenced Files\n"]
    for file_path in mentioned_files:
        try:
            content = file_path.read_text(encoding="utf-8")
            # Limit file content to reasonable size
            if len(content) > 20000:
                content = content[:20000] + "\n... (file truncated)"
            context_parts.append(
                f"\n### {file_path.name}\nPath: `{file_path}`\n```\n{content}\n```"
            )
        except Exception as e:
            context_parts.append(f"\n### {file_path.name}\n[Error reading file: {e}]")
    
    return "\n".join(context_parts)


_PASTE_PATH_RE = re.compile(r"@\S*[\\/]pastes[\\/]paste_\S+\.txt")


def display_user_message(text: str, *, pasted_blocks: int = 0, pasted_lines: int = 0) -> None:
    """Display user message with file mentions colored.

    For long pasted content, shows a trimmed preview with line count.
    """
    cleaned = _PASTE_PATH_RE.sub("", text).strip()
    if not cleaned and pasted_blocks:
        cleaned = f"[Pasted text: {pasted_lines} lines]"

    # For very long content (from expanded paste), show a trimmed preview
    lines = cleaned.split('\n')
    if len(lines) > 5:
        # Show first 3 lines + summary
        preview_lines = lines[:3]
        preview = '\n'.join(preview_lines)
        remaining = len(lines) - 3
        rich_text = Text()
        rich_text.append(preview, style=COLORS["user"])
        rich_text.append(f"\n... (+{remaining} more lines)", style="dim")
    else:
        pattern = r"(@[\w./-]+)"
        parts = re.split(pattern, cleaned)
        rich_text = Text()
        for part in parts:
            if part.startswith("@"):
                rich_text.append(part, style="magenta")
            else:
                rich_text.append(part, style=COLORS["user"])

    if pasted_blocks:
        suffix = "s" if pasted_blocks > 1 else ""
        rich_text.append(
            f" ({pasted_blocks} pasted block{suffix}, {pasted_lines} lines total)",
            style="dim",
        )

    get_console().print(rich_text)


def get_file_completions(document_text: str) -> List[str]:
    """Get file completions for @ mentions."""
    import glob as glob_module

    # Find the @ mention being typed
    match = re.search(r"@([\w./-]*)$", document_text)
    if not match:
        return []
    
    partial = match.group(1)
    
    if partial:
        # Search for files matching the partial path (current dir only, not recursive)
        search_pattern = f"{partial}*"
        matches = glob_module.glob(search_pattern, recursive=False)
        # Also search one level of subdirectories (limited depth)
        if os.sep not in partial and "/" not in partial:
            for d in os.listdir("."):
                if os.path.isdir(d) and not d.startswith("."):
                    sub_matches = glob_module.glob(os.path.join(d, f"{partial}*"))
                    matches.extend(sub_matches[:5])
    else:
        # Show files in current directory
        matches = glob_module.glob("*")
    
    # Filter to only files (not directories) and limit results
    completions = []
    seen = set()
    for m in matches[:20]:
        if m in seen:
            continue
        seen.add(m)
        if os.path.isfile(m):
            completions.append(m)
        elif os.path.isdir(m):
            completions.append(m + "/")
    
    return completions


def show_help():
    """Display categorized help information."""
    categories = {
        "Session": {
            "/new":             "Start a new chat session",
            "/clear, /reset":   "Clear screen and reset conversation",
            "/resume [name]":   "Resume a previous session",
            "/history":         "Show conversation history",
            "/save, /export":   "Save conversation to JSON (no system prompts)",
            "/retry":           "Retry the last message (resend to agent)",
            "/undo":            "Remove the last user/assistant exchange",
            "/compact":         "Compact context (summarize history)",
            "/btw <question>":  "Ephemeral side question (no tools, not saved)",
            "/queue":           "Queue: <prompt> | list | clear | remove <n>",
            "/background":      "Run prompt in background (/bg alias)",
            "/stop":            "Kill all running background tasks",
        },
        "Configure": {
            "/model [p/m]":     "Show or switch model",
            "/config":          "Show current configuration",
            "/cost, /usage":    "Show detailed token usage and cost",
            "/debug":           "Show debug info (model, history count)",
            "/reasoning":       "Toggle reasoning display: on | off",
            "/statusbar, /sb":  "Toggle the status bar",
        },
        "Tools & Skills": {
            "/tools":           "List available tools",
            "/skills":          "Manage skills: list | install | remove | inspect | reload",
        },
        "Permissions": {
            "/permissions":     "View or set mode (allow-all/auto/strict)",
            "/yolo":            "Toggle YOLO mode (auto-approve all)",
        },
        "Media": {
            "/paste":           "Paste image from clipboard",
            "/image <path>":    "Attach a local image file",
        },
        "Other": {
            "/help":            "Show this help message",
            "/exit, /quit":     "Exit the CLI",
        },
    }

    get_console().print()
    get_console().print("  [bold]Available Commands[/bold]")
    get_console().print()

    for category, commands in categories.items():
        get_console().print(f"  [bold]-- {category} --[/bold]")
        for cmd, desc in commands.items():
            get_console().print(f"    [bright_green]{cmd:<18}[/bright_green] [dim]{desc}[/dim]")
        get_console().print()

    get_console().print("  [bold]Keyboard Shortcuts[/bold]")
    get_console().print()
    shortcuts = {
        "Enter":             "Submit your message",
        "Ctrl+X":            "Toggle Agent/Shell mode ($ = shell, > = agent)",
        "Ctrl+J, Alt+Enter": "Insert newline for multi-line input",
        "Ctrl+D":            "Exit",
        "Ctrl+C":            "Interrupt current operation",
        "Tab, Right Arrow":  "Accept completion / auto-suggestion",
        "Alt+V":             "Paste image from clipboard",
    }
    for key, desc in shortcuts.items():
        get_console().print(f"    [bright_green]{key:<20}[/bright_green] [dim]{desc}[/dim]")
    get_console().print()

    get_console().print("  [bold]Input Features[/bold]")
    get_console().print()
    get_console().print("    [bright_green]@filename[/bright_green]           Reference a file - content injected into prompt")
    get_console().print("    [bright_green]/command[/bright_green]            Type / to see slash commands with auto-complete")
    get_console().print()
    get_console().print("  [dim]Tip: type your message and press Enter to chat![/dim]")
    get_console().print()


def _extract_filename(file_path: str) -> str:
    """Extract filename from a file path."""
    return Path(file_path).name


def _format_line_range(offset: int, limit: int) -> str:
    """Format line range as L{start}-{end}."""
    start = offset + 1 if offset else 1
    end = start + (limit or 500) - 1
    return f"L{start}-{end}"


def _shorten_path(file_path: str) -> str:
    """Shorten a file path for display: prefer relative path, fallback to filename."""
    if not file_path or file_path == ".":
        return "."
    p = Path(file_path)
    try:
        return str(p.relative_to(Path.cwd()))
    except ValueError:
        return p.name


def _shorten_paths_in_command(command: str) -> str:
    """Shorten absolute paths embedded in a shell command."""
    cwd = str(Path.cwd())
    if cwd in command:
        command = command.replace(cwd + "/", "").replace(cwd, ".")
    return command


def format_tool_display(tool_name: str, tool_args: dict) -> str:
    """Format tool call for user-friendly display."""
    # File reading tools - show filename and line range
    if tool_name == "read_file":
        file_path = tool_args.get("file_path", "")
        filename = _extract_filename(file_path)
        offset = tool_args.get("offset", 0)
        limit = tool_args.get("limit", 500)
        line_range = _format_line_range(offset, limit)
        return f"{filename} ({line_range})"
    
    # File writing tools - show filename only
    if tool_name == "write_file":
        file_path = tool_args.get("file_path", "")
        return _extract_filename(file_path)
    
    # File editing tools - show filename only
    if tool_name == "edit_file":
        file_path = tool_args.get("file_path", "")
        return _extract_filename(file_path)
    
    # Execute command - shorten absolute paths in command
    if tool_name == "execute":
        command = tool_args.get("command", "")
        command = _shorten_paths_in_command(command)
        if len(command) > 300:
            return command[:297] + "..."
        return command
    
    # Todo tools - list the todo items
    if tool_name == "write_todos":
        todos = tool_args.get("todos", [])
        if isinstance(todos, list) and todos:
            todo_lines = []
            for todo in todos[:5]:
                if isinstance(todo, dict):
                    content = todo.get("content", "")[:50]
                    status = todo.get("status", "pending")
                    status_icon = "✓" if status == "completed" else "○" if status == "pending" else "◐"
                    todo_lines.append(f"{status_icon} {content}")
                else:
                    todo_lines.append(f"○ {str(todo)[:50]}")
            if len(todos) > 5:
                todo_lines.append(f"  ... and {len(todos) - 5} more")
            return "\n    ".join(todo_lines)
        return f"{len(todos)} items"
    
    # Web search - show search queries
    if tool_name == "web_search":
        queries = tool_args.get("queries", "")
        if isinstance(queries, list):
            return ", ".join(str(q)[:40] for q in queries[:3])
        return str(queries)[:80]
    
    # Fetch URL - show the URL
    if tool_name == "fetch_url":
        url = tool_args.get("url", "")
        if len(url) > 60:
            return url[:57] + "..."
        return url
    
    # ls/glob/grep - show shortened path/pattern
    if tool_name == "ls":
        directory = tool_args.get("directory", ".")
        return _shorten_path(directory)

    if tool_name == "glob":
        pattern = tool_args.get("pattern", "*")
        path = tool_args.get("path", ".")
        return f"{pattern} in {_shorten_path(path)}"

    if tool_name == "grep":
        pattern = tool_args.get("pattern", "")
        path = tool_args.get("path", ".")
        include = tool_args.get("include", "")
        display = f"'{pattern[:40]}' in {_shorten_path(path)}"
        if include:
            display += f" ({include})"
        return display
    
    # Task tool - show description
    if tool_name == "task":
        description = tool_args.get("description", "")
        if len(description) > 80:
            return description[:77] + "..."
        return description
    
    # Default format for other tools
    brief_args = []
    for key, value in tool_args.items():
        if isinstance(value, str):
            if len(value) > 40:
                value = value[:37] + "..."
            brief_args.append(f"{key}={value!r}")
        elif isinstance(value, (int, float, bool)):
            brief_args.append(f"{key}={value}")
        elif isinstance(value, list):
            brief_args.append(f"{key}=[{len(value)} items]")
        elif isinstance(value, dict):
            brief_args.append(f"{key}={{...}}")
    
    args_str = ", ".join(brief_args[:3])
    if len(brief_args) > 3:
        args_str += ", ..."
    
    return args_str if args_str else ""


def _display_tool_impl(console_instance, tool_name: str, tool_args: dict,
                       tool_count: int = 0) -> None:
    """Shared implementation for displaying a tool call."""
    icon = TOOL_ICONS.get(tool_name, TOOL_ICONS["default"])
    display_str = format_tool_display(tool_name, tool_args)

    # Add blank line between tools for readability
    if tool_count > 1:
        console_instance.print()

    # Special handling for write_todos - multi-line display
    if tool_name == "write_todos" and "\n" in display_str:
        console_instance.print(f"  {icon} [bold magenta]{tool_name}[/bold magenta] tasks:")
        console_instance.print(f"    {display_str}", style="dim")
    elif display_str:
        console_instance.print(f"  {icon} [bold magenta]{tool_name}[/bold magenta] [dim]{display_str}[/dim]")
    else:
        console_instance.print(f"  {icon} [bold magenta]{tool_name}[/bold magenta]")


_BOX_COLOR = "bright_yellow"


class StreamDisplayManager:
    """Manages CLI output display state for streaming responses.

    LLM response text is wrapped in a ``╭─ Response ─╮ … ╰───╯`` box.
    Reasoning/thinking gets a separate ``╭─ Thinking ─╮`` box.
    """

    def __init__(self, console_instance):
        self.console = console_instance
        self._term_width = min(console_instance.width or 80, 120)
        self.reset()

    def reset(self):
        """Reset state for a new response."""
        self.in_thinking = False
        self.thinking_shown = False
        self.tool_count = 0
        self.in_tool_section = False
        self.response_started = False
        self.has_content_output = False
        self._response_buffer = []
        self._box_opened = False
        self._thinking_box_opened = False
        self._line_buffer = ""  # accumulates tokens until newline for line-buffered output

    def _open_box(self, label: str = "Response"):
        w = self._term_width
        fill = max(0, w - len(label) - 5)
        self.console.print(f"[{_BOX_COLOR}]╭─ {label} {'─' * fill}╮[/{_BOX_COLOR}]")

    def _close_box(self):
        w = self._term_width
        self.console.print(f"[{_BOX_COLOR}]╰{'─' * (w - 2)}╯[/{_BOX_COLOR}]")

    def _flush_line_buffer(self):
        """Flush any accumulated partial line to output."""
        if self._line_buffer:
            self.console.print(self._line_buffer, highlight=False, markup=False)
            self._line_buffer = ""

    def _stream_text(self, content: str):
        """Buffer tokens and output complete lines.

        Accumulate tokens and flush on each newline through the same
        console path used by box drawing, ensuring correct ordering.
        """
        self._line_buffer += content
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            self.console.print(line, highlight=False, markup=False)

    def start_thinking(self):
        """Start thinking section with a box."""
        if not self.thinking_shown:
            self.console.print()
            self._open_box("Thinking")
            self._thinking_box_opened = True
            self.thinking_shown = True
            self.in_thinking = True

    def stream_thinking(self, content: str):
        """Stream thinking content with line-buffered output."""
        self._stream_text(content)

    def end_thinking(self):
        """End thinking section and close its box."""
        if self.in_thinking:
            self._flush_line_buffer()
            if self._thinking_box_opened:
                self._close_box()
                self._thinking_box_opened = False
            self.in_thinking = False
            self.response_started = False

    def start_tool_section(self):
        """Start tool section."""
        if not self.in_tool_section:
            if self.in_thinking:
                self.end_thinking()
            if self.has_content_output and not self.response_started:
                self.console.print()
            self.console.print()
            self.in_tool_section = True

    def display_tool(self, tool_name: str, tool_args: dict):
        """Display a single tool call."""
        self.start_tool_section()
        self.tool_count += 1
        _display_tool_impl(self.console, tool_name, tool_args, self.tool_count)
    
    def display_tool_result(self, tool_name: str, result_content: str,
                            is_error: bool = False, elapsed: float = None):
        """Display tool execution result as a compact preview."""
        if not result_content:
            if elapsed is not None:
                self.console.print(f"    [dim]completed in {elapsed:.1f}s[/dim]")
            return

        # Special handling for task (subagent) - show inner tool calls
        if tool_name == "task":
            self._display_task_result(result_content, is_error)
            return

        result_str = str(result_content)

        # Shorten absolute paths in results for grep/glob/execute
        if tool_name in ("grep", "glob", "execute", "ls"):
            cwd = str(Path.cwd())
            if cwd in result_str:
                result_str = result_str.replace(cwd + "/", "").replace(cwd, ".")

        lines = result_str.splitlines()

        # grep results: fewer preview lines, they can be very long
        if tool_name == "grep":
            max_lines = 3
            max_line_width = 100
        else:
            max_lines = 4
            max_line_width = 120
        
        style = "dim red" if is_error else "dim"
        prefix = "    ⎿ " if not is_error else "    ⎿ ⚠ "
        cont_prefix = "      "
        
        display_lines = lines[:max_lines]
        for i, line in enumerate(display_lines):
            if len(line) > max_line_width:
                line = line[:max_line_width - 3] + "..."
            p = prefix if i == 0 else cont_prefix
            self.console.print(f"{p}{line}", style=style)
        
        remaining = len(lines) - max_lines
        if remaining > 0:
            self.console.print(f"{cont_prefix}... ({remaining} more lines)", style="dim italic")
    
    def _display_task_result(self, result_content: str, is_error: bool = False):
        """Display subagent task result with inner tool calls and execution summary."""
        try:
            data = json.loads(result_content)
        except (ValueError, TypeError):
            self.console.print(f"    ⎿ {str(result_content)[:120]}", style="dim")
            return
        
        success = data.get("success", False)
        tool_summary = data.get("tool_calls_summary", [])
        exec_time = data.get("execution_time")
        tool_count = data.get("tool_count", len(tool_summary))
        subagent_name = data.get("subagent_name", "Subagent")
        
        if not success:
            error_msg = data.get("error", "Unknown error")
            self.console.print(f"    ⎿ ⚠ {error_msg[:120]}", style="dim red")
            return
        
        max_shown = 8
        for i, tc in enumerate(tool_summary[:max_shown]):
            name = tc.get("name", "")
            info = tc.get("info", "")
            if len(info) > 90:
                info = info[:87] + "..."
            if i == 0:
                self.console.print(f"    ⎿ ", end="", style="dim")
            else:
                self.console.print(f"      ", end="")
            self.console.print(f"{name}", end="", style="dim bold")
            if info:
                self.console.print(f" {info}", style="dim")
            else:
                self.console.print(style="dim")
        
        if len(tool_summary) > max_shown:
            remaining = len(tool_summary) - max_shown
            self.console.print(f"      ... and {remaining} more tool calls", style="dim italic")
        
        summary_parts = []
        if tool_count > 0:
            summary_parts.append(f"{tool_count} tool uses")
        if exec_time is not None:
            summary_parts.append(f"cost: {exec_time:.1f}s")
        if summary_parts:
            summary_str = ", ".join(summary_parts)
            self.console.print(f"    [dim italic]Execution Summary: {summary_str}[/dim italic]")
    
    def end_tool_section(self):
        """End tool section."""
        if self.in_tool_section:
            self.in_tool_section = False
            self.response_started = False
    
    def start_response(self):
        """Start response section with a box."""
        if not self.response_started:
            if self.in_thinking:
                self.end_thinking()
            if self.in_tool_section:
                self.end_tool_section()
            self.console.print()
            self._open_box("Response")
            self._box_opened = True
            self.response_started = True

    def stream_response(self, content: str):
        """Stream response content with line-buffered output."""
        self.start_response()
        self._response_buffer.append(content)
        self._stream_text(content)
        self.has_content_output = True

    def finalize(self):
        """Finalize output: flush buffered text and close open boxes."""
        if self.in_thinking:
            self.end_thinking()
        if self.in_tool_section:
            self.end_tool_section()
        if self.has_content_output:
            self._flush_line_buffer()
            if self._box_opened:
                self._close_box()
                self._box_opened = False
        elif self._box_opened:
            self._close_box()
            self._box_opened = False


def display_tool_call(tool_name: str, tool_args: dict) -> None:
    """Display a tool call with icon and colored tool name."""
    _display_tool_impl(get_console(), tool_name, tool_args)


def _has_markdown(text: str) -> bool:
    """Detect if text contains Markdown formatting worth rendering."""
    markers = ["```", "## ", "### ", "* ", "- [ ]", "| ", "**", "1. "]
    return any(m in text for m in markers)


def render_markdown_response(console_instance, text: str) -> None:
    """Render a complete response as rich Markdown if it contains formatting."""
    if _has_markdown(text):
        console_instance.print(Markdown(text))
    else:
        console_instance.print(text, style=COLORS["agent"])


def display_diff(console_instance, file_path: str, old_content: str, new_content: str) -> None:
    """Display unified diff between old and new file content."""
    diff_lines = list(difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=3,
    ))
    if diff_lines:
        diff_text = "".join(diff_lines)
        console_instance.print(Syntax(diff_text, "diff", theme="monokai", line_numbers=False))


def _format_tokens_short(n: int) -> str:
    """Format token count with K/M suffix for compact display."""
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{int(v)}M" if v == int(v) else f"{v:.1f}M"
    if n >= 1_000:
        v = n / 1_000
        return f"{int(v)}K" if v == int(v) else f"{v:.1f}K"
    return str(n)


def context_pct_style(pct: float) -> str:
    """Return Rich style name based on context usage percentage."""
    if pct >= 95:
        return "bold red"
    if pct >= 80:
        return "red"
    if pct >= 50:
        return "yellow"
    return "green"


def build_context_bar(pct: float, width: int = 10) -> str:
    """Build a visual context usage bar like [████░░░░░░]."""
    safe = max(0.0, min(100.0, pct))
    filled = round((safe / 100) * width)
    return f"[{'█' * filled}{'░' * max(0, width - filled)}]"


def display_token_stats(
    console_instance,
    cost_tracker,
    *,
    context_window: int = 128000,
    session_total_tokens: int = 0,
    tool_use_count: int = 0,
    elapsed_seconds: float = 0.0,
) -> None:
    """Display compact per-response stats footer with color-graded context.

    Format example::

        ctx 50.0% (64K / 128K) [████░░░░░░] · 2 tools · 5.32s · $0.0034
    """
    if cost_tracker is None:
        return

    if session_total_tokens <= 0:
        session_total_tokens = (
            cost_tracker.total_input_tokens + cost_tracker.total_output_tokens
        )

    used_pct = (
        session_total_tokens / context_window * 100 if context_window > 0 else 0.0
    )
    pct_style = context_pct_style(used_pct)
    bar = build_context_bar(used_pct)

    parts = [
        f"[{pct_style}]ctx {used_pct:.1f}%[/{pct_style}] "
        f"({_format_tokens_short(session_total_tokens)} / "
        f"{_format_tokens_short(context_window)}) "
        f"[{pct_style}]{bar}[/{pct_style}]"
    ]

    if tool_use_count > 0:
        label = "tool" if tool_use_count == 1 else "tools"
        parts.append(f"[dim]{tool_use_count} {label}[/dim]")

    if elapsed_seconds > 0:
        parts.append(f"[dim]{elapsed_seconds:.2f}s[/dim]")

    cost = cost_tracker.total_cost_usd
    cost_str = f"${cost:.4f}" if cost < 0.01 else f"${cost:.2f}"
    parts.append(f"[dim]{cost_str}[/dim]")

    console_instance.print(f"{'  ·  '.join(parts)}")


# ---------------------------------------------------------------------------
# Persistent TUI status bar (prompt_toolkit fragments)
# ---------------------------------------------------------------------------

def _ctx_bar_ansi(pct: float, width: int = 10) -> str:
    """Build a plain-text context usage bar for the status bar."""
    safe = max(0.0, min(100.0, pct))
    filled = round((safe / 100) * width)
    return f"{'█' * filled}{'░' * max(0, width - filled)}"


def _ctx_fg_style(pct: float) -> str:
    """Return a prompt_toolkit style class for context usage percentage."""
    if pct >= 95:
        return "class:sb-critical"
    if pct >= 80:
        return "class:sb-bad"
    if pct >= 50:
        return "class:sb-warn"
    return "class:sb-good"


def format_duration_compact(seconds: float) -> str:
    """Format seconds into compact human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def build_status_bar_fragments(
    *,
    model_name: str = "",
    context_tokens: int = 0,
    context_window: int = 0,
    cost_usd: float = 0.0,
    active_seconds: float = 0.0,
    last_turn_seconds: float = 0.0,
    spinner_text: str = "",
    terminal_width: int = 80,
):
    """Build prompt_toolkit formatted-text fragments for the persistent status bar.

    Time display uses *agent active time* (sum of all LLM + tool
    execution durations) rather than session wall-clock, plus the
    most recent turn's latency.

    Adapts to terminal width:
      <52 cols:  ▸ model · ⏱12.3s
      <76 cols:  ▸ model · 45% · $0.02 · ⏱12.3s
      >=76 cols: ▸ model │ 64K/128K │ [████░░] 45% │ $0.02 │ ⏱12.3s Σ1m45s
    """
    short = model_name.split("/")[-1] if "/" in model_name else model_name
    if len(short) > 26:
        short = short[:23] + "..."
    pct = (context_tokens / context_window * 100) if context_window > 0 else 0.0
    pct_label = f"{pct:.0f}%"
    fg = _ctx_fg_style(pct)
    cost_str = f"${cost_usd:.4f}" if cost_usd < 0.01 else f"${cost_usd:.2f}"

    turn_str = f"⏱ {last_turn_seconds:.1f}s" if last_turn_seconds > 0 else ""
    total_str = f"Σ {format_duration_compact(active_seconds)}" if active_seconds > 0 else ""

    sep = ("class:sb-dim", " · ")

    if terminal_width < 52:
        frags = [
            ("class:sb", " ▸ "),
            ("class:sb-strong", short),
        ]
        if turn_str:
            frags.append(sep)
            frags.append(("class:sb", turn_str))
    elif terminal_width < 76:
        frags = [
            ("class:sb", " ▸ "),
            ("class:sb-strong", short),
            sep,
            (fg, pct_label),
            sep,
            ("class:sb-dim", cost_str),
        ]
        if turn_str:
            frags.append(sep)
            frags.append(("class:sb", turn_str))
    else:
        ctx_used = _format_tokens_short(context_tokens) if context_tokens else "0"
        ctx_total = _format_tokens_short(context_window) if context_window else "?"
        frags = [
            ("class:sb", " ▸ "),
            ("class:sb-strong", short),
            ("class:sb-dim", " │ "),
            ("class:sb", f"{ctx_used}/{ctx_total}"),
            ("class:sb-dim", " │ "),
            (fg, _ctx_bar_ansi(pct)),
            ("class:sb-dim", " "),
            (fg, pct_label),
            ("class:sb-dim", " │ "),
            ("class:sb", cost_str),
        ]
        if turn_str:
            frags.append(("class:sb-dim", " │ "))
            frags.append(("class:sb", turn_str))
        if total_str:
            frags.append(("class:sb-dim", "  "))
            frags.append(("class:sb-dim", total_str))

    frags.append(("class:sb", " "))
    return frags
