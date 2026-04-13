# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: CLI slash command handlers and dispatch registry.

All _cmd_* functions live here. interactive.py imports COMMAND_REGISTRY
and COMMAND_HANDLERS to wire them into the TUI process_loop.
"""
import asyncio
import collections
import json
import os
import queue
import shlex
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentica.cli.config import (
    get_console,
    BUILTIN_TOOLS,
    TOOL_REGISTRY,
    MODEL_REGISTRY,
    EXAMPLE_MODELS,
    create_agent,
    get_model,
    _generate_session_id,
)
from agentica.cli.display import (
    print_header,
    show_help,
)
from agentica.memory.models import AgentRun
from agentica.model.message import Message
from agentica.run_response import RunResponse
from agentica.skills import (
    get_skill_registry,
    install_skills,
    list_installed_skills,
    load_skills,
    remove_skill,
)
from agentica.skills.skill_registry import reset_skill_registry


# ==================== CommandContext ====================

@dataclass
class CommandContext:
    """Shared context passed to all command handlers.

    Replaces the scattered **kwargs parameter bags with a single,
    type-checkable object.
    """
    agent_config: dict
    current_agent: Any  # Agent instance
    extra_tools: Optional[List] = None
    extra_tool_names: Optional[List[str]] = None
    workspace: Any = None  # Optional[Workspace]
    skills_registry: Any = None
    permission_manager: Any = None
    shell_mode: bool = False
    tui_state: Optional[dict] = None
    pending_queue: Any = None  # PendingQueue
    agent_running: bool = False
    attached_images: Optional[list] = None
    image_counter: Optional[list] = None
    # Background tasks — instance-level, not module-global
    bg_tasks: Dict[str, dict] = field(default_factory=dict)
    bg_task_counter: int = 0


# ==================== PendingQueue ====================

class PendingQueue:
    """Thread-safe observable queue with list/clear/remove support."""

    def __init__(self):
        self._deque = collections.deque()
        self._lock = threading.Lock()

    def put(self, item):
        with self._lock:
            self._deque.append(item)

    def get(self, timeout: float = 0.1):
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._deque:
                    return self._deque.popleft()
            if time.monotonic() >= deadline:
                raise queue.Empty
            time.sleep(0.02)

    def peek_all(self) -> list:
        with self._lock:
            return list(self._deque)

    def qsize(self) -> int:
        with self._lock:
            return len(self._deque)

    def empty(self) -> bool:
        with self._lock:
            return len(self._deque) == 0

    def clear(self):
        with self._lock:
            self._deque.clear()

    def remove_index(self, idx: int) -> bool:
        with self._lock:
            if 0 <= idx < len(self._deque):
                del self._deque[idx]
                return True
            return False


# ==================== Concurrent commands ====================

# Commands that can execute while the agent is streaming (non-blocking).
# Readonly info commands + queue/bg management.
CONCURRENT_CMDS = frozenset({
    "/bg", "/background", "/stop",
    "/q", "/queue",
    "/cost", "/usage", "/config", "/debug",
    "/history", "/help", "/tools", "/skills",
    "/permissions", "/statusbar", "/sb",
    "/reasoning",
})


# ==================== Helpers ====================

IMAGE_EXTENSIONS = frozenset({
    '.png', '.jpg', '.jpeg', '.gif', '.webp',
    '.bmp', '.tiff', '.tif', '.svg', '.ico',
})


def _cmd_title(name: str):
    """Print a command header."""
    get_console().print(f"\n  [bold]{name}[/bold]")


def _sanitize_history_for_model_switch(agent) -> None:
    """Strip tool_calls and tool messages from working memory history."""
    wm = agent.working_memory
    for run in wm.runs:
        if not run.response or not run.response.messages:
            continue
        cleaned = []
        for msg in run.response.messages:
            if msg.role == "tool":
                continue
            if msg.role == "assistant" and msg.tool_calls:
                text = msg.content if isinstance(msg.content, str) else ""
                if text:
                    cleaned.append(Message(role="assistant", content=text))
                continue
            if msg.role == "system":
                continue
            cleaned.append(msg)
        run.response.messages = cleaned


def _refresh_skills_session(ctx: CommandContext):
    """Reload skill registry from disk and rebuild the current agent."""
    reset_skill_registry()
    load_skills()
    new_registry = get_skill_registry()
    new_agent = create_agent(ctx.agent_config, ctx.extra_tools, ctx.workspace, new_registry)
    return {
        "skills_registry": new_registry,
        "current_agent": new_agent,
    }


def _run_async_safe(coro):
    """Run an async coroutine safely from a sync context.

    Uses asyncio.run() in threads without an event loop.
    Falls back to loop.run_until_complete() if a loop already exists.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Already inside an event loop — create a new one in a thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


# ==================== Command Handlers ====================

def _cmd_help(ctx: CommandContext, cmd_args: str = ""):
    _cmd_title("/help")
    show_help()


def _cmd_exit(ctx: CommandContext, cmd_args: str = ""):
    return "EXIT"


