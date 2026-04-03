# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Config dataclasses for Agent V2 architecture.

Provides layered configuration:
- PromptConfig: Prompt engineering details
- ToolConfig: Tool calling behavior
- WorkspaceMemoryConfig: Workspace memory settings
- TeamConfig: Team collaboration settings
"""

from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Union,
)


@dataclass
class PromptConfig:
    """Prompt construction configuration.

    Most users only need Agent.instructions. These parameters are for advanced customization.
    """
    # Custom system prompt (overrides default build logic)
    system_prompt: Optional[Union[str, Callable]] = None
    system_prompt_template: Optional[Any] = None  # PromptTemplate
    system_message_role: str = "system"
    user_message_role: str = "user"
    user_prompt_template: Optional[Any] = None  # PromptTemplate
    use_default_user_message: bool = True

    # System message building details
    task: Optional[str] = None
    role: Optional[str] = None
    guidelines: Optional[List[str]] = None
    expected_output: Optional[str] = None
    additional_context: Optional[str] = None
    introduction: Optional[str] = None
    references_format: Literal["json", "yaml"] = "json"

    # Prompt behavior switches
    add_name_to_instructions: bool = False
    add_datetime_to_instructions: bool = True
    prevent_hallucinations: bool = False
    prevent_prompt_leakage: bool = False
    limit_tool_access: bool = False
    enable_agentic_prompt: bool = False

    # Output formatting
    output_language: Optional[str] = None
    markdown: bool = False


@dataclass
class ToolConfig:
    """Tool calling configuration."""
    support_tool_calls: bool = True
    tool_call_limit: Optional[int] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    auto_load_mcp: bool = False
    # Knowledge tools
    search_knowledge: bool = True
    update_knowledge: bool = False
    # History tools
    read_chat_history: bool = False
    read_tool_call_history: bool = False
    # References
    add_references: bool = False
    # Compression
    compress_tool_results: bool = False
    compression_manager: Optional[Any] = None

    # ---- Deep / Agentic capabilities (Model-layer hooks) ----

    # Context overflow handling: when token usage exceeds the threshold (0–1 fraction
    # of context_window), truncate old non-system messages before the next LLM call.
    # 0.0 = disabled. Recommended: 0.8 (warn at 80%, hard-truncate at 90%).
    context_overflow_threshold: float = 0.0

    # Repetition detection: if the same tool+args pair appears N times consecutively
    # in function_call_stack, inject a "you're stuck in a loop, change strategy" message.
    # 0 = disabled. Recommended: 3.
    max_repeated_tool_calls: int = 0


@dataclass
class WorkspaceMemoryConfig:
    """Workspace memory loading configuration."""
    load_workspace_context: bool = True
    load_workspace_memory: bool = True
    memory_days: int = 2
    auto_archive: bool = False  # Auto-archive conversation after each run()


@dataclass
class TeamConfig:
    """Team collaboration configuration."""
    respond_directly: bool = False
    add_transfer_instructions: bool = True
    team_response_separator: str = "\n"


@dataclass
class ToolRuntimeConfig:
    """Runtime configuration for a single tool.

    Controls whether a tool is enabled at Agent level.
    Query-level override via run(enabled_tools=[...]).
    """
    name: str
    enabled: bool = True


@dataclass
class SkillRuntimeConfig:
    """Runtime configuration for a single skill.

    Controls whether a skill is enabled at Agent level.
    Query-level override via run(enabled_skills=[...]).
    """
    name: str
    enabled: bool = True


@dataclass
class SandboxConfig:
    """Sandbox execution isolation configuration (best-effort).

    Controls file system and command execution boundaries for security.
    NOTE: This is a best-effort safety net, NOT a true security sandbox.
    Determined attackers can bypass these checks (e.g. via encoding, symlinks,
    or indirect execution). Use OS-level sandboxing (Docker, seccomp, etc.)
    for untrusted code.

    Attributes:
        enabled: Whether sandbox restrictions are active
        writable_dirs: List of directory paths the agent is allowed to write to.
        blocked_paths: Path components that are always blocked for read/write.
            Access to any path containing these path components is denied.
            Uses path component matching (not substring) to avoid false positives.
        blocked_commands: Shell command patterns that are blocked from execution.
            Uses regex boundary matching to reduce false positives.
        allowed_commands: Optional whitelist of allowed command prefixes.
            If set (non-None), ONLY commands whose first token matches one of
            these prefixes are permitted. None means no whitelist restriction
            (all commands allowed, subject to blocked_commands).
            Example: ["python", "pip", "git", "pytest"] restricts the agent to
            only run Python/pip/git/pytest commands.
            NOTE: This is prefix-matched against the first token of the command,
            so "python" allows both "python script.py" and "python3 -c '...'".
        max_execution_time: Maximum seconds for a single command execution
    """
    enabled: bool = False
    writable_dirs: List[str] = field(default_factory=list)
    blocked_paths: List[str] = field(default_factory=lambda: [
        ".ssh", ".gnupg", ".aws", ".azure", ".config/gcloud",
        ".env", ".netrc", "id_rsa", "id_ed25519",
    ])
    blocked_commands: List[str] = field(default_factory=lambda: [
        "rm -rf /", "rm -rf /*", "mkfs", "dd if=",
        ":(){ :|:& };:", "chmod -R 777 /",
        "> /dev/sda", "curl|sh", "curl |sh", "wget|sh", "wget |sh",
    ])
    allowed_commands: Optional[List[str]] = None
    max_execution_time: int = 300
