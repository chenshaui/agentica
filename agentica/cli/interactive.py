# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: CLI interactive mode - main interaction loop
"""
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import time
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import List, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style as PTStyle

from agentica.cli.config import (
    console,
    history_file,
    TOOL_REGISTRY,
    MODEL_REGISTRY,
    EXAMPLE_MODELS,
    configure_tools,
    create_agent,
)
from agentica.cli.display import (
    COLORS,
    StreamDisplayManager,
    build_status_bar_fragments,
    print_header,
    show_help,
    parse_file_mentions,
    inject_file_contents,
    display_user_message,
    get_file_completions,
)
from agentica.cli.permissions import PermissionManager
from agentica.model.message import Message
from agentica.run_response import AgentCancelledError
from agentica.utils.log import suppress_console_logging
from agentica.workspace import Workspace
from agentica.skills import (
    get_skill_registry,
    install_skills,
    list_installed_skills,
    load_skills,
    remove_skill,
)
from agentica.skills.skill_registry import reset_skill_registry


# ==================== Image Attachment Helpers ====================

IMAGE_EXTENSIONS = frozenset({
    '.png', '.jpg', '.jpeg', '.gif', '.webp',
    '.bmp', '.tiff', '.tif', '.svg', '.ico',
})


def _split_path_input(raw: str) -> tuple:
    """Split a leading file path token from trailing free-form text.

    Supports quoted paths and backslash-escaped spaces, e.g.
      /tmp/pic.png describe this
      ~/Photos/My\\ Photo/cat.png what is this?
      "/path/to/image file.png" summarize
    """
    raw = str(raw or "").strip()
    if not raw:
        return "", ""

    if raw[0] in {'"', "'"}:
        quote = raw[0]
        pos = 1
        while pos < len(raw):
            ch = raw[pos]
            if ch == '\\' and pos + 1 < len(raw):
                pos += 2
                continue
            if ch == quote:
                token = raw[1:pos]
                remainder = raw[pos + 1:].strip()
                return token, remainder
            pos += 1
        return raw[1:], ""

    pos = 0
    while pos < len(raw):
        ch = raw[pos]
        if ch == '\\' and pos + 1 < len(raw) and raw[pos + 1] == ' ':
            pos += 2
        elif ch == ' ':
            break
        else:
            pos += 1

    token = raw[:pos].replace('\\ ', ' ')
    remainder = raw[pos:].strip()
    return token, remainder


def _resolve_attachment_path(raw_path: str) -> Optional[Path]:
    """Resolve a user-supplied local attachment path.

    Accepts quoted or unquoted paths, expands ~ and env vars.
    Returns None when the path does not resolve to an existing file.
    """
    token = str(raw_path or "").strip()
    if not token:
        return None
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        token = token[1:-1].strip()
    if not token:
        return None

    expanded = os.path.expandvars(os.path.expanduser(token))
    path = Path(expanded)
    if not path.is_absolute():
        path = Path(os.getcwd()) / path

    try:
        resolved = path.resolve()
    except Exception:
        resolved = path

    if not resolved.exists() or not resolved.is_file():
        return None
    return resolved


def _detect_file_drop(user_input: str) -> Optional[dict]:
    """Detect if user_input starts with a real local file path.

    Returns a dict on match::
        {"path": Path, "is_image": bool, "remainder": str}
    Returns None when the input is not a real file path.
    """
    if not isinstance(user_input, str):
        return None
    stripped = user_input.strip()
    if not stripped:
        return None

    starts_like_path = (
        stripped.startswith("/")
        or stripped.startswith("~")
        or stripped.startswith("./")
        or stripped.startswith("../")
        or stripped.startswith('"/')
        or stripped.startswith('"~')
        or stripped.startswith("'/")
        or stripped.startswith("'~")
    )
    if not starts_like_path:
        return None

    first_token, remainder = _split_path_input(stripped)
    drop_path = _resolve_attachment_path(first_token)
    if drop_path is None:
        return None

    return {
        "path": drop_path,
        "is_image": drop_path.suffix.lower() in IMAGE_EXTENSIONS,
        "remainder": remainder,
    }


def _try_attach_clipboard_image(attached_images: list, image_counter: list) -> bool:
    """Check clipboard for an image and attach it if found.

    Saves the image to ~/.agentica/images/ and appends the path.
    Returns True if an image was attached.
    """
    from agentica.cli.clipboard import save_clipboard_image
    from agentica.config import AGENTICA_HOME

    img_dir = Path(AGENTICA_HOME) / "images"
    image_counter[0] += 1
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    img_path = img_dir / f"clip_{ts}_{image_counter[0]}.png"

    if save_clipboard_image(img_path):
        attached_images.append(img_path)
        return True
    image_counter[0] -= 1
    return False


def _sanitize_history_for_model_switch(agent) -> None:
    """Strip tool_calls and tool messages from working memory history.

    Different models use different tool_call_id formats.  When hot-swapping
    models within the same session, leftover tool messages cause errors like
    "tool call id ... is duplicated".  This converts the history to plain
    user/assistant text so any model can understand it.
    """
    wm = agent.working_memory
    for run in wm.runs:
        if not run.response or not run.response.messages:
            continue
        cleaned = []
        for msg in run.response.messages:
            if msg.role == "tool":
                # Drop tool result messages entirely
                continue
            if msg.role == "assistant" and msg.tool_calls:
                # Keep assistant text content but strip tool_calls
                text = msg.content if isinstance(msg.content, str) else ""
                if text:
                    cleaned.append(Message(role="assistant", content=text))
                continue
            if msg.role == "system":
                # System messages are regenerated per-turn, skip
                continue
            cleaned.append(msg)
        run.response.messages = cleaned


# ==================== Command Handlers ====================

def _cmd_help(**kwargs):
    show_help()


def _cmd_exit(**kwargs):
    return "EXIT"


def _cmd_tools(extra_tool_names=None, **kwargs):
    console.print("Available additional tools:", style="cyan")
    for name in sorted(TOOL_REGISTRY.keys()):
        marker = " [active]" if extra_tool_names and name in extra_tool_names else ""
        console.print(f"  - {name}{marker}")
    console.print()
    console.print("Use --tools <name> when starting CLI to enable tools.", style="dim")


def _cmd_skills(skills_registry=None, current_agent=None, **kwargs):
    shown = False

    # Show skills from external registry (--enable-skills)
    if skills_registry and len(skills_registry) > 0:
        console.print("External Skills:", style="cyan")
        for skill in skills_registry.list_all():
            trigger_info = f" (trigger: [green]{skill.trigger}[/green])" if skill.trigger else ""
            console.print(f"  - [bold]{skill.name}[/bold]{trigger_info}")
            console.print(f"    {skill.description[:60]}...", style="dim")
        console.print()
        triggers = skills_registry.list_triggers()
        if triggers:
            console.print("Triggers:", style="cyan")
            for trigger, skill_name in triggers.items():
                console.print(f"  {trigger} -> {skill_name}")
        shown = True

    # Show skills from agent's SkillTool (built-in auto-loaded skills)
    if current_agent and hasattr(current_agent, 'tools') and current_agent.tools:
        from agentica.tools.skill_tool import SkillTool
        for tool in current_agent.tools:
            if isinstance(tool, SkillTool):
                agent_skills = tool._get_enabled_skills()
                if agent_skills:
                    console.print("Agent Skills:", style="cyan")
                    for skill in agent_skills:
                        trigger_info = f" (trigger: [green]{skill.trigger}[/green])" if skill.trigger else ""
                        console.print(f"  - [bold]{skill.name}[/bold]{trigger_info}")
                        console.print(f"    {skill.description[:60]}...", style="dim")
                    console.print(f"  [dim]Total: {len(agent_skills)} skills[/dim]")
                    shown = True
                break

    if not shown:
        console.print("No skills loaded. Use --enable-skills to enable.", style="yellow")


def _cmd_memory(current_agent=None, **kwargs):
    if hasattr(current_agent, 'working_memory') and current_agent.working_memory:
        messages = current_agent.working_memory.messages if hasattr(current_agent.working_memory, 'messages') else []
        if messages:
            console.print(f"[bold cyan]Conversation History ({len(messages)} messages)[/bold cyan]")
            console.print()
            for i, msg in enumerate(messages[-20:], 1):
                role = msg.role
                content = msg.content or ''
                
                role_colors = {'user': '[cyan]User[/cyan]', 'assistant': '[green]Assistant[/green]',
                               'system': '[yellow]System[/yellow]'}
                role_display = role_colors.get(role, f"[dim]{role}[/dim]")
                
                if isinstance(content, str):
                    preview = content[:300] + "..." if len(content) > 300 else content
                elif isinstance(content, list):
                    preview = str(content)[:300] + "..."
                else:
                    preview = str(content)[:300]
                
                console.print(f"  {role_display}: {preview}")
                console.print()
        else:
            console.print("[yellow]No conversation history yet.[/yellow]")
    else:
        console.print("[yellow]No memory available.[/yellow]")


def _cmd_workspace(workspace=None, **kwargs):
    if workspace:
        console.print(f"Workspace: [bold]{workspace.path}[/bold]", style="cyan")
        console.print(f"User: [cyan]{workspace.user_id or 'default'}[/cyan]")
        console.print(f"Exists: {'Yes' if workspace.exists() else 'No'}")
        if workspace.exists():
            files = workspace.list_files()
            console.print("Config Files:", style="cyan")
            for fname, exists in files.items():
                status_icon = "✓" if exists else "✗"
                console.print(f"  {status_icon} {fname}")
            
            memory_files = workspace.get_all_memory_files()
            console.print(f"User Memory:", style="cyan")
            if memory_files:
                for mf in memory_files[:5]:
                    if mf.name == "MEMORY.md":
                        console.print(f"  ✓ {mf.name} [dim](long-term)[/dim]")
                    else:
                        console.print(f"  ✓ {mf.name} [dim](daily)[/dim]")
                if len(memory_files) > 5:
                    console.print(f"  ... and {len(memory_files) - 5} more")
            else:
                console.print("  [dim]No memory files yet[/dim]")
            console.print()
    else:
        console.print("Workspace not configured.", style="yellow")


def _cmd_newchat(agent_config=None, extra_tools=None, workspace=None, skills_registry=None, **kwargs):
    current_agent = create_agent(agent_config, extra_tools, workspace, skills_registry)
    console.print("[green]New chat session created.[/green]")
    console.print("[dim]Conversation history cleared.[/dim]")
    return {"current_agent": current_agent}


def _cmd_resume(cmd_args=None, agent_config=None, extra_tools=None, workspace=None,
                skills_registry=None, **kwargs):
    """Resume a previous session from JSONL log.

    Usage:
        /resume              — list sessions
        /resume <session_id> — resume entire session
        /resume <number>     — resume session by list number
        /resume <session_id> at <uuid> — resume truncated at specific message
    """
    from agentica.memory.session_log import SessionLog

    sessions = SessionLog.list_sessions()
    if not sessions:
        console.print("[yellow]No sessions found to resume.[/yellow]")
        return

    args_str = (cmd_args or "").strip()

    # Parse "session_id at uuid" syntax
    resume_at_uuid = None
    if " at " in args_str:
        parts = args_str.split(" at ", 1)
        args_str = parts[0].strip()
        resume_at_uuid = parts[1].strip()

    if args_str:
        # Try number first
        try:
            idx = int(args_str) - 1
            if 0 <= idx < len(sessions):
                chosen = sessions[idx]
            else:
                console.print("[red]Invalid number.[/red]")
                return
        except ValueError:
            # Match by session_id substring
            matching = [s for s in sessions if args_str in s["session_id"]]
            if not matching:
                console.print(f"[red]No session matching '{args_str}'[/red]")
                return
            chosen = matching[0]

        # If no at_uuid specified, show user messages for query-granularity resume
        if resume_at_uuid is None:
            log = SessionLog(chosen["session_id"])
            user_msgs = log.list_user_messages(limit=10)
            if user_msgs:
                console.print(f"\n[bold]Session: {chosen['session_id']}[/bold]")
                console.print("[dim]Recent queries (resume from any point):[/dim]\n")
                for i, m in enumerate(user_msgs, 1):
                    ts = m.get("timestamp", "")[:19].replace("T", " ") if m.get("timestamp") else ""
                    console.print(f"  {i}. [dim]{ts}[/dim] {m['content']}")
                console.print(f"\n[dim]Usage: /resume {chosen['session_id']} at <uuid>[/dim]")
                console.print(f"[dim]Or just press Enter to resume from the end.[/dim]")

        # Create agent with chosen session — auto-resumes from JSONL
        agent_config = dict(agent_config)
        agent_config["session_id"] = chosen["session_id"]
        agent_config["_resume_at_uuid"] = resume_at_uuid  # passed to runner
        current_agent = create_agent(agent_config, extra_tools, workspace, skills_registry)

        # If resume_at specified, reload with truncation
        if resume_at_uuid and current_agent._session_log:
            current_agent.working_memory.clear()
            for rm in current_agent._session_log.load(resume_at=resume_at_uuid):
                current_agent.working_memory.add_message(
                    Message(role=rm["role"], content=rm.get("content", ""))
                )

        console.print(f"[green]Resumed session: {chosen['session_id']}"
                     f"{f' at {resume_at_uuid[:8]}...' if resume_at_uuid else ''}[/green]")
        return {"current_agent": current_agent}
    else:
        # Show session list
        console.print("\n[bold]Available sessions:[/bold]\n")
        for i, s in enumerate(sessions[:10], 1):
            ts_str = s.get("last_timestamp", "") or ""
            if ts_str:
                ts_str = ts_str[:19].replace("T", " ")
            size_kb = s["size_bytes"] / 1024
            sid = s["session_id"]
            # Truncate long UUIDs for display
            display_id = f"{sid[:8]}...{sid[-4:]}" if len(sid) > 20 else sid
            console.print(f"  {i}. [cyan]{display_id}[/cyan]  {ts_str}  ({size_kb:.0f}KB)")
        console.print(f"\n[dim]Usage: /resume <number> or /resume <session_id>[/dim]")
        return


def _cmd_clear(agent_config=None, extra_tools=None, extra_tool_names=None,
               workspace=None, skills_registry=None, shell_mode=False, **kwargs):
    os.system('clear' if os.name != 'nt' else 'cls')
    current_agent = create_agent(agent_config, extra_tools, workspace, skills_registry)
    print_header(
        agent_config["model_provider"],
        agent_config["model_name"],
        work_dir=agent_config.get("work_dir"),
        extra_tools=extra_tool_names,
        shell_mode=shell_mode
    )
    console.print("[info]Screen cleared and conversation reset.[/info]")
    return {"current_agent": current_agent}


def _cmd_model(cmd_args="", agent_config=None, extra_tools=None,
               workspace=None, skills_registry=None, current_agent=None, **kwargs):
    supported_providers = set(MODEL_REGISTRY.keys())
    if cmd_args:
        if "/" in cmd_args:
            new_provider, new_model = cmd_args.split("/", 1)
            new_provider = new_provider.strip().lower()
            new_model = new_model.strip()
        else:
            new_model = cmd_args.strip()
            new_provider = agent_config["model_provider"]
        
        if new_provider not in supported_providers:
            console.print(f"[red]Unknown provider: {new_provider}[/red]")
            console.print(f"Supported: {', '.join(sorted(supported_providers))}", style="dim")
            return
        
        agent_config["model_provider"] = new_provider
        agent_config["model_name"] = new_model

        # Hot-swap model on existing agent — preserves session & history
        from agentica.cli.config import get_model
        new_model_obj = get_model(
            model_provider=new_provider,
            model_name=new_model,
            base_url=agent_config.get("base_url"),
            api_key=agent_config.get("api_key"),
            max_tokens=agent_config.get("max_tokens"),
            temperature=agent_config.get("temperature"),
        )
        if current_agent is not None:
            current_agent.model = new_model_obj
            # Sanitize history: strip tool_calls and tool messages to avoid
            # cross-model tool_call_id conflicts (e.g., ZhipuAI → Moonshot).
            # Keep only user/assistant text so any model can understand the context.
            _sanitize_history_for_model_switch(current_agent)
            console.print(f"[green]Switched to: {new_provider}/{new_model} (session preserved)[/green]")
            # Return signal to update tui_state but NOT replace current_agent
            return {"model_switched": True}
        else:
            # No existing agent — create fresh
            current_agent = create_agent(agent_config, extra_tools, workspace, skills_registry)
            console.print(f"[green]Switched to: {new_provider}/{new_model}[/green]")
            return {"current_agent": current_agent}
    else:
        console.print(f"Current model: [bold cyan]{agent_config['model_provider']}/{agent_config['model_name']}[/bold cyan]")
        console.print()
        console.print("Supported providers and example models:", style="cyan")
        for provider in sorted(EXAMPLE_MODELS.keys()):
            marker = " [current]" if provider == agent_config["model_provider"] else ""
            models_str = ", ".join(EXAMPLE_MODELS[provider][:3]) + ", ..."
            console.print(f"  {provider}{marker}: [dim]{models_str}[/dim]")
        console.print()
        console.print("Usage: /model <provider>/<model>  (any model name accepted)", style="dim")
        console.print("Examples: /model openai/gpt-5, /model deepseek/deepseek-chat", style="dim")


def _cmd_compact(current_agent=None, cmd_args="", **kwargs):
    if not (hasattr(current_agent, 'working_memory') and current_agent.working_memory):
        console.print("[yellow]No conversation history to compact.[/yellow]")
        return

    messages = current_agent.working_memory.messages if hasattr(current_agent.working_memory, 'messages') else []
    msg_count = len(messages)
    if msg_count == 0:
        console.print("[yellow]No messages to compact.[/yellow]")
        return

    custom_instructions = cmd_args.strip() if cmd_args else None
    cm = current_agent.tool_config.compression_manager if hasattr(current_agent, 'tool_config') else None

    if cm is not None:
        # Use CompressionManager.auto_compact with LLM summary
        console.print(f"[dim]Compacting {msg_count} messages with LLM summary...[/dim]")
        model = current_agent.model if hasattr(current_agent, 'model') else None
        wm = current_agent.working_memory

        import asyncio
        compacted = asyncio.run(cm.auto_compact(
            messages,
            model=model,
            force=True,
            working_memory=wm,
            custom_instructions=custom_instructions,
        ))
        if compacted:
            console.print(f"[green]Context compacted: {msg_count} messages -> {len(messages)} summary.[/green]")
        else:
            console.print("[yellow]Compaction failed (LLM summary unavailable). Falling back to rule-based.[/yellow]")
            _rule_based_compact(current_agent, messages, msg_count)
    else:
        # Fallback: rule-based compact (no LLM)
        console.print(f"[dim]Compacting {msg_count} messages (rule-based)...[/dim]")
        _rule_based_compact(current_agent, messages, msg_count)

    console.print("[dim]Workspace memory preserved.[/dim]")


def _rule_based_compact(current_agent, messages, msg_count):
    """Fallback compact: keep recent messages, summarise old ones by truncation."""
    from agentica.model.message import Message

    # Keep last 6 messages as-is, summarise everything before
    keep_recent = 6
    if msg_count <= keep_recent:
        console.print("[yellow]Too few messages to compact.[/yellow]")
        return

    old_messages = messages[:-keep_recent]
    recent_messages = messages[-keep_recent:]

    summary_parts = []
    for msg in old_messages:
        role = msg.role
        content = msg.content or ''
        if isinstance(content, str) and content:
            preview = content[:300] + "..." if len(content) > 300 else content
            summary_parts.append(f"[{role}] {preview}")
        elif isinstance(content, list) and content:
            preview = str(content)[:300] + "..."
            summary_parts.append(f"[{role}] {preview}")

    if summary_parts:
        summary = "Previous conversation summary:\n" + "\n".join(summary_parts)
        summary_msg = Message(
            role="user",
            content=f"[Context compressed]\n\n{summary}"
        )
        confirm_msg = Message(
            role="assistant",
            content="Understood. I have the conversation context. Continuing."
        )
        messages.clear()
        messages.append(summary_msg)
        messages.append(confirm_msg)
        messages.extend(recent_messages)
        console.print(f"[green]Context compacted: {msg_count} messages -> {len(messages)} messages.[/green]")
    else:
        messages.clear()
        console.print(f"[green]Context cleared ({msg_count} messages).[/green]")


def _cmd_debug(agent_config=None, current_agent=None, shell_mode=False,
               workspace=None, skills_registry=None, extra_tool_names=None, **kwargs):
    console.print("[bold cyan]Debug Info[/bold cyan]")
    console.print(f"  Model: {agent_config['model_provider']}/{agent_config['model_name']}")
    console.print(f"  Shell Mode: {'[green]ON[/green]' if shell_mode else '[dim]OFF[/dim]'}")
    console.print(f"  Work Dir: {agent_config.get('work_dir') or os.getcwd()}")
    
    if hasattr(current_agent, 'working_memory') and current_agent.working_memory:
        msg_count = len(current_agent.working_memory.messages) if hasattr(current_agent.working_memory, 'messages') else 0
        console.print(f"  History Messages: {msg_count}")
    
    if hasattr(current_agent, 'tools') and current_agent.tools:
        tool_names = [t.name if hasattr(t, 'name') else str(t) for t in current_agent.tools]
        console.print(f"  Extra Tools: {len(tool_names)}")
    
    if workspace:
        console.print(f"  Workspace: {workspace.path}")
        console.print(f"  Workspace Exists: {workspace.exists()}")
    
    if skills_registry:
        console.print(f"  Skills Loaded: {len(skills_registry)}")


def _cmd_reload_skills(skills_registry=None, **kwargs):
    """Reload skills from disk (clears memoization caches)."""
    try:
        result = _refresh_skills_session(
            agent_config=kwargs.get("agent_config"),
            extra_tools=kwargs.get("extra_tools"),
            workspace=kwargs.get("workspace"),
        )
        console.print(
            f"Reloaded {len(result['skills_registry'])} skills from disk.",
            style="green",
        )
        return result
    except Exception as e:
        console.print(f"Failed to reload skills: {e}", style="red")


def _refresh_skills_session(agent_config=None, extra_tools=None, workspace=None):
    """Reload skill registry from disk and rebuild the current agent."""
    reset_skill_registry()
    load_skills()
    new_registry = get_skill_registry()
    new_agent = create_agent(agent_config, extra_tools, workspace, new_registry)
    return {
        "skills_registry": new_registry,
        "current_agent": new_agent,
    }


def _cmd_extensions(cmd_args="", agent_config=None, extra_tools=None, workspace=None, **kwargs):
    """Manage external skill extensions inside the interactive CLI."""
    parts = shlex.split(cmd_args)
    if not parts:
        console.print(
            "Usage: /extensions <install|list|remove|reload> ...",
            style="yellow",
        )
        return

    subcommand = parts[0].lower()
    args = parts[1:]

    try:
        if subcommand == "install":
            if not args:
                console.print(
                    "Usage: /extensions install <git-url-or-local-path> [--target-dir DIR] [--force]",
                    style="yellow",
                )
                return

            source = None
            target_dir = None
            force = False
            index = 0
            while index < len(args):
                arg = args[index]
                if arg == "--force":
                    force = True
                    index += 1
                elif arg == "--target-dir":
                    if index + 1 >= len(args):
                        console.print("--target-dir requires a path", style="red")
                        return
                    target_dir = args[index + 1]
                    index += 2
                elif source is None:
                    source = arg
                    index += 1
                else:
                    console.print(f"Unexpected argument: {arg}", style="red")
                    return

            if source is None:
                console.print("Missing install source.", style="red")
                return

            replaced_symlinked_skills: list[str] = []
            installed = install_skills(
                source,
                destination_dir=target_dir,
                force=force,
                replaced_symlinked_skills=replaced_symlinked_skills,
            )
            console.print(
                f"Installed {len(installed)} skill(s) from {source}.",
                style="green",
            )
            for skill_name in replaced_symlinked_skills:
                console.print(
                    f"replaced existing symlinked skill: {skill_name}",
                    style="green",
                )
            if target_dir:
                console.print(
                    "Note: custom --target-dir is only auto-discovered when it is a standard skills path or included in AGENTICA_EXTRA_SKILL_PATH.",
                    style="yellow",
                )
            return _refresh_skills_session(
                agent_config=agent_config,
                extra_tools=extra_tools,
                workspace=workspace,
            )

        if subcommand == "list":
            skills = list_installed_skills()
            if not skills:
                console.print("No installed external skills found.", style="yellow")
                return
            console.print("Installed skills:", style="cyan")
            for skill in skills:
                console.print(f"  - [bold]{skill.name}[/bold]: {skill.description}")
            return

        if subcommand in {"remove", "uninstall"}:
            if not args:
                console.print("Usage: /extensions remove <skill-name>", style="yellow")
                return
            removed_path = remove_skill(args[0])
            console.print(f"Removed skill '{args[0]}' from {removed_path}", style="green")
            return _refresh_skills_session(
                agent_config=agent_config,
                extra_tools=extra_tools,
                workspace=workspace,
            )

        if subcommand == "reload":
            return _cmd_reload_skills(
                agent_config=agent_config,
                extra_tools=extra_tools,
                workspace=workspace,
            )

        console.print(
            f"Unknown /extensions subcommand: {subcommand}",
            style="red",
        )
    except Exception as e:
        console.print(f"Extensions command failed: {e}", style="red")


def _cmd_cost(current_agent=None, **kwargs):
    """Display cumulative token usage and cost for the current session."""
    tracker = current_agent.run_response.cost_tracker if current_agent else None

    if tracker is None or tracker.turns == 0:
        console.print("[yellow]No cost data available yet. Cost is tracked after the first LLM call.[/yellow]")
        return

    console.print(f"[bold cyan]Session Cost Summary[/bold cyan]")
    console.print(tracker.summary())


def _cmd_export(cmd_args="", current_agent=None, **kwargs):
    """Export conversation history to a Markdown file."""
    if not (hasattr(current_agent, 'working_memory') and current_agent.working_memory):
        console.print("[yellow]No conversation to export.[/yellow]")
        return

    messages = current_agent.working_memory.messages if hasattr(current_agent.working_memory, 'messages') else []
    if not messages:
        console.print("[yellow]No messages to export.[/yellow]")
        return

    filename = cmd_args.strip() if cmd_args.strip() else "conversation_export.md"
    if not filename.endswith(".md"):
        filename += ".md"

    lines = ["# Conversation Export\n"]
    for msg in messages:
        role = msg.role or "unknown"
        content = msg.content or ""
        if isinstance(content, list):
            content = str(content)
        lines.append(f"## {role.capitalize()}\n")
        lines.append(f"{content}\n")

    Path(filename).write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]Exported {len(messages)} messages to {filename}[/green]")


def _cmd_permissions(cmd_args="", permission_manager=None, **kwargs):
    """View or change permission mode."""
    if cmd_args.strip():
        new_mode = cmd_args.strip().lower()
        if new_mode not in ("allow-all", "auto", "strict"):
            console.print(f"[red]Invalid mode: {new_mode}. Use: allow-all, auto, strict[/red]")
            return
        if permission_manager:
            permission_manager.mode = new_mode
            permission_manager.session_allowed.clear()
            console.print(f"[green]Permission mode set to: {new_mode}[/green]")
        return

    if permission_manager:
        console.print(f"[bold cyan]Permission Mode: {permission_manager.mode}[/bold cyan]")
        if permission_manager.session_allowed:
            console.print(f"  Session-allowed tools: {', '.join(sorted(permission_manager.session_allowed))}")
        console.print()
        console.print("  [dim]allow-all[/dim]  - auto-approve everything")
        console.print("  [dim]auto[/dim]      - prompt for write/execute, auto-approve reads")
        console.print("  [dim]strict[/dim]    - prompt for every tool call")
        console.print()
        console.print("Usage: /permissions <mode>", style="dim")


def _cmd_yolo(permission_manager=None, **kwargs):
    """Toggle YOLO mode (allow-all ↔ auto)."""
    if not permission_manager:
        return
    if permission_manager.mode == "allow-all":
        permission_manager.mode = "auto"
        permission_manager.session_allowed.clear()
        console.print("[cyan]YOLO OFF[/cyan] — back to auto-approve mode")
    else:
        permission_manager.mode = "allow-all"
        console.print("[bold yellow]⚡ YOLO ON[/bold yellow] — all tool calls auto-approved")


def _cmd_paste(attached_images=None, image_counter=None, **kwargs):
    """Check clipboard for an image and attach it."""
    if attached_images is None or image_counter is None:
        console.print("[dim]Image paste not available.[/dim]")
        return
    from agentica.cli.clipboard import has_clipboard_image
    if has_clipboard_image():
        if _try_attach_clipboard_image(attached_images, image_counter):
            img = attached_images[-1]
            size_kb = img.stat().st_size // 1024 if img.exists() else 0
            console.print(f"  [green]📎 Image #{len(attached_images)} attached: {img.name} ({size_kb}KB)[/green]")
        else:
            console.print("  [dim]Clipboard has an image but extraction failed.[/dim]")
    else:
        console.print("  [dim]No image found in clipboard.[/dim]")


def _cmd_image(cmd_args="", attached_images=None, image_counter=None, **kwargs):
    """Attach a local image file for the next prompt."""
    if attached_images is None or image_counter is None:
        console.print("[dim]Image attachment not available.[/dim]")
        return
    raw_args = cmd_args.strip()
    if not raw_args:
        console.print("  [dim]Usage: /image <path>  e.g. /image /path/to/image.png[/dim]")
        return

    path_token, _ = _split_path_input(raw_args)
    image_path = _resolve_attachment_path(path_token)
    if image_path is None:
        console.print(f"  [dim]File not found: {path_token}[/dim]")
        return
    if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        console.print(f"  [dim]Not a supported image file: {image_path.name}[/dim]")
        return

    attached_images.append(image_path)
    image_counter[0] += 1
    console.print(f"  [green]📎 Attached image: {image_path.name}[/green]")


def _cmd_queue(cmd_args="", pending_queue=None, agent_running=None, **kwargs):
    """Queue a message for the next turn."""
    payload = cmd_args.strip()
    if not payload:
        console.print("  [dim]Usage: /queue <prompt>[/dim]")
        return
    if pending_queue is None:
        console.print("  [dim]Queue not available.[/dim]")
        return
    pending_queue.put(payload)
    preview = payload[:80] + ("..." if len(payload) > 80 else "")
    if agent_running and agent_running[0]:
        console.print(f"  [dim]Queued for the next turn: {preview}[/dim]")
    else:
        console.print(f"  [dim]Queued: {preview}[/dim]")


def _cmd_reasoning(cmd_args="", tui_state=None, **kwargs):
    """Toggle reasoning/thinking display."""
    if tui_state is None:
        return
    arg = cmd_args.strip().lower()
    if not arg:
        state = "ON" if tui_state.get("show_reasoning", True) else "OFF"
        console.print(f"  Reasoning display: [bold]{state}[/bold]")
        console.print("  [dim]Usage: /reasoning on|off[/dim]")
        return
    if arg in ("show", "on", "true", "1"):
        tui_state["show_reasoning"] = True
        console.print("  [green]Reasoning display: ON[/green]")
    elif arg in ("hide", "off", "false", "0"):
        tui_state["show_reasoning"] = False
        console.print("  [green]Reasoning display: OFF[/green]")
    else:
        console.print(f"  [dim]Unknown argument: {arg}. Use: on, off[/dim]")


# Command dispatch table: {command: (handler, description)}
# Single source of truth for both dispatch and typeahead completion.
COMMAND_REGISTRY = {
    "/exit":          (_cmd_exit,          "Exit the CLI"),
    "/quit":          (_cmd_exit,          "Exit the CLI"),
    "/help":          (_cmd_help,          "Show help information"),
    "/tools":         (_cmd_tools,         "List available tools"),
    "/skills":        (_cmd_skills,        "List loaded skills"),
    "/memory":        (_cmd_memory,        "Show conversation history"),
    "/workspace":     (_cmd_workspace,     "Show workspace status"),
    "/newchat":       (_cmd_newchat,       "Start a new chat session"),
    "/resume":        (_cmd_resume,        "Resume a previous session"),
    "/clear":         (_cmd_clear,         "Clear screen and reset"),
    "/reset":         (_cmd_clear,         "Clear screen and reset"),
    "/model":         (_cmd_model,         "View or switch model"),
    "/compact":       (_cmd_compact,       "Compact context with summary. Usage: /compact [custom instructions]"),
    "/debug":         (_cmd_debug,         "Show debug info"),
    "/cost":          (_cmd_cost,          "Show token usage and cost"),
    "/export":        (_cmd_export,        "Export conversation to Markdown. Usage: /export [filename]"),
    "/permissions":   (_cmd_permissions,   "View or set permission mode (allow-all/auto/strict)"),
    "/reload-skills": (_cmd_reload_skills, "Reload skills from disk"),
    "/extensions":    (_cmd_extensions,    "Manage external skills: install/list/remove/reload"),
    "/yolo":          (_cmd_yolo,          "Toggle YOLO mode (auto-approve all tool calls)"),
    "/paste":         (_cmd_paste,         "Paste image from clipboard"),
    "/image":         (_cmd_image,         "Attach a local image file. Usage: /image <path>"),
    "/queue":         (_cmd_queue,         "Queue a message for next turn. Usage: /queue <prompt>"),
    "/reasoning":     (_cmd_reasoning,     "Toggle reasoning display. Usage: /reasoning on|off"),
}

# For backward compat / quick dispatch lookup
COMMAND_HANDLERS = {cmd: handler for cmd, (handler, _) in COMMAND_REGISTRY.items()}


def _handle_shell_command(user_input: str, work_dir: Optional[str] = None) -> None:
    """Execute a shell command directly."""
    console.print(f"[dim]$ {user_input}[/dim]")
    try:
        result = subprocess.run(
            user_input,
            shell=True,
            capture_output=True,
            text=True,
            cwd=work_dir or os.getcwd()
        )
        if result.stdout:
            console.print(result.stdout, end="")
        if result.stderr:
            console.print(result.stderr, style="red", end="")
        if result.returncode != 0:
            console.print(f"[dim]Exit code: {result.returncode}[/dim]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    console.print()


def _process_stream_response(
    current_agent, final_input: str, session_tokens: list,
    tui_state: dict, *, images: Optional[list] = None,
) -> None:
    """Process the agent's streaming response and display it.

    Args:
        session_tokens: single-element list ``[int]`` accumulating total tokens
                        across the interactive session (mutated in-place).
        tui_state:      mutable dict shared with the TUI — used to drive the
                        persistent spinner widget and context-token counter.
        images:         optional list of image paths to attach to this turn.
    """
    def _set_spinner(text: str = ""):
        tui_state["spinner_text"] = text

    _set_spinner("⏳ Thinking…")
    request_start = perf_counter()

    try:
        from agentica.run_config import RunConfig
        run_kwargs = {"config": RunConfig(stream_intermediate_steps=True)}
        if images:
            run_kwargs["images"] = [str(p) for p in images]
        response_stream = current_agent.run_stream_sync(final_input, **run_kwargs)

        display = StreamDisplayManager(console)
        shown_tool_count = 0

        for chunk in response_stream:
            if chunk is None:
                continue

            if chunk.event in ("RunStarted", "RunCompleted", "UpdatingMemory"):
                continue

            if chunk.event == "ToolCallStarted":
                if chunk.tools and len(chunk.tools) > shown_tool_count:
                    for tool_info in chunk.tools[shown_tool_count:]:
                        tool_name = tool_info.get("tool_name") or tool_info.get("name", "unknown")
                        tool_args = tool_info.get("tool_args") or tool_info.get("arguments", {})
                        if isinstance(tool_args, str):
                            try:
                                tool_args = json.loads(tool_args)
                            except Exception:
                                tool_args = {"args": tool_args}
                        display.display_tool(tool_name, tool_args)
                        _set_spinner(f"🔧 {tool_name}")
                    shown_tool_count = len(chunk.tools)
                continue

            elif chunk.event == "ToolCallCompleted":
                _set_spinner("⏳ Thinking…")
                if chunk.tools:
                    for tool_info in reversed(chunk.tools):
                        if "content" in tool_info:
                            tool_name = tool_info.get("tool_name") or tool_info.get("name", "unknown")
                            result_content = tool_info.get("content", "")
                            is_error = tool_info.get("tool_call_error", False)
                            elapsed = (tool_info.get("metrics") or {}).get("time")
                            display.display_tool_result(
                                tool_name, str(result_content) if result_content else "",
                                is_error=is_error, elapsed=elapsed,
                            )
                            break
                continue

            has_content = chunk.content and isinstance(chunk.content, str)
            has_reasoning = hasattr(chunk, 'reasoning_content') and chunk.reasoning_content

            if not has_content and not has_reasoning:
                continue

            # Show reasoning/thinking if enabled (default: True, toggle via /reasoning)
            if has_reasoning and not has_content:
                if tui_state.get("show_reasoning", True):
                    _set_spinner("")
                    display.start_thinking()
                    display.stream_thinking(chunk.reasoning_content)
                continue

            if has_content:
                _set_spinner("")
                display.stream_response(chunk.content)

        display.finalize()
        _set_spinner("")

        # Update status bar
        elapsed = perf_counter() - request_start
        tui_state["last_turn_seconds"] = elapsed
        tui_state["active_seconds"] = tui_state.get("active_seconds", 0.0) + elapsed

        cost_tracker = current_agent.run_response.cost_tracker
        if cost_tracker and cost_tracker.turns > 0:
            # Context usage = last API call's input tokens (= current context size)
            # NOT cumulative across turns — each turn's input already includes history
            context_tokens = cost_tracker.last_input_tokens
            context_window = (
                current_agent.model.context_window if current_agent.model else 128000
            )
            tui_state["context_tokens"] = context_tokens
            tui_state["context_window"] = context_window
            tui_state["cost_usd"] = cost_tracker.total_cost_usd

        if not display.has_content_output and display.tool_count == 0 and not display.thinking_shown:
            _set_spinner("")
            console.print("[info]Agent returned no content.[/info]")

    except KeyboardInterrupt:
        current_agent.cancel()
        _set_spinner("")
        # Wait for the background runner thread to finish (up to 3s)
        # so agent state is properly cleaned up before next turn
        _deadline = time.monotonic() + 3.0
        while current_agent._running and time.monotonic() < _deadline:
            time.sleep(0.05)
        # Force-clear running flag if thread didn't finish in time
        current_agent._running = False
        current_agent._cancelled = False
        console.print("\n[yellow]Agent cancelled.[/yellow]")
    except AgentCancelledError:
        _set_spinner("")
        current_agent._running = False
        current_agent._cancelled = False
        console.print("\n[yellow]Agent cancelled.[/yellow]")
    except Exception as e:
        _set_spinner("")
        console.print(f"\n[bold red]Error during agent execution: {str(e)}[/bold red]")


def _setup_prompt_toolkit(shell_mode_ref: list, skills_registry, tui_state: dict,
                          attached_images: list, image_counter: list,
                          pasted_files: list, paste_counter: list):
    """Set up prompt_toolkit PromptSession with bottom_toolbar status bar.

    Returns:
        ``(get_input, tui_state)``
    """
    # ── Completer ──
    class AgenticaCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if text.startswith("/"):
                parts = text.split(None, 1)
                if len(parts) >= 2:
                    cmd = parts[0].lower()
                    if skills_registry:
                        skill = skills_registry.get_skill_by_trigger(cmd)
                        if skill and skill.argument_hint:
                            yield Completion(skill.argument_hint, start_position=0,
                                             display=skill.argument_hint, display_meta="argument")
                    return
                q = text.lower()
                for cmd_name, (_, desc) in COMMAND_REGISTRY.items():
                    if cmd_name.startswith(q):
                        yield Completion(cmd_name, start_position=-len(text),
                                         display=cmd_name, display_meta=desc)
                if skills_registry:
                    for trigger, skill_name in skills_registry.list_triggers().items():
                        if trigger.startswith(q):
                            skill = skills_registry.get_skill_by_trigger(trigger)
                            meta = skill.description[:40] if skill else skill_name
                            yield Completion(trigger, start_position=-len(text),
                                             display=trigger, display_meta=meta)
                return
            m = re.search(r"@([\w./-]*)$", text)
            if m:
                partial = m.group(1)
                for comp in get_file_completions(text):
                    yield Completion(comp, start_position=-len(partial), display=comp)

    # ── Key bindings ──
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    @kb.add("c-j")
    def _newline2(event):
        event.current_buffer.insert_text("\n")

    @kb.add("c-d")
    def _exit(event):
        event.app.exit(result=None)

    @kb.add("c-x")
    def _toggle_shell(event):
        event.current_buffer.text = "__TOGGLE_SHELL_MODE__"
        event.current_buffer.validate_and_handle()

    _paste_just_collapsed = [False]

    @kb.add(Keys.BracketedPaste, eager=True)
    def _handle_paste(event):
        pasted = (event.data or "").replace('\r\n', '\n').replace('\r', '\n')
        if _try_attach_clipboard_image(attached_images, image_counter):
            n = len(attached_images)
            img = attached_images[-1]
            size_kb = img.stat().st_size // 1024 if img.exists() else 0
            console.print(f"  [green]📎 Image #{n} attached: {img.name} ({size_kb}KB)[/green]")
        if pasted:
            line_count = pasted.count('\n')
            buf = event.current_buffer
            if line_count >= 5 and not buf.text.strip().startswith("/"):
                from agentica.config import AGENTICA_HOME
                paste_dir = Path(AGENTICA_HOME) / "pastes"
                paste_dir.mkdir(parents=True, exist_ok=True)
                paste_counter[0] += 1
                ts = datetime.now().strftime("%H%M%S")
                paste_file = paste_dir / f"paste_{paste_counter[0]}_{ts}.txt"
                paste_file.write_text(pasted, encoding="utf-8")
                pasted_files.append((paste_file, line_count + 1))
                placeholder = f"[Pasted text #{paste_counter[0]}: {line_count + 1} lines -> {paste_file}]"
                prefix = ""
                if buf.cursor_position > 0 and buf.text[buf.cursor_position - 1] != '\n':
                    prefix = "\n"
                _paste_just_collapsed[0] = True
                buf.insert_text(prefix + placeholder)
            else:
                buf.insert_text(pasted)

    @kb.add("c-v")
    def _handle_ctrl_v(event):
        if _try_attach_clipboard_image(attached_images, image_counter):
            img = attached_images[-1]
            size_kb = img.stat().st_size // 1024 if img.exists() else 0
            console.print(f"  [green]📎 Image #{len(attached_images)} attached: {img.name} ({size_kb}KB)[/green]")

    @kb.add("escape", "v")
    def _handle_alt_v(event):
        if _try_attach_clipboard_image(attached_images, image_counter):
            img = attached_images[-1]
            size_kb = img.stat().st_size // 1024 if img.exists() else 0
            console.print(f"  [green]📎 Image #{len(attached_images)} attached: {img.name} ({size_kb}KB)[/green]")

    # ── Bottom toolbar (status bar below the prompt) ──
    def _bottom_toolbar():
        tw = shutil.get_terminal_size().columns
        return build_status_bar_fragments(
            model_name=tui_state.get("model_name", ""),
            context_tokens=tui_state.get("context_tokens", 0),
            context_window=tui_state.get("context_window", 128000),
            cost_usd=tui_state.get("cost_usd", 0.0),
            active_seconds=tui_state.get("active_seconds", 0.0),
            last_turn_seconds=tui_state.get("last_turn_seconds", 0.0),
            spinner_text=tui_state.get("spinner_text", ""),
            terminal_width=tw,
        )

    style = PTStyle.from_dict({
        "prompt": "ansicyan bold",
        "shell-prompt": "ansigreen bold",
        "bottom-toolbar": "bg:ansiblack ansiwhite",
        "sb": "bg:ansiblack ansiwhite",
        "sb-strong": "bg:ansiblack ansiwhite bold",
        "sb-dim": "bg:ansiblack ansigray",
        "sb-good": "bg:ansiblack ansigreen",
        "sb-warn": "bg:ansiblack ansiyellow",
        "sb-bad": "bg:ansiblack ansired",
        "sb-critical": "bg:ansiblack ansired bold",
        "sb-spin": "bg:ansiblack ansiyellow italic",
    })

    history_dir = os.path.dirname(history_file)
    if history_dir:
        os.makedirs(history_dir, exist_ok=True)

    session = PromptSession(
        history=FileHistory(history_file),
        auto_suggest=AutoSuggestFromHistory(),
        completer=AgenticaCompleter(),
        key_bindings=kb,
        style=style,
        multiline=False,
        bottom_toolbar=_bottom_toolbar,
    )

    def get_input():
        tw = min(console.width or 80, 120)
        console.print(f"[bright_yellow]{'─' * tw}[/bright_yellow]")
        try:
            if shell_mode_ref[0]:
                return session.prompt([("class:shell-prompt", "$ ")], multiline=False)
            else:
                return session.prompt([("class:prompt", "❯ ")], multiline=False)
        except KeyboardInterrupt:
            return "__CTRL_C__"
        except EOFError:
            return None

    return get_input, tui_state


def run_interactive(agent_config: dict, extra_tool_names: Optional[List[str]] = None,
                    workspace: Optional[Workspace] = None, skills_registry=None):
    """Run the interactive CLI with prompt_toolkit PromptSession.

    Rich console output renders directly to stdout (no patch_stdout proxy),
    ensuring correct Markdown / ANSI formatting.  The status bar lives in
    ``bottom_toolbar`` — always below the input prompt.
    """
    if not agent_config.get("debug"):
        suppress_console_logging()

    shell_mode_ref = [False]

    perm_mode = agent_config.get("permissions", "auto")
    permission_manager = PermissionManager(mode=perm_mode)

    extra_tools = configure_tools(extra_tool_names) if extra_tool_names else None
    current_agent = create_agent(agent_config, extra_tools, workspace, skills_registry)

    print_header(
        agent_config["model_provider"],
        agent_config["model_name"],
        work_dir=agent_config.get("work_dir"),
        extra_tools=extra_tool_names,
        shell_mode=shell_mode_ref[0]
    )

    if workspace and workspace.exists():
        console.print(f"  Workspace: [green]{workspace.path}[/green]")
    if skills_registry and len(skills_registry) > 0:
        triggers = skills_registry.list_triggers()
        if triggers:
            trigger_str = ", ".join(triggers.keys())
            console.print(f"  Skills: [cyan]{len(skills_registry)} loaded[/cyan] (triggers: {trigger_str})")
    if perm_mode != "auto":
        console.print(f"  Permissions: [yellow]{perm_mode}[/yellow]")
    console.print()

    tui_state = {
        "model_name": agent_config.get("model_name", ""),
        "context_tokens": 0,
        "context_window": current_agent.model.context_window if current_agent.model else 128000,
        "cost_usd": 0.0,
        "active_seconds": 0.0,
        "last_turn_seconds": 0.0,
        "spinner_text": "",
        "show_reasoning": True,
    }

    # Queue for messages typed while agent is busy (queue mode)
    pending_queue: queue.Queue = queue.Queue()
    agent_running_ref = [False]

    attached_images: List[Path] = []
    image_counter = [0]
    pasted_files: list = []
    paste_counter = [0]

    get_input, tui_state = _setup_prompt_toolkit(
        shell_mode_ref, skills_registry, tui_state,
        attached_images, image_counter,
        pasted_files, paste_counter,
    )

    session_tokens = [0]
    ctrl_c_count = 0

    while True:
        try:
            user_input = get_input()

            if user_input is None:
                console.print("\nExiting...", style="yellow")
                break

            if user_input == "__CTRL_C__":
                ctrl_c_count += 1
                if ctrl_c_count >= 2:
                    console.print("\nExiting...", style="yellow")
                    break
                console.print("\n[dim]Press Ctrl+C again to exit, or Ctrl+D to quit immediately.[/dim]")
                continue

            ctrl_c_count = 0
            user_input = user_input.strip()
            if not user_input and not attached_images:
                continue

            if user_input == "__TOGGLE_SHELL_MODE__":
                shell_mode_ref[0] = not shell_mode_ref[0]
                mode_str = (
                    "[green]Shell Mode ON[/green] - Commands execute directly"
                    if shell_mode_ref[0]
                    else "[cyan]Agent Mode ON[/cyan] - AI processes your input"
                )
                console.print(f"\n{mode_str}")
                continue

            # Detect dragged/pasted file paths (before slash command detection)
            dropped = _detect_file_drop(user_input)
            if dropped:
                if dropped["is_image"]:
                    attached_images.append(dropped["path"])
                    image_counter[0] += 1
                    console.print(f"  [green]📎 Attached image: {dropped['path'].name}[/green]")
                    user_input = dropped["remainder"] or f"[User attached image: {dropped['path'].name}]"
                else:
                    user_input = f"@{dropped['path']} {dropped['remainder']}".strip()

            if shell_mode_ref[0]:
                if user_input.startswith("/") and user_input.split()[0].lower() in {
                    "/exit", "/quit", "/help", "/model", "/debug", "/clear", "/reset"
                }:
                    pass
                else:
                    _handle_shell_command(user_input, agent_config.get("work_dir"))
                    continue

            first_word = user_input.split()[0].lower() if user_input else ""
            is_command = first_word in COMMAND_HANDLERS or (
                skills_registry and first_word.startswith("/")
                and skills_registry.match_trigger(user_input) is not None
            )
            if is_command:
                cmd_parts = user_input.split(maxsplit=1)
                cmd = cmd_parts[0].lower()
                cmd_args = cmd_parts[1] if len(cmd_parts) > 1 else ""

                handler = COMMAND_HANDLERS.get(cmd)
                if handler:
                    result = handler(
                        cmd_args=cmd_args,
                        agent_config=agent_config,
                        current_agent=current_agent,
                        extra_tools=extra_tools,
                        extra_tool_names=extra_tool_names,
                        workspace=workspace,
                        skills_registry=skills_registry,
                        shell_mode=shell_mode_ref[0],
                        permission_manager=permission_manager,
                        attached_images=attached_images,
                        image_counter=image_counter,
                        pending_queue=pending_queue,
                        agent_running=agent_running_ref,
                        tui_state=tui_state,
                    )
                    if result == "EXIT":
                        break
                    if isinstance(result, dict):
                        if "current_agent" in result:
                            current_agent = result["current_agent"]
                            # Update status bar when agent is replaced
                            tui_state["model_name"] = agent_config.get("model_name", "")
                            tui_state["context_window"] = (
                                current_agent.model.context_window if current_agent.model else 128000
                            )
                            # Reset context tokens and cost for new session
                            session_tokens[0] = 0
                            tui_state["context_tokens"] = 0
                            tui_state["cost_usd"] = 0.0
                        if result.get("model_switched"):
                            # Hot-swap: model changed on existing agent, update status bar only
                            tui_state["model_name"] = agent_config.get("model_name", "")
                            tui_state["context_window"] = (
                                current_agent.model.context_window if current_agent.model else 128000
                            )
                        if "skills_registry" in result:
                            skills_registry = result["skills_registry"]
                    continue
                else:
                    if skills_registry:
                        matched_skill = skills_registry.match_trigger(user_input)
                        if matched_skill:
                            skill_prompt = matched_skill.get_prompt()
                            current_agent.add_instruction(f"\n# {matched_skill.name} Skill\n{skill_prompt}")
                            if matched_skill.trigger and user_input.lower().startswith(matched_skill.trigger):
                                user_input = user_input[len(matched_skill.trigger):].strip()
                            console.print(f"[dim]Skill activated: {matched_skill.name}[/dim]")

            # Default prompt when images are attached but no text
            if not user_input and attached_images:
                user_input = "What do you see in this image?"

            # Expand paste references back to full content before processing
            _paste_ref_re = re.compile(r'\[Pasted text #\d+: \d+ lines -> (.+?)\]')
            paste_refs = list(_paste_ref_re.finditer(user_input))
            n_pasted_blocks = len(paste_refs) + len(pasted_files)
            n_pasted_lines = sum(n for _, n in pasted_files) if pasted_files else 0
            if paste_refs:
                def _expand_ref(m):
                    p = Path(m.group(1))
                    if p.exists():
                        content = p.read_text(encoding="utf-8")
                        return content
                    return m.group(0)
                expanded = _paste_ref_re.sub(_expand_ref, user_input)
                n_pasted_lines += expanded.count('\n') + 1
                user_input = expanded
            pasted_files.clear()

            prompt_text, mentioned_files = parse_file_mentions(user_input)
            final_input = inject_file_contents(prompt_text, mentioned_files)

            # Show image badges with path + size
            if attached_images:
                for img in attached_images:
                    size_kb = img.stat().st_size // 1024 if img.exists() else 0
                    console.print(f"  [dim]📎 {img.name} ({size_kb}KB) -> {img}[/dim]")

            display_user_message(
                user_input,
                pasted_blocks=n_pasted_blocks,
                pasted_lines=n_pasted_lines,
            )

            # Collect images for this turn, then clear the attachment list
            turn_images = list(attached_images) if attached_images else None
            attached_images.clear()

            # Run agent in background thread so main thread can accept queued input
            agent_running_ref[0] = True
            agent_error = [None]

            def _run_agent():
                _process_stream_response(
                    current_agent, final_input, session_tokens, tui_state,
                    images=turn_images,
                )
                agent_error[0] = None

            agent_thread = threading.Thread(target=_run_agent, daemon=True)
            agent_thread.start()

            # While agent is running, accept input and queue it
            while agent_thread.is_alive():
                try:
                    agent_thread.join(timeout=0.1)
                    if not agent_thread.is_alive():
                        break
                except KeyboardInterrupt:
                    # Ctrl+C during agent run: cancel agent, preserve session
                    current_agent.cancel()
                    console.print("\n[yellow]Agent cancelled.[/yellow]")
                    agent_thread.join(timeout=3.0)
                    current_agent._running = False
                    current_agent._cancelled = False
                    break

            agent_running_ref[0] = False
            console.print()

            # Drain queued messages (from /queue command)
            while not pending_queue.empty():
                queued_input = pending_queue.get_nowait()
                if not queued_input:
                    continue
                console.print(f"[dim]Processing queued: {str(queued_input)[:60]}...[/dim]")
                display_user_message(str(queued_input))
                q_text, q_files = parse_file_mentions(str(queued_input))
                q_final = inject_file_contents(q_text, q_files)
                agent_running_ref[0] = True
                _process_stream_response(
                    current_agent, q_final, session_tokens, tui_state,
                )
                agent_running_ref[0] = False
                console.print()

        except KeyboardInterrupt:
            # Ctrl+C while waiting for input (not during agent run)
            ctrl_c_count += 1
            if ctrl_c_count >= 2:
                console.print("\nExiting...", style="yellow")
                break
            console.print("\n[dim]Press Ctrl+C again to exit, or type /exit.[/dim]")
            continue
        except Exception as e:
            console.print(f"\n[bold red]An unexpected error occurred: {str(e)}[/bold red]")
            continue

    console.print("\nThank you for using Agentica CLI. Goodbye!", style="bold green")