def _cmd_tools(ctx: CommandContext, cmd_args: str = ""):
    """List available tools with categories and descriptions."""
    _cmd_title("/tools")
    con = get_console()

    active_set = set(ctx.extra_tool_names) if ctx.extra_tool_names else set()

    # Built-in tools
    con.print()
    con.print(f"  [bold cyan]Built-in Tools ({len(BUILTIN_TOOLS)})[/bold cyan]  [dim]always active[/dim]")
    con.print(f"    {', '.join(BUILTIN_TOOLS)}")
    con.print()

    # Extra tools — grouped by category from TOOL_REGISTRY
    categories: Dict[str, Dict[str, str]] = {}
    for name, (_mod, _cls, category, description) in TOOL_REGISTRY.items():
        categories.setdefault(category, {})[name] = description

    con.print("  [bold cyan]Extra Tools[/bold cyan]  [dim]enable with --tools <name>[/dim]")
    con.print()
    for category in sorted(categories.keys()):
        tools = categories[category]
        active_in_cat = [t for t in tools if t in active_set]
        marker = " [green]*[/green]" if active_in_cat else ""
        con.print(f"  [bold]{category}[/bold]{marker}  [dim]({len(tools)} tools)[/dim]")
        for name, desc in sorted(tools.items()):
            active = "[green](*)[/green] " if name in active_set else "    "
            con.print(f"    {active}[dim]{name:<20}[/dim] {desc}")
        con.print()

    if active_set:
        con.print(f"  [green](*) = currently enabled[/green]")
    con.print(f"  [dim]Example: agentica --tools browser duckduckgo[/dim]")
    con.print()


def _cmd_skills(ctx: CommandContext, cmd_args: str = ""):
    """Unified skill management: list, install, remove, reload, inspect."""
    _cmd_title("/skills")
    con = get_console()
    args_str = cmd_args.strip()
    parts = shlex.split(args_str) if args_str else []
    subcommand = parts[0].lower() if parts else ""
    sub_args = parts[1:]

    if subcommand == "install":
        if not sub_args:
            con.print("  [dim]Usage: /skills install <git-url-or-local-path> [--force][/dim]")
            return
        source = None
        force = False
        for arg in sub_args:
            if arg == "--force":
                force = True
            elif source is None:
                source = arg
        if source is None:
            con.print("  [dim]Missing install source.[/dim]")
            return
        replaced = []
        installed = install_skills(source, force=force, replaced_symlinked_skills=replaced)
        con.print(f"  [green]Installed {len(installed)} skill(s) from {source}.[/green]")
        for name in replaced:
            con.print(f"  [green]Replaced existing: {name}[/green]")
        return _refresh_skills_session(ctx)

    if subcommand in ("remove", "uninstall"):
        if not sub_args:
            con.print("  [dim]Usage: /skills remove <skill-name>[/dim]")
            return
        removed_path = remove_skill(sub_args[0])
        con.print(f"  [green]Removed skill '{sub_args[0]}' from {removed_path}[/green]")
        return _refresh_skills_session(ctx)

    if subcommand == "reload":
        return _cmd_reload_skills(ctx)

    if subcommand == "inspect":
        query = " ".join(sub_args).lower() if sub_args else ""
        if not query:
            con.print("  [dim]Usage: /skills inspect <skill-name>[/dim]")
            return
        found = None
        if ctx.skills_registry:
            for skill in ctx.skills_registry.list_all():
                if skill.name.lower() == query:
                    found = skill
                    break
        if not found:
            for skill in list_installed_skills():
                if skill.name.lower() == query:
                    found = skill
                    break
        if found:
            con.print(f"  [bold cyan]{found.name}[/bold cyan]")
            con.print(f"  [dim]Path: {found.path}[/dim]")
            con.print(f"  [dim]Location: {found.location}[/dim]")
            if found.description:
                con.print(f"  {found.description}")
            if found.trigger:
                con.print(f"  Trigger: [green]{found.trigger}[/green]")
            if found.requires:
                con.print(f"  Requires: {', '.join(found.requires)}")
            content = found.content
            if content:
                lines = content.splitlines()[:10]
                con.print()
                for line in lines:
                    con.print(f"  [dim]{line}[/dim]")
                if len(content.splitlines()) > 10:
                    con.print(f"  [dim]... ({len(content.splitlines()) - 10} more lines)[/dim]")
        else:
            con.print(f"  [yellow]Skill '{query}' not found.[/yellow]")
        return

    # /skills list (or /skills with no subcommand)
    all_skills = []

    if ctx.skills_registry and len(ctx.skills_registry) > 0:
        for skill in ctx.skills_registry.list_all():
            all_skills.append(("loaded", skill))

    if ctx.current_agent and ctx.current_agent.tools:
        from agentica.tools.skill_tool import SkillTool
        for tool in ctx.current_agent.tools:
            if isinstance(tool, SkillTool):
                for skill in tool._get_enabled_skills():
                    all_skills.append(("agent", skill))
                break

    installed = list_installed_skills()
    loaded_names = {s.name for _, s in all_skills}
    for skill in installed:
        if skill.name not in loaded_names:
            all_skills.append(("installed", skill))

    if not all_skills and subcommand != "list":
        con.print("  No skills found.")
        con.print()
        con.print("  [dim]Usage:[/dim]")
        con.print("    [dim]/skills                   List all skills[/dim]")
        con.print("    [dim]/skills install <source>   Install from git URL or local path[/dim]")
        con.print("    [dim]/skills remove <name>      Remove an installed skill[/dim]")
        con.print("    [dim]/skills inspect <name>     Preview skill details[/dim]")
        con.print("    [dim]/skills reload             Reload skills from disk[/dim]")
        return

    if all_skills:
        con.print(f"  [bold cyan]Skills ({len(all_skills)})[/bold cyan]")
        con.print()
        for source_type, skill in all_skills:
            trigger_str = f" [green]{skill.trigger}[/green]" if skill.trigger else ""
            loc = f"[dim]({source_type})[/dim]"
            con.print(f"    [bold]{skill.name}[/bold]{trigger_str} {loc}")
            if skill.description:
                desc = skill.description[:70] + ("..." if len(skill.description) > 70 else "")
                con.print(f"      [dim]{desc}[/dim]")
        con.print()
    else:
        con.print("  No skills found.")
        con.print()

    con.print("  [dim]Commands: /skills install <src> | remove <name> | inspect <name> | reload[/dim]")


def _cmd_history(ctx: CommandContext, cmd_args: str = ""):
    """Display conversation history in compact format."""
    _cmd_title("/history")
    con = get_console()
    agent = ctx.current_agent
    if not agent:
        con.print("[yellow]No conversation history yet.[/yellow]")
        return
    messages = agent.working_memory.messages
    if not messages:
        con.print("[yellow]No conversation history yet.[/yellow]")
        return

    preview_limit = 400
    visible_index = 0
    hidden_tool_msgs = 0

    def _flush_tools():
        nonlocal hidden_tool_msgs
        if hidden_tool_msgs == 0:
            return
        noun = "message" if hidden_tool_msgs == 1 else "messages"
        con.print(f"\n  [dim]\\[Tools] ({hidden_tool_msgs} tool {noun} hidden)[/dim]")
        hidden_tool_msgs = 0

    con.print()
    con.print("  [bold cyan]Conversation History[/bold cyan]")

    for msg in messages:
        role = msg.role
        if role == "system":
            continue
        if role == "tool":
            hidden_tool_msgs += 1
            continue

        _flush_tools()
        visible_index += 1

        content = msg.content or ""
        if isinstance(content, list):
            content = str(content)
        content_text = content if isinstance(content, str) else str(content)

        if role == "user":
            preview = content_text[:preview_limit]
            suffix = "..." if len(content_text) > preview_limit else ""
            con.print(f"\n  [cyan]\\[You #{visible_index}][/cyan]")
            con.print(f"    {preview}{suffix}")
            continue

        tool_calls = msg.tool_calls or []
        if content_text:
            preview = content_text[:preview_limit]
            suffix = "..." if len(content_text) > preview_limit else ""
        elif tool_calls:
            n = len(tool_calls)
            preview = f"(requested {n} tool call{'s' if n > 1 else ''})"
            suffix = ""
        else:
            preview = "(no text response)"
            suffix = ""
        con.print(f"\n  [green]\\[Agent #{visible_index}][/green]")
        con.print(f"    {preview}{suffix}")

    _flush_tools()
    con.print()


def _cmd_workspace(ctx: CommandContext, cmd_args: str = ""):
    """Display current configuration and workspace status."""
    _cmd_title("/config")
    con = get_console()

    con.print()
    con.print("  [bold]-- Model --[/bold]")
    con.print(f"  Model:       {ctx.agent_config.get('model_provider', '')}/{ctx.agent_config.get('model_name', '')}")
    if ctx.current_agent and ctx.current_agent.model:
        model = ctx.current_agent.model
        if model.base_url:
            con.print(f"  Base URL:    {model.base_url}")
        api_key = model.api_key or ""
        key_display = "********" + api_key[-4:] if len(api_key) > 4 else "(not set)"
        con.print(f"  API Key:     {key_display}")
        con.print(f"  Context:     {model.context_window:,} tokens")

    con.print()
    con.print("  [bold]-- Terminal --[/bold]")
    con.print(f"  Working Dir: {os.getcwd()}")
    con.print(f"  Mode:        {'Shell' if ctx.shell_mode else 'Agent'}")
    if ctx.permission_manager:
        con.print(f"  Permissions: {ctx.permission_manager.mode}")

    con.print()
    con.print("  [bold]-- Agent --[/bold]")
    con.print(f"  Built-in:    {', '.join(BUILTIN_TOOLS)}")
    if ctx.extra_tool_names:
        con.print(f"  Extra:       {', '.join(ctx.extra_tool_names)}")
    if ctx.skills_registry and len(ctx.skills_registry) > 0:
        con.print(f"  Skills:      {len(ctx.skills_registry)} loaded")
    show_reasoning = ctx.tui_state.get("show_reasoning", True) if ctx.tui_state else True
    con.print(f"  Reasoning:   {'on' if show_reasoning else 'off'}")

    con.print()
    con.print("  [bold]-- Session --[/bold]")
    if ctx.current_agent:
        con.print(f"  Session ID:  {ctx.current_agent.session_id}")
    started = ctx.tui_state.get("session_start") if ctx.tui_state else None
    if started:
        con.print(f"  Started:     {started}")
    msg_count = 0
    if ctx.current_agent:
        msg_count = len(ctx.current_agent.working_memory.messages)
    con.print(f"  Messages:    {msg_count}")

    if ctx.workspace and ctx.workspace.exists():
        con.print(f"  Workspace:   {ctx.workspace.path}")
        memory_files = ctx.workspace.get_all_memory_files()
        if memory_files:
            paths = ", ".join(str(mf) for mf in memory_files)
            con.print(f"  Memory:      {paths}")
        else:
            con.print("  Memory:      (none)")
    elif ctx.workspace:
        con.print(f"  Workspace:   {ctx.workspace.path} (not initialized)")
    else:
        con.print("  Workspace:   (not configured)")
    con.print()


def _cmd_newchat(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    current_agent = create_agent(ctx.agent_config, ctx.extra_tools, ctx.workspace, ctx.skills_registry)
    con.print("[green]New chat session created.[/green]")
    con.print("[dim]Conversation history cleared.[/dim]")
    return {"current_agent": current_agent}


def _cmd_resume(ctx: CommandContext, cmd_args: str = ""):
    """Resume a previous session from JSONL log."""
    from agentica.memory.session_log import SessionLog
    con = get_console()

    sessions = SessionLog.list_sessions()
    if not sessions:
        con.print("[yellow]No sessions found to resume.[/yellow]")
        return

    args_str = (cmd_args or "").strip()
    resume_at_uuid = None
    if " at " in args_str:
        parts = args_str.split(" at ", 1)
        args_str = parts[0].strip()
        resume_at_uuid = parts[1].strip()

    if args_str:
        try:
            idx = int(args_str) - 1
            if 0 <= idx < len(sessions):
                chosen = sessions[idx]
            else:
                con.print("[red]Invalid number.[/red]")
                return
        except ValueError:
            matching = [s for s in sessions if args_str in s["session_id"]]
            if not matching:
                con.print(f"[red]No session matching '{args_str}'[/red]")
                return
            chosen = matching[0]

        if resume_at_uuid is None:
            log = SessionLog(chosen["session_id"])
            user_msgs = log.list_user_messages(limit=10)
            if user_msgs:
                con.print(f"\n[bold]Session: {chosen['session_id']}[/bold]")
                con.print("[dim]Recent queries (resume from any point):[/dim]\n")
                for i, m in enumerate(user_msgs, 1):
                    ts = m.get("timestamp", "")[:19].replace("T", " ") if m.get("timestamp") else ""
                    con.print(f"  {i}. [dim]{ts}[/dim] {m['content']}")
                con.print(f"\n[dim]Usage: /resume {chosen['session_id']} at <uuid>[/dim]")

        agent_config = dict(ctx.agent_config)
        agent_config["session_id"] = chosen["session_id"]
        agent_config["_resume_at_uuid"] = resume_at_uuid
        current_agent = create_agent(agent_config, ctx.extra_tools, ctx.workspace, ctx.skills_registry)

        if resume_at_uuid and current_agent._session_log:
            current_agent.working_memory.clear()
            for rm in current_agent._session_log.load(resume_at=resume_at_uuid):
                current_agent.working_memory.add_message(
                    Message(role=rm["role"], content=rm.get("content", ""))
                )

        con.print(f"[green]Resumed session: {chosen['session_id']}"
                  f"{f' at {resume_at_uuid[:8]}...' if resume_at_uuid else ''}[/green]")
        return {"current_agent": current_agent}
    else:
        con.print("\n[bold]Available sessions:[/bold]\n")
        for i, s in enumerate(sessions[:10], 1):
            ts_str = s.get("last_timestamp", "") or ""
            if ts_str:
                ts_str = ts_str[:19].replace("T", " ")
            size_kb = s["size_bytes"] / 1024
            sid = s["session_id"]
            display_id = f"{sid[:8]}...{sid[-4:]}" if len(sid) > 20 else sid
            con.print(f"  {i}. [cyan]{display_id}[/cyan]  {ts_str}  ({size_kb:.0f}KB)")
        con.print(f"\n[dim]Usage: /resume <number> or /resume <session_id>[/dim]")
        return


def _cmd_clear(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    os.system('clear' if os.name != 'nt' else 'cls')
    current_agent = create_agent(ctx.agent_config, ctx.extra_tools, ctx.workspace, ctx.skills_registry)
    print_header(
        ctx.agent_config["model_provider"],
        ctx.agent_config["model_name"],
        work_dir=ctx.agent_config.get("work_dir"),
        extra_tools=ctx.extra_tool_names,
        shell_mode=ctx.shell_mode,
    )
    con.print("[info]Screen cleared and conversation reset.[/info]")
    return {"current_agent": current_agent}


def _cmd_model(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    supported_providers = set(MODEL_REGISTRY.keys())
    if cmd_args:
        if "/" in cmd_args:
            new_provider, new_model = cmd_args.split("/", 1)
            new_provider = new_provider.strip().lower()
            new_model = new_model.strip()
        else:
            new_model = cmd_args.strip()
            new_provider = ctx.agent_config["model_provider"]

        if new_provider not in supported_providers:
            con.print(f"[red]Unknown provider: {new_provider}[/red]")
            con.print(f"Supported: {', '.join(sorted(supported_providers))}", style="dim")
            return

        ctx.agent_config["model_provider"] = new_provider
        ctx.agent_config["model_name"] = new_model

        new_model_obj = get_model(
            model_provider=new_provider,
            model_name=new_model,
            base_url=ctx.agent_config.get("base_url"),
            api_key=ctx.agent_config.get("api_key"),
            max_tokens=ctx.agent_config.get("max_tokens"),
            temperature=ctx.agent_config.get("temperature"),
        )
        if ctx.current_agent is not None:
            ctx.current_agent.model = new_model_obj
            _sanitize_history_for_model_switch(ctx.current_agent)
            con.print(f"[green]Switched to: {new_provider}/{new_model} (session preserved)[/green]")
            return {"model_switched": True}
        else:
            current_agent = create_agent(ctx.agent_config, ctx.extra_tools, ctx.workspace, ctx.skills_registry)
            con.print(f"[green]Switched to: {new_provider}/{new_model}[/green]")
            return {"current_agent": current_agent}
    else:
        con.print(f"Current model: [bold cyan]{ctx.agent_config['model_provider']}/{ctx.agent_config['model_name']}[/bold cyan]")
        con.print()
        con.print("Supported providers and example models:", style="cyan")
        for provider in sorted(EXAMPLE_MODELS.keys()):
            marker = " [current]" if provider == ctx.agent_config["model_provider"] else ""
            models_str = ", ".join(EXAMPLE_MODELS[provider][:3]) + ", ..."
            con.print(f"  {provider}{marker}: [dim]{models_str}[/dim]")
        con.print()
        con.print("Usage: /model <provider>/<model>  (any model name accepted)", style="dim")
        con.print("Examples: /model openai/gpt-5, /model deepseek/deepseek-chat", style="dim")


def _cmd_compact(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    agent = ctx.current_agent
    if not agent or not agent.working_memory:
        con.print("[yellow]No conversation history to compact.[/yellow]")
        return

    messages = agent.working_memory.messages
    msg_count = len(messages)
    if msg_count == 0:
        con.print("[yellow]No messages to compact.[/yellow]")
        return

    custom_instructions = cmd_args.strip() if cmd_args else None
    cm = agent.tool_config.compression_manager if agent.tool_config else None

    if cm is not None:
        con.print(f"[dim]Compacting {msg_count} messages with LLM summary...[/dim]")
        model = agent.model
        wm = agent.working_memory

        compacted = _run_async_safe(cm.auto_compact(
            messages, model=model, force=True, working_memory=wm,
            custom_instructions=custom_instructions,
        ))
        if compacted:
            con.print(f"[green]Context compacted: {msg_count} messages -> {len(messages)} summary.[/green]")
        else:
            con.print("[yellow]Compaction failed. Falling back to rule-based.[/yellow]")
            _rule_based_compact(agent, messages, msg_count)
    else:
        con.print(f"[dim]Compacting {msg_count} messages (rule-based)...[/dim]")
        _rule_based_compact(agent, messages, msg_count)

    con.print("[dim]Workspace memory preserved.[/dim]")


def _rule_based_compact(current_agent, messages, msg_count):
    con = get_console()
    keep_recent = 6
    if msg_count <= keep_recent:
        con.print("[yellow]Too few messages to compact.[/yellow]")
        return

    old_messages = messages[:-keep_recent]
    recent_messages = messages[-keep_recent:]

    summary_parts = []
    for msg in old_messages:
        content = msg.content or ''
        if isinstance(content, str) and content:
            preview = content[:300] + "..." if len(content) > 300 else content
            summary_parts.append(f"[{msg.role}] {preview}")

    if summary_parts:
        summary = "Previous conversation summary:\n" + "\n".join(summary_parts)
        messages.clear()
        messages.append(Message(role="user", content=f"[Context compressed]\n\n{summary}"))
        messages.append(Message(role="assistant", content="Understood. I have the conversation context. Continuing."))
        messages.extend(recent_messages)
        con.print(f"[green]Context compacted: {msg_count} messages -> {len(messages)} messages.[/green]")
    else:
        messages.clear()
        con.print(f"[green]Context cleared ({msg_count} messages).[/green]")


def _cmd_debug(ctx: CommandContext, cmd_args: str = ""):
    _cmd_title("/debug")
    con = get_console()
    con.print("[bold cyan]Debug Info[/bold cyan]")
    con.print(f"  Model: {ctx.agent_config['model_provider']}/{ctx.agent_config['model_name']}")
    con.print(f"  Shell Mode: {'[green]ON[/green]' if ctx.shell_mode else '[dim]OFF[/dim]'}")
    con.print(f"  Work Dir: {ctx.agent_config.get('work_dir') or os.getcwd()}")
    agent = ctx.current_agent
    if agent and agent.working_memory:
        msg_count = len(agent.working_memory.messages)
        con.print(f"  History Messages: {msg_count}")
    if agent and agent.tools:
        con.print(f"  Extra Tools: {len(agent.tools)}")
    if ctx.workspace:
        con.print(f"  Workspace: {ctx.workspace.path}")
    if ctx.skills_registry:
        con.print(f"  Skills Loaded: {len(ctx.skills_registry)}")


def _cmd_reload_skills(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    result = _refresh_skills_session(ctx)
    con.print(f"Reloaded {len(result['skills_registry'])} skills from disk.", style="green")
    return result


def _cmd_cost(ctx: CommandContext, cmd_args: str = ""):
    """Display detailed token usage and cost for the current session."""
    _cmd_title("/usage")
    con = get_console()
    tracker = ctx.current_agent.run_response.cost_tracker if ctx.current_agent else None

    if tracker is None or tracker.turns == 0:
        con.print("[yellow]No usage data yet.[/yellow]")
        return

    model_name = f"{ctx.agent_config.get('model_provider', '')}/{ctx.agent_config.get('model_name', '')}"

    total_cache_read = 0
    total_cache_write = 0
    for stat in tracker.model_usage.values():
        total_cache_read += stat.cache_read_tokens
        total_cache_write += stat.cache_write_tokens

    prompt_total = tracker.total_input_tokens + total_cache_read + total_cache_write
    total_all = prompt_total + tracker.total_output_tokens

    ts = ctx.tui_state or {}
    ctx_tokens = ts.get("context_tokens", 0)
    ctx_window = ts.get("context_window", 128000)
    ctx_pct = (ctx_tokens / ctx_window * 100) if ctx_window > 0 else 0
    active_secs = ts.get("active_seconds", 0)

    msg_count = 0
    if ctx.current_agent:
        msg_count = len(ctx.current_agent.working_memory.messages)

    if active_secs < 60:
        duration_str = f"{active_secs:.0f}s"
    elif active_secs < 3600:
        m, s = divmod(int(active_secs), 60)
        duration_str = f"{m}m {s:02d}s"
    else:
        h, rem = divmod(int(active_secs), 3600)
        m, _ = divmod(rem, 60)
        duration_str = f"{h}h {m:02d}m"

    session_cost = ts.get("cost_usd", 0.0) if ts else tracker.total_cost_usd
    cost_str = f"~${session_cost:.4f}"

    sep = "─" * 42
    con.print()
    con.print("  [bold cyan]Session Token Usage[/bold cyan]")
    con.print(f"  {sep}")
    con.print(f"  {'Model:':<30} {model_name}")
    con.print(f"  {'Input tokens:':<30} {tracker.total_input_tokens:>12,}")
    if total_cache_read > 0:
        con.print(f"  {'Cache read tokens:':<30} {total_cache_read:>12,}")
    if total_cache_write > 0:
        con.print(f"  {'Cache write tokens:':<30} {total_cache_write:>12,}")
    con.print(f"  {'Output tokens:':<30} {tracker.total_output_tokens:>12,}")
    con.print(f"  {'Prompt tokens (total):':<30} {prompt_total:>12,}")
    con.print(f"  {'Total tokens:':<30} {total_all:>12,}")
    con.print(f"  {'API calls:':<30} {ts.get('total_api_calls', tracker.turns):>12}")
    con.print(f"  {'Session duration:':<30} {duration_str:>12}")
    con.print(f"  {'Total cost:':<30} {cost_str}")
    con.print(f"  {sep}")
    con.print(f"  Current context:  {ctx_tokens:,} / {ctx_window:,} ({ctx_pct:.0f}%)")
    con.print(f"  Messages:         {msg_count}")
    con.print()


def _cmd_export(ctx: CommandContext, cmd_args: str = ""):
    """Save conversation history to a JSON file (excludes system prompts)."""
    con = get_console()
    agent = ctx.current_agent
    if not agent:
        con.print("[yellow]No conversation to save.[/yellow]")
        return

    messages = agent.working_memory.messages
    export_msgs = []
    for msg in messages:
        if msg.role == "system":
            continue
        content = msg.content or ""
        if isinstance(content, list):
            content = str(content)
        if isinstance(content, str):
            content = content.strip()
        entry = {"role": msg.role, "content": content}
        if msg.tool_calls:
            entry["tool_calls"] = len(msg.tool_calls)
        export_msgs.append(entry)

    if not export_msgs:
        con.print("[yellow]No messages to save.[/yellow]")
        return

    filename = cmd_args.strip() if cmd_args.strip() else f"conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    if not filename.endswith(".json"):
        filename += ".json"

    model_name = f"{ctx.agent_config.get('model_provider', '')}/{ctx.agent_config.get('model_name', '')}"

    data = {
        "model": model_name,
        "session_id": agent.session_id,
        "exported_at": datetime.now().isoformat(),
        "messages": export_msgs,
    }
    Path(filename).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    con.print(f"  [green]Saved {len(export_msgs)} messages to {filename}[/green]")


def _cmd_permissions(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    if cmd_args.strip():
        new_mode = cmd_args.strip().lower()
        if new_mode not in ("allow-all", "auto", "strict"):
            con.print(f"[red]Invalid mode: {new_mode}. Use: allow-all, auto, strict[/red]")
            return
        if ctx.permission_manager:
            ctx.permission_manager.mode = new_mode
            ctx.permission_manager.session_allowed.clear()
            con.print(f"[green]Permission mode set to: {new_mode}[/green]")
        return

    if ctx.permission_manager:
        con.print(f"[bold cyan]Permission Mode: {ctx.permission_manager.mode}[/bold cyan]")
        if ctx.permission_manager.session_allowed:
            con.print(f"  Session-allowed tools: {', '.join(sorted(ctx.permission_manager.session_allowed))}")
        con.print()
        con.print("  [dim]allow-all[/dim]  - auto-approve everything")
        con.print("  [dim]auto[/dim]      - prompt for write/execute, auto-approve reads")
        con.print("  [dim]strict[/dim]    - prompt for every tool call")
        con.print()
        con.print("Usage: /permissions <mode>", style="dim")


def _cmd_yolo(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    if not ctx.permission_manager:
        return
    if ctx.permission_manager.mode == "allow-all":
        ctx.permission_manager.mode = "auto"
        ctx.permission_manager.session_allowed.clear()
        con.print("[cyan]YOLO OFF[/cyan] -- back to auto-approve mode")
    else:
        ctx.permission_manager.mode = "allow-all"
        con.print("[bold yellow]YOLO ON[/bold yellow] -- all tool calls auto-approved")


def _cmd_paste(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    if ctx.attached_images is None or ctx.image_counter is None:
        con.print("[dim]Image paste not available.[/dim]")
        return
    from agentica.cli.clipboard import has_clipboard_image
    from agentica.cli.interactive import _try_attach_clipboard_image
    if has_clipboard_image():
        if _try_attach_clipboard_image(ctx.attached_images, ctx.image_counter):
            img = ctx.attached_images[-1]
            size_kb = img.stat().st_size // 1024 if img.exists() else 0
            con.print(f"  [green]Image #{len(ctx.attached_images)} attached: {img.name} ({size_kb}KB)[/green]")
        else:
            con.print("  [dim]Clipboard has an image but extraction failed.[/dim]")
    else:
        con.print("  [dim]No image found in clipboard.[/dim]")


def _cmd_image(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    if ctx.attached_images is None or ctx.image_counter is None:
        con.print("[dim]Image attachment not available.[/dim]")
        return
    raw_args = cmd_args.strip()
    if not raw_args:
        con.print("  [dim]Usage: /image <path>  e.g. /image /path/to/image.png[/dim]")
        return

    from agentica.cli.interactive import _split_path_input, _resolve_attachment_path
    path_token, _ = _split_path_input(raw_args)
    image_path = _resolve_attachment_path(path_token)
    if image_path is None:
        con.print(f"  [dim]File not found: {path_token}[/dim]")
        return
    if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        con.print(f"  [dim]Not a supported image file: {image_path.name}[/dim]")
        return

    ctx.attached_images.append(image_path)
    ctx.image_counter[0] += 1
    con.print(f"  [green]Attached image: {image_path.name}[/green]")


def _extract_queue_text(item) -> str:
    """Extract display text from a queue payload (str, tuple, etc.)."""
    if isinstance(item, tuple):
        if item[0] == "__BTW__":
            return str(item[1])
        return str(item[0])  # (text, images)
    return str(item)


def _cmd_queue(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    pq = ctx.pending_queue
    args = cmd_args.strip()

    if not args:
        items = pq.peek_all() if pq else []
        if items:
            con.print(f"  [cyan]Queued messages ({len(items)}):[/cyan]")
            for i, item in enumerate(items):
                preview = _extract_queue_text(item)[:80]
                con.print(f"    {i + 1}. [dim]{preview}[/dim]")
            con.print()
        con.print("  [dim]Usage: /queue <prompt>  |  /queue list  |  /queue clear  |  /queue remove <n>[/dim]")
        return

    sub = args.split(maxsplit=1)
    subcommand = sub[0].lower()

    if subcommand == "list":
        if pq is None or pq.empty():
            con.print("  [dim]Queue is empty.[/dim]")
            return
        items = pq.peek_all()
        con.print(f"  [cyan]Queued messages ({len(items)}):[/cyan]")
        for i, item in enumerate(items):
            con.print(f"    {i + 1}. [dim]{_extract_queue_text(item)[:80]}[/dim]")
        return

    if subcommand == "clear":
        if pq is None:
            return
        n = pq.qsize()
        pq.clear()
        con.print(f"  [green]Cleared {n} queued message(s).[/green]")
        return

    if subcommand == "remove":
        if pq is None:
            return
        idx_str = sub[1].strip() if len(sub) > 1 else ""
        if not idx_str.isdigit():
            con.print("  [dim]Usage: /queue remove <number>[/dim]")
            return
        idx = int(idx_str) - 1
        if pq.remove_index(idx):
            con.print(f"  [green]Removed queued message #{idx + 1}.[/green]")
        else:
            con.print(f"  [red]Invalid index: {idx + 1}[/red]")
        return

    # Default: queue a prompt
    pq.put(args)
    preview = args[:80] + ("..." if len(args) > 80 else "")
    if not ctx.agent_running:
        con.print(f"  Queued: {preview}")


def _cmd_reasoning(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    if ctx.tui_state is None:
        return
    arg = cmd_args.strip().lower()
    if not arg:
        state = "ON" if ctx.tui_state.get("show_reasoning", True) else "OFF"
        con.print(f"  Reasoning display: [bold]{state}[/bold]")
        con.print("  [dim]Usage: /reasoning on|off[/dim]")
        return
    if arg in ("show", "on", "true", "1"):
        ctx.tui_state["show_reasoning"] = True
        con.print("  [green]Reasoning display: ON[/green]")
    elif arg in ("hide", "off", "false", "0"):
        ctx.tui_state["show_reasoning"] = False
        con.print("  [green]Reasoning display: OFF[/green]")
    else:
        con.print(f"  [dim]Unknown argument: {arg}. Use: on, off[/dim]")


def _cmd_retry(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    agent = ctx.current_agent
    if not agent:
        con.print("[yellow]No conversation to retry.[/yellow]")
        return
    wm = agent.working_memory
    last_user_msg = None
    for msg in reversed(wm.messages):
        if msg.role == "user":
            last_user_msg = msg
            break
    if last_user_msg is None or not last_user_msg.content:
        con.print("[yellow]No user message found to retry.[/yellow]")
        return
    user_text = last_user_msg.content if isinstance(last_user_msg.content, str) else str(last_user_msg.content)
    if wm.runs:
        wm.runs.pop()
    while wm.messages and wm.messages[-1].role in ("assistant", "tool"):
        wm.messages.pop()
    if wm.messages and wm.messages[-1].role == "user":
        wm.messages.pop()
    if ctx.pending_queue is not None:
        ctx.pending_queue.put(user_text)
        preview = user_text[:60] + ("..." if len(user_text) > 60 else "")
        con.print(f"  [green]Retrying: {preview}[/green]")


def _cmd_undo(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    agent = ctx.current_agent
    if not agent:
        con.print("[yellow]No conversation history.[/yellow]")
        return
    wm = agent.working_memory
    if not wm.messages:
        con.print("[yellow]No messages to undo.[/yellow]")
        return
    if wm.runs:
        wm.runs.pop()
    removed = 0
    while wm.messages and wm.messages[-1].role in ("assistant", "tool"):
        wm.messages.pop()
        removed += 1
    if wm.messages and wm.messages[-1].role == "user":
        wm.messages.pop()
        removed += 1
    if removed > 0:
        con.print(f"  [green]Undone last exchange ({removed} messages removed).[/green]")
    else:
        con.print("[yellow]Nothing to undo.[/yellow]")


def _cmd_btw(ctx: CommandContext, cmd_args: str = ""):
    """Ephemeral side question — dispatched as concurrent task, no tools, not persisted."""
    con = get_console()
    question = cmd_args.strip()
    if not question:
        con.print("  [dim]Usage: /btw <question>[/dim]")
        return
    if ctx.current_agent is None:
        con.print("[yellow]No active agent.[/yellow]")
        return
    if ctx.pending_queue is not None:
        ctx.pending_queue.put(("__BTW__", question))
        con.print(f"  [dim]Side question: {question[:60]}{'...' if len(question) > 60 else ''}[/dim]")


def _cmd_statusbar(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    if ctx.tui_state is None:
        return
    current = ctx.tui_state.get("statusbar_visible", True)
    ctx.tui_state["statusbar_visible"] = not current
    state = "OFF" if current else "ON"
    con.print(f"  [green]Status bar: {state}[/green]")


def _cmd_background(ctx: CommandContext, cmd_args: str = ""):
    """Run a prompt in the background (independent agent with context snapshot)."""
    con = get_console()
    prompt = cmd_args.strip()
    if not prompt:
        if ctx.bg_tasks:
            con.print(f"  [cyan]Active background tasks ({len(ctx.bg_tasks)}):[/cyan]")
            for tid, info in ctx.bg_tasks.items():
                con.print(f"    #{info['num']} [dim]{tid}[/dim] {info['prompt'][:60]}")
        else:
            con.print("  [dim]No active background tasks.[/dim]")
        con.print("  [dim]Usage: /background <prompt>  |  /stop to kill all[/dim]")
        return

    ctx.bg_task_counter += 1
    task_num = ctx.bg_task_counter
    task_id = f"bg_{datetime.now().strftime('%H%M%S')}_{task_num}"

    ctx.bg_tasks[task_id] = {"thread": None, "agent": None, "prompt": prompt, "num": task_num}

    # Capture references needed by the background thread
    agent_config = ctx.agent_config
    extra_tools = ctx.extra_tools
    workspace = ctx.workspace
    skills_registry = ctx.skills_registry
    bg_tasks = ctx.bg_tasks

    # Snapshot current conversation context for the background agent.
    # History is loaded via working_memory.runs (not .messages) by the runner,
    # so we must inject a synthetic AgentRun with the snapshot messages.
    context_snapshot = []
    main_agent = ctx.current_agent
    if main_agent and main_agent.working_memory and main_agent.working_memory.messages:
        for msg in main_agent.working_memory.messages:
            if msg.role in ("user", "assistant") and msg.content:
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if len(content) > 500:
                    content = content[:500] + "..."
                context_snapshot.append(Message(role=msg.role, content=content))
        # Keep only last 10 messages to avoid blowing up context
        context_snapshot = context_snapshot[-10:]

    def _run_bg():
        bg_config = dict(agent_config)
        bg_config["session_id"] = _generate_session_id()
        bg_config["debug"] = False
        bg_agent = create_agent(bg_config, extra_tools, workspace, skills_registry)
        bg_tasks[task_id]["agent"] = bg_agent

        # Inject context snapshot as a synthetic AgentRun so the runner
        # picks it up via get_messages_from_last_n_runs().
        if context_snapshot:
            synthetic_run = AgentRun(
                response=RunResponse(messages=context_snapshot),
            )
            bg_agent.working_memory.runs.append(synthetic_run)

        result_text = ""
        try:
            response = bg_agent.run_sync(prompt)
            result_text = response.content if response else ""
        except Exception as e:
            if bg_agent._cancelled:
                result_text = "(cancelled)"
            else:
                result_text = f"Error: {e}"
        finally:
            bg_tasks.pop(task_id, None)

        # Use shared box printer from interactive
        from agentica.cli.interactive import _print_boxed_result
        _print_boxed_result(
            f"Background #{task_num}", prompt, result_text or "",
            color="bright_magenta",
        )

    thread = threading.Thread(target=_run_bg, daemon=True, name=task_id)
    bg_tasks[task_id]["thread"] = thread
    thread.start()
    preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
    con.print(f"  [green]Background #{task_num} started:[/green] {preview}")


def _cmd_stop(ctx: CommandContext, cmd_args: str = ""):
    con = get_console()
    if not ctx.bg_tasks:
        con.print("  [dim]No running background tasks.[/dim]")
        return
    count = len(ctx.bg_tasks)
    for tid, info in list(ctx.bg_tasks.items()):
        agent = info.get("agent")
        if agent is not None:
            agent.cancel()
    con.print(f"  [green]Stopped {count} background task(s).[/green]")


# ==================== Command Registry ====================

COMMAND_REGISTRY = {
    # Session
    "/new":           (_cmd_newchat,       "Start a new chat session"),
    "/clear":         (_cmd_clear,         "Clear screen and reset"),
    "/reset":         (_cmd_clear,         "Clear screen and reset (alias)"),
    "/history":       (_cmd_history,       "Show conversation history"),
    "/export":        (_cmd_export,        "Save conversation to JSON"),
    "/save":          (_cmd_export,        "Save conversation to JSON (alias)"),
    "/retry":         (_cmd_retry,         "Retry the last message (resend to agent)"),
    "/undo":          (_cmd_undo,          "Remove the last user/assistant exchange"),
    "/compact":       (_cmd_compact,       "Compact context (summarize history)"),
    "/resume":        (_cmd_resume,        "Resume a previous session"),
    "/btw":           (_cmd_btw,           "Ephemeral side question (no tools, not persisted)"),
    "/queue":         (_cmd_queue,         "Queue management: <prompt> | list | clear | remove <n>"),
    "/q":             (_cmd_queue,         "Queue management (alias)"),
    "/background":    (_cmd_background,    "Run a prompt in background (independent agent)"),
    "/bg":            (_cmd_background,    "Run a prompt in background (alias)"),
    "/stop":          (_cmd_stop,          "Kill all running background tasks"),
    # Model & Config
    "/model":         (_cmd_model,         "View or switch model"),
    "/config":        (_cmd_workspace,     "Show current configuration"),
    "/cost":          (_cmd_cost,          "Show token usage and cost"),
    "/usage":         (_cmd_cost,          "Show token usage and cost (alias)"),
    "/debug":         (_cmd_debug,         "Show debug info"),
    "/reasoning":     (_cmd_reasoning,     "Toggle reasoning display: on | off"),
    "/statusbar":     (_cmd_statusbar,     "Toggle the status bar visibility"),
    "/sb":            (_cmd_statusbar,     "Toggle the status bar (alias)"),
    # Tools & Skills
    "/tools":         (_cmd_tools,         "List available tools"),
    "/skills":        (_cmd_skills,        "Manage skills: list | install | remove | inspect | reload"),
    "/extensions":    (_cmd_skills,        "Manage skills (alias for /skills)"),
    # Permissions
    "/permissions":   (_cmd_permissions,   "View or set permission mode"),
    "/yolo":          (_cmd_yolo,          "Toggle YOLO mode (auto-approve all)"),
    # Media
    "/paste":         (_cmd_paste,         "Paste image from clipboard"),
    "/image":         (_cmd_image,         "Attach a local image file"),
    # Other
    "/help":          (_cmd_help,          "Show available commands"),
    "/exit":          (_cmd_exit,          "Exit the CLI"),
    "/quit":          (_cmd_exit,          "Exit the CLI (alias)"),
}

COMMAND_HANDLERS = {cmd: handler for cmd, (handler, _) in COMMAND_REGISTRY.items()}
