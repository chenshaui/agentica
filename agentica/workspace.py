# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description:
Workspace management for Agentica agents.
Inspired by OpenClaw's workspace concept.
"""
import asyncio
import functools
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import date, datetime

from agentica.config import AGENTICA_WORKSPACE_DIR, AGENTICA_HOME


async def _async_read_text(path: Path, encoding: str = "utf-8") -> str:
    """Read text file in executor to avoid blocking event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(path.read_text, encoding=encoding))


async def _async_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text file in executor to avoid blocking event loop."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, functools.partial(path.write_text, content, encoding=encoding))


@dataclass
class WorkspaceConfig:
    """Workspace configuration.

    Attributes:
        agent_md: Agent instruction file name
        persona_md: Persona settings file name
        tools_md: Tool documentation file name
        user_md: User information file name
        memory_md: Long-term memory file name
        memory_dir: Daily memory directory name
        skills_dir: Skills directory name
        users_dir: User data directory name (for multi-user isolation)
    """
    agent_md: str = "AGENT.md"
    persona_md: str = "PERSONA.md"
    tools_md: str = "TOOLS.md"
    user_md: str = "USER.md" # user infomation
    users_dir: str = "users" # for multi-user isolation
    memory_dir: str = "memory" # daily memory, under users/{user_id}/memory
    memory_md: str = "MEMORY.md" # user's long-term memory, under users/{user_id}/
    skills_dir: str = "skills" # each user's skills, under users/{user_id}/skills
    conversations_dir: str = "conversations" # conversation archive, under users/{user_id}/conversations


class Workspace:
    """Agent Workspace.

    Workspace is the configuration and memory storage directory for Agent,
    supporting multi-user isolation. All user data is stored under users/ directory.

    Directory structure:
    - AGENT.md: Agent instructions and constraints (globally shared)
    - PERSONA.md: Agent persona settings (globally shared)
    - TOOLS.md: Tool usage documentation (globally shared)
    - skills/: Custom skills directory (globally shared)
    - users/: User data directory (all users including default)
        - default/: Default user (when no user_id specified)
            - USER.md: User information
            - MEMORY.md: Long-term memory
            - memory/: Daily memory directory
        - {user_id}/: Other users
            - USER.md: User information
            - MEMORY.md: Long-term memory
            - memory/: Daily memory directory

    Default user mode:
        >>> workspace = Workspace("~/.agentica/workspace")  # user_id='default'
        >>> workspace.initialize()
        >>> await workspace.write_memory_entry("pref", "User prefers concise responses", "user")

    Custom user mode:
        >>> workspace = Workspace("~/.agentica/workspace", user_id="alice@example.com")
        >>> workspace.initialize()
        >>> await workspace.write_memory_entry("lang", "Alice likes Python", "user")

    Switch user:
        >>> workspace.set_user("bob@example.com")
        >>> await workspace.write_memory_entry("style", "Bob prefers detailed explanations", "user")
    """

    # Global config files (shared across all users)
    DEFAULT_GLOBAL_FILES = {
        "AGENT.md": """# Agent Instructions

You are a helpful AI assistant.

## Guidelines
1. Be concise and accurate
2. Use tools when needed
3. Store important information in memory
4. Follow user preferences in USER.md

## Code Verification

**VERY IMPORTANT**: After completing code changes, you MUST verify your work:

1. **Find Commands**: Check project files for validation commands:
   - README.md, package.json, pyproject.toml, Makefile

2. **Execute Validation**: Use shell tool to run:
   - Lint: `npm run lint`, `ruff check .`, etc.
   - Type check: `npm run typecheck`, `mypy .`, etc.
   - Test: `npm test`, `pytest`, etc.

3. **Fix Issues**: If validation fails, fix and re-run until passing.

## Build/Lint/Test Commands

<!-- Add project-specific commands here -->
<!-- Example:
- Build: `npm run build`
- Lint: `npm run lint`
- Test: `npm test`
- Single test: `npm test -- --grep "test name"`
-->
""",
        "PERSONA.md": """# Persona

## Personality
- Friendly and professional
- Direct and honest
- Proactive in helping

## Communication Style
- Clear and concise
- Use examples when explaining
- Ask clarifying questions when needed
""",
        "TOOLS.md": """# Tool Usage Guidelines

## File Operations
- Always use absolute paths
- Read files before editing
- Create backups for important changes

## Shell Commands
- Prefer safe, non-destructive commands
- Explain what commands will do
""",
    }
    
    # Default user file template
    DEFAULT_USER_MD = """# User Profile

## User ID
{user_id}

## Preferences
- Language: Chinese or English
- Style: Concise

## Context
<!-- User's background, projects, etc. -->
"""

    def __init__(
        self,
        path: Optional[str | Path] = None,
        config: Optional[WorkspaceConfig] = None,
        user_id: Optional[str] = None,
    ):
        """Initialize workspace.

        Args:
            path: Workspace path, defaults to AGENTICA_WORKSPACE_DIR (~/.agentica/workspace)
            config: Workspace configuration, defaults to WorkspaceConfig defaults
            user_id: User ID for multi-user isolation. Defaults to 'default' if not specified
        """
        if path is None:
            path = AGENTICA_WORKSPACE_DIR
        self.path = Path(path).expanduser().resolve()
        self.config = config or WorkspaceConfig()
        # Default to 'default' user if not specified
        self._user_id = user_id if user_id else "default"
        # Per-file locks for concurrent archive writes
        self._archive_locks: Dict[str, asyncio.Lock] = {}
        # Flag to avoid redundant _initialize_user_dir calls
        self._user_initialized: bool = False

    @property
    def user_id(self) -> str:
        """Get current user ID."""
        return self._user_id

    def set_user(self, user_id: Optional[str]):
        """Set current user ID.

        Args:
            user_id: User ID, defaults to 'default' if None
        """
        new_id = user_id if user_id else "default"
        if new_id != self._user_id:
            self._user_initialized = False
        self._user_id = new_id

    def _get_user_path(self) -> Path:
        """Get current user's data directory path.

        Returns:
            Path to users/{user_id}/ directory
        """
        # Sanitize user_id, replace unsafe characters
        safe_user_id = self._user_id.replace("/", "_").replace("\\", "_").replace("..", "_")
        return self.path / self.config.users_dir / safe_user_id

    def _get_user_memory_dir(self) -> Path:
        """Get current user's daily memory directory."""
        return self._get_user_path() / self.config.memory_dir

    def _get_user_memory_md(self) -> Path:
        """Get current user's long-term memory file path."""
        return self._get_user_path() / self.config.memory_md

    def _get_user_md(self) -> Path:
        """Get current user's USER.md file path."""
        return self._get_user_path() / self.config.user_md

    def initialize(self, force: bool = False) -> bool:
        """Initialize workspace.

        Creates workspace directory, global configuration files, and user data directory.

        Args:
            force: Whether to overwrite existing files

        Returns:
            Whether initialization was successful
        """
        self.path.mkdir(parents=True, exist_ok=True)

        # Create globally shared files (AGENT.md, PERSONA.md, TOOLS.md)
        for filename, content in self.DEFAULT_GLOBAL_FILES.items():
            filepath = self.path / filename
            if not filepath.exists() or force:
                filepath.write_text(content, encoding="utf-8")

        # Create global directories
        (self.path / self.config.skills_dir).mkdir(exist_ok=True)
        (self.path / self.config.users_dir).mkdir(exist_ok=True)

        # Always create user directory (default or specified)
        self._initialize_user_dir()

        return True

    def _initialize_user_dir(self):
        """Initialize current user's data directory.

        Uses a cached flag to avoid redundant I/O on repeated calls.
        """
        if self._user_initialized:
            return

        user_path = self._get_user_path()
        user_path.mkdir(parents=True, exist_ok=True)

        # Create user's USER.md
        user_md = user_path / self.config.user_md
        if not user_md.exists():
            user_md.write_text(
                self.DEFAULT_USER_MD.format(user_id=self._user_id),
                encoding="utf-8"
            )

        # Create user's memory directory
        (user_path / self.config.memory_dir).mkdir(exist_ok=True)

        # Create user's conversations directory
        (user_path / self.config.conversations_dir).mkdir(exist_ok=True)

        self._user_initialized = True

    def exists(self) -> bool:
        """Check if workspace exists.

        Returns:
            Whether both workspace directory and AGENT.md file exist
        """
        return self.path.exists() and (self.path / self.config.agent_md).exists()

    async def read_file_async(self, filename: str) -> Optional[str]:
        """Read workspace file asynchronously.

        Args:
            filename: File name (relative to workspace path)

        Returns:
            File content, or None if file doesn't exist or is empty
        """
        filepath = self.path / filename
        if filepath.exists() and filepath.is_file():
            content = (await _async_read_text(filepath)).strip()
            return content if content else None
        return None

    def read_file(self, filename: str) -> Optional[str]:
        """Read workspace file (sync, for init-time use).

        Args:
            filename: File name (relative to workspace path)

        Returns:
            File content, or None if file doesn't exist or is empty
        """
        filepath = self.path / filename
        if filepath.exists() and filepath.is_file():
            content = filepath.read_text(encoding="utf-8").strip()
            return content if content else None
        return None

    def write_file(self, filename: str, content: str):
        """Write workspace file.

        Args:
            filename: File name (relative to workspace path)
            content: Content to write
        """
        filepath = self.path / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")

    def append_file(self, filename: str, content: str):
        """Append content to workspace file.

        Args:
            filename: File name (relative to workspace path)
            content: Content to append
        """
        filepath = self.path / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if filepath.exists():
            existing = filepath.read_text(encoding="utf-8").strip()

        new_content = f"{existing}\n\n{content}".strip() if existing else content
        filepath.write_text(new_content, encoding="utf-8")

    async def get_context_prompt(self) -> str:
        """Get workspace context (for injecting into System Prompt).

        Reads AGENT.md, PERSONA.md, TOOLS.md file contents (globally shared),
        and user-specific USER.md file content.

        Also discovers AGENT.md files along the directory chain from CWD up to
        the filesystem root (mirrors CC's multi-level CLAUDE.md merge).
        The merge order is: global ~/.agentica/AGENT.md -> ancestor dirs
        (root first) -> CWD AGENT.md -> workspace AGENT.md.

        Returns:
            Merged context string
        """
        contents = []

        # 1. Multi-level AGENT.md chain (CWD upward)
        chain_contents = self._load_agent_md_chain()
        if chain_contents:
            contents.append(f"<!-- Project AGENT.md chain -->\n{chain_contents}")

        # 2. Workspace-level files (AGENT.md, PERSONA.md, TOOLS.md)
        global_files = [
            self.config.agent_md,
            self.config.persona_md,
            self.config.tools_md,
        ]
        for f in global_files:
            content = await self.read_file_async(f)
            if content:
                contents.append(f"<!-- {f} -->\n{content}")

        # 3. User-specific USER.md
        user_md_path = self._get_user_md()
        if user_md_path.exists():
            content = (await _async_read_text(user_md_path)).strip()
            if content:
                contents.append(f"<!-- USER.md (user: {self._user_id}) -->\n{content}")

        return "\n\n---\n\n".join(contents) if contents else ""

    def _load_agent_md_chain(self) -> str:
        """Load AGENT.md files from CWD upward to root, plus global ~/.agentica/AGENT.md.

        Mirrors CC's multi-level CLAUDE.md merge: knowledge files at higher
        directories provide broad context, while those closer to CWD provide
        project-specific instructions.

        Merge order (earlier = lower priority, later = higher priority):
            1. ~/.agentica/AGENT.md  (user global preferences)
            2. /repo-root/AGENT.md   (project-level, checked into git)
            3. /repo-root/src/AGENT.md  (subdir-specific, if CWD is deeper)

        Returns:
            Merged content string, or empty string if no files found.
        """
        cwd = Path(os.getcwd())
        found: list[str] = []

        # Walk from CWD upward, collecting AGENT.md files
        visited = set()
        for dir_path in [cwd] + list(cwd.parents):
            resolved = dir_path.resolve()
            if resolved in visited:
                break
            visited.add(resolved)

            agent_md = resolved / "AGENT.md"
            if agent_md.is_file():
                try:
                    text = agent_md.read_text(encoding="utf-8").strip()
                    if text:
                        found.append(text)
                except Exception:
                    pass

            # Stop at git root (project boundary) to avoid scanning
            # unrelated parent directories
            if (resolved / ".git").exists():
                break

        # Reverse so root-level comes first (lower priority)
        found.reverse()

        # Prepend user global AGENT.md (~/.agentica/AGENT.md)
        global_agent_md = Path(AGENTICA_HOME) / "AGENT.md"
        if global_agent_md.is_file():
            try:
                text = global_agent_md.read_text(encoding="utf-8").strip()
                if text:
                    found.insert(0, text)
            except Exception:
                pass

        # Deduplicate: if workspace AGENT.md is the same as a chain file, skip
        workspace_agent_md = self.path / self.config.agent_md
        workspace_path_resolved = workspace_agent_md.resolve() if workspace_agent_md.exists() else None
        if workspace_path_resolved:
            found = [
                f for f in found
                if not (self.path.resolve() / self.config.agent_md).is_file()
                or f != (self.path.resolve() / self.config.agent_md).read_text(encoding="utf-8").strip()
            ]

        return "\n\n---\n\n".join(found) if found else ""

    def get_git_context(self, max_status_lines: int = 30) -> Optional[str]:
        """Get git status context for system prompt injection.

        Returns branch, uncommitted changes, and recent commits.
        Returns None if not in a git repo or git is unavailable.
        """
        cwd = str(self.path)
        try:
            subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=cwd, capture_output=True, check=True, timeout=5,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return None

        parts = []
        try:
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=cwd, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if branch:
                parts.append(f"Git branch: {branch}")
        except Exception:
            pass

        try:
            status = subprocess.run(
                ["git", "status", "--short"],
                cwd=cwd, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if status:
                lines = status.splitlines()
                if len(lines) > max_status_lines:
                    lines = lines[:max_status_lines] + [f"... ({len(lines) - max_status_lines} more)"]
                parts.append(f"Uncommitted changes:\n{chr(10).join(lines)}")
        except Exception:
            pass

        try:
            log = subprocess.run(
                ["git", "log", "--oneline", "-3"],
                cwd=cwd, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if log:
                parts.append(f"Recent commits:\n{log}")
        except Exception:
            pass

        return "\n".join(parts) if parts else None

    # =========================================================================
    # Memory index constants (mirrors CC's MEMORY.md limits)
    # =========================================================================
    _MEMORY_INDEX_MAX_LINES: int = 200
    _MEMORY_INDEX_MAX_BYTES: int = 25_000

    # Injected after memory content to guard against stale references.
    _MEMORY_DRIFT_DEFENSE: str = (
        "Note: memories reflect the state at write time. "
        "If a memory references a specific file path, function, or flag, "
        "verify it still exists before recommending it."
    )

    async def get_relevant_memories(
        self,
        query: str = "",
        limit: int = 5,
        already_surfaced: Optional[set] = None,
    ) -> str:
        """Load MEMORY.md index, score entries against query, return top-k content.

        Implements CC-style relevance-based recall instead of dumping all memory:
        - Parses MEMORY.md as an index of entry links
        - Scores each entry against the query with keyword overlap
        - Loads only the top-k most relevant entry files
        - Appends a drift-defense note to guard against stale references

        Falls back to loading all entries when query is empty (same as before).

        Args:
            query: Current user query (used for relevance scoring)
            limit: Maximum number of memory entries to return
            already_surfaced: Set of filenames already shown this session (dedup)

        Returns:
            Formatted memory string ready for system prompt injection, or empty string.
        """
        self._initialize_user_dir()
        index_path = self._get_user_memory_md()
        memory_dir = self._get_user_memory_dir()

        if not index_path.exists() and not memory_dir.exists():
            return ""

        # --- Parse MEMORY.md index ---
        index_entries: List[Dict] = []
        if index_path.exists():
            index_content = (await _async_read_text(index_path)).strip()
            if index_content:
                index_entries = self._parse_memory_index(index_content)

        # --- If no structured index exists, fall back to listing memory dir files ---
        if not index_entries and memory_dir.exists():
            for f in sorted(memory_dir.glob("*.md"), reverse=True):
                index_entries.append({
                    "title": f.stem,
                    "filename": f.name,
                    "hook": f.stem.replace("_", " "),
                })

        if not index_entries:
            return ""

        # --- Filter already-surfaced entries (avoid repeating in same session) ---
        if already_surfaced:
            index_entries = [e for e in index_entries if e["filename"] not in already_surfaced]

        if not index_entries:
            return ""

        # --- Score entries against query ---
        if query.strip():
            scored = self._score_memory_entries(query, index_entries)
        else:
            # No query: take the most recent entries (already sorted by recency from glob)
            scored = index_entries[:limit]

        top_entries = scored[:limit]

        # --- Load file content for selected entries ---
        parts = []
        for entry in top_entries:
            content_path = memory_dir / entry["filename"]
            if content_path.exists():
                raw = (await _async_read_text(content_path)).strip()
                # Strip frontmatter (---...---) before injecting
                body = self._strip_frontmatter(raw)
                if body:
                    parts.append(f"### {entry['title']}\n\n{body}")
                    # Write back to already_surfaced for session-level dedup
                    if already_surfaced is not None:
                        already_surfaced.add(entry["filename"])

        if not parts:
            return ""

        result = "\n\n".join(parts)
        result += f"\n\n*{self._MEMORY_DRIFT_DEFENSE}*"
        return result

    async def write_memory_entry(
        self,
        title: str,
        content: str,
        memory_type: str = "project",
        description: str = "",
    ) -> str:
        """Write a typed memory entry as an individual file and update MEMORY.md index.

        Each entry gets its own .md file under users/{user_id}/memory/ with a
        YAML frontmatter header (name, description, type). The MEMORY.md index
        is updated with a single-line reference to the new file.

        The description field is the key relevance signal — it should contain
        searchable keywords that identify when this memory is relevant.

        Args:
            title: Short display name for the memory
            content: Full memory content (why + how to apply)
            memory_type: One of "user", "feedback", "project", "reference"
            description: One-line hook for relevance scoring (defaults to title)

        Returns:
            Absolute path to the written memory file.
        """
        self._initialize_user_dir()
        memory_dir = self._get_user_memory_dir()
        memory_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize title to a safe filename
        safe_title = re.sub(r"[^\w\-]", "_", title.lower())[:50].strip("_")
        filename = f"{memory_type}_{safe_title}.md"
        filepath = memory_dir / filename

        hook = description or title
        frontmatter = (
            f"---\nname: {title}\n"
            f"description: {hook}\n"
            f"type: {memory_type}\n---\n\n"
        )
        await _async_write_text(filepath, frontmatter + content)

        # Update MEMORY.md index
        await self._update_memory_index(
            index_path=self._get_user_memory_md(),
            filename=filename,
            title=title,
            hook=hook,
        )

        return str(filepath)

    async def _update_memory_index(
        self,
        index_path: Path,
        filename: str,
        title: str,
        hook: str,
    ) -> None:
        """Append or update an entry in MEMORY.md index, enforcing size limits.

        Format: `- [Title](memory/filename.md) — one-line hook`
        Limits: 200 lines / 25KB (CC convention). Oldest entries are evicted.
        """
        new_entry = f"- [{title}](memory/{filename}) — {hook[:100]}"

        existing = ""
        if index_path.exists():
            existing = (await _async_read_text(index_path)).strip()

        lines = [l for l in existing.splitlines() if l.strip()] if existing else []

        # Remove existing entry for this file (update case)
        lines = [l for l in lines if f"(memory/{filename})" not in l]
        lines.append(new_entry)

        # Enforce hard limits: evict oldest entries from the front
        while len(lines) > self._MEMORY_INDEX_MAX_LINES:
            lines.pop(0)

        content = "\n".join(lines)
        while len(content.encode("utf-8")) > self._MEMORY_INDEX_MAX_BYTES:
            if not lines:
                break
            lines.pop(0)
            content = "\n".join(lines)

        await _async_write_text(index_path, content)

    def _parse_memory_index(self, index_content: str) -> List[Dict]:
        """Parse MEMORY.md index lines into entry dicts.

        Expected format: `- [Title](memory/filename.md) — one-line hook`
        """
        entries = []
        for line in index_content.splitlines():
            m = re.match(r"-\s+\[(.+?)\]\(memory/(.+?)\)\s*[—\-]\s*(.+)", line)
            if m:
                entries.append({
                    "title": m.group(1).strip(),
                    "filename": m.group(2).strip(),
                    "hook": m.group(3).strip(),
                })
        return entries

    @staticmethod
    def _compute_relevance_score(query_lower: str, text_lower: str) -> float:
        """Compute relevance score using hybrid word + character bigram matching.

        Supports both English (word-level) and CJK (character bigram) queries.

        Args:
            query_lower: Lowercased query string
            text_lower: Lowercased text to match against

        Returns:
            Relevance score (0.0 = no match, higher = better match)
        """
        word_tokens = set(query_lower.split())
        char_bigrams: set = set()
        for i in range(len(query_lower) - 1):
            bigram = query_lower[i:i + 2].strip()
            if bigram:
                char_bigrams.add(bigram)

        if not word_tokens and not char_bigrams:
            return 0.0

        score = 0.0
        if word_tokens:
            word_hits = sum(1.0 for w in word_tokens if w in text_lower)
            score += word_hits / len(word_tokens)
        if char_bigrams:
            ngram_hits = sum(1.0 for ng in char_bigrams if ng in text_lower)
            score += 0.5 * ngram_hits / len(char_bigrams)
        return score

    def _score_memory_entries(self, query: str, entries: List[Dict]) -> List[Dict]:
        """Score memory entries by token overlap with query.

        Returns entries sorted by score descending. Entries with score=0 are
        included at the end (ensures fallback when no token matches).
        """
        query_lower = query.lower()
        scored = []
        for entry in entries:
            text = f"{entry['title']} {entry['hook']}".lower()
            score = self._compute_relevance_score(query_lower, text)
            scored.append({**entry, "_score": score})

        scored.sort(key=lambda x: -x["_score"])
        return scored

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """Remove YAML frontmatter block (---...---) from memory file content."""
        stripped = re.sub(r"^---[\s\S]*?---\s*", "", content, flags=re.MULTILINE).strip()
        return stripped if stripped else content

    async def write_memory(self, content: str, to_daily: bool = True):
        """Write memory content. Delegates to write_memory_entry() for indexed storage.

        For backward compatibility. New code should use write_memory_entry() directly.

        Args:
            content: Memory content
            to_daily: Ignored (kept for API compatibility). All entries go to memory/ dir.
        """
        # Derive a title from the first 50 chars of content
        title = content[:50].strip().replace("\n", " ")
        if not title:
            title = "untitled"
        await self.write_memory_entry(
            title=title,
            content=content,
            memory_type="project",
            description=title,
        )

    async def save_memory(self, content: str, long_term: bool = False):
        """Save memory (alias for write_memory, kept for backward compatibility).

        Args:
            content: Memory content
            long_term: Ignored (kept for API compatibility).
        """
        await self.write_memory(content)

    def get_skills_dir(self) -> Path:
        """Get skills directory path.

        Returns:
            Absolute path to skills directory
        """
        return self.path / self.config.skills_dir

    def list_files(self) -> Dict[str, bool]:
        """List workspace global file status.

        Returns:
            Dictionary with file names as keys and existence status as values.
            Note: Only lists globally shared files, not user-specific files.
        """
        # Only list globally shared config files
        files = [
            self.config.agent_md,
            self.config.persona_md,
            self.config.tools_md,
        ]
        return {f: (self.path / f).exists() for f in files}

    def get_all_memory_files(self) -> List[Path]:
        """Get all memory file paths for current user.

        Returns:
            List of all memory file paths
        """
        files = []

        # Long-term memory
        memory_md = self._get_user_memory_md()
        if memory_md.exists():
            files.append(memory_md)

        # Daily memory
        memory_dir = self._get_user_memory_dir()
        if memory_dir.exists():
            files.extend(sorted(memory_dir.glob("*.md"), reverse=True))

        return files

    def search_memory(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.1,
    ) -> List[Dict]:
        """Search memory with hybrid word + character n-gram matching.

        Uses a combination of word-level matching (for English and space-delimited
        languages) and character bigram matching (for CJK languages like Chinese)
        to support multilingual queries.

        Args:
            query: Search query (supports English, Chinese, and mixed)
            limit: Maximum number of results
            min_score: Minimum match score threshold

        Returns:
            List of matching memories, each containing content, file_path, score
        """
        query_lower = query.lower()
        if not query_lower.strip():
            return []

        results = []
        for file_path in self.get_all_memory_files():
            content = file_path.read_text(encoding="utf-8").strip()
            if not content:
                continue

            score = self._compute_relevance_score(query_lower, content.lower())

            if score >= min_score:
                results.append({
                    "content": content,
                    "file_path": str(file_path.relative_to(self.path)),
                    "score": round(score, 4),
                })

        results.sort(key=lambda x: -x["score"])
        return results[:limit]

    def clear_daily_memory(self, keep_days: int = 7):
        """Clear old daily memory files (date-pattern only).

        Only deletes files matching YYYY-MM-DD.md pattern. Typed memory entry
        files (e.g. user_role.md, project_deploy.md) are never deleted.

        Args:
            keep_days: Number of most recent date files to keep
        """
        memory_dir = self._get_user_memory_dir()
        if not memory_dir.exists():
            return

        # Only match date-pattern files: YYYY-MM-DD.md
        date_files = sorted(
            [f for f in memory_dir.glob("*.md") if re.match(r"\d{4}-\d{2}-\d{2}\.md$", f.name)],
            reverse=True,
        )
        for f in date_files[keep_days:]:
            f.unlink()

    # =========================================================================
    # Conversation Archive
    # =========================================================================

    def _get_user_conversations_dir(self) -> Path:
        """Get current user's conversation archive directory."""
        return self._get_user_path() / self.config.conversations_dir

    def _get_archive_lock(self, filepath: Path) -> asyncio.Lock:
        """Get or create a per-file asyncio.Lock for serializing archive writes."""
        key = str(filepath)
        if key not in self._archive_locks:
            self._archive_locks[key] = asyncio.Lock()
        return self._archive_locks[key]

    async def archive_conversation(self, messages: List[Dict], session_id: Optional[str] = None) -> str:
        """Archive a conversation to daily Markdown file.

        Messages are appended to users/{user_id}/conversations/YYYY-MM-DD.md.
        Uses per-file locking to prevent concurrent write-write races.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            session_id: Optional session identifier for grouping

        Returns:
            Path to the archive file
        """
        self._initialize_user_dir()
        conv_dir = self._get_user_conversations_dir()
        conv_dir.mkdir(parents=True, exist_ok=True)

        today = date.today().isoformat()
        filepath = conv_dir / f"{today}.md"

        now = datetime.now().strftime("%H:%M:%S")
        header = f"\n\n---\n\n### {now}"
        if session_id:
            header += f" (session: {session_id})"
        header += "\n\n"

        lines = [header]
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue
            # Truncate very long messages in archive
            if len(content) > 2000:
                content = content[:2000] + "\n...[truncated]"
            lines.append(f"**{role}**: {content}\n\n")

        archive_text = "".join(lines)

        # Use per-file lock to serialize concurrent writes
        lock = self._get_archive_lock(filepath)
        async with lock:
            existing = ""
            if filepath.exists():
                existing = (await _async_read_text(filepath)).strip()
            new_content = f"{existing}{archive_text}".strip() if existing else archive_text.strip()
            await _async_write_text(filepath, new_content)

        return str(filepath)

    def search_conversations(
        self,
        query: str,
        limit: int = 10,
        max_files: Optional[int] = None,
    ) -> List[Dict]:
        """Search conversation archive by keyword.

        Args:
            query: Search query (keyword matching)
            limit: Maximum number of matching blocks to return
            max_files: Only search the most recent N archive files (None = search all)

        Returns:
            List of matching conversation blocks with date, content, score
        """
        conv_dir = self._get_user_conversations_dir()
        if not conv_dir.exists():
            return []

        files = sorted(conv_dir.glob("*.md"), reverse=True)
        if max_files is not None:
            files = files[:max_files]

        query_lower = query.lower()
        query_words = query_lower.split()
        results = []

        for filepath in files:
            content = filepath.read_text(encoding="utf-8").strip()
            if not content:
                continue

            # Split into conversation blocks by ---
            blocks = content.split("---")
            for block in blocks:
                block = block.strip()
                if not block:
                    continue
                block_lower = block.lower()
                score = sum(1.0 for w in query_words if w in block_lower) / max(len(query_words), 1)
                if score > 0:
                    results.append({
                        "date": filepath.stem,
                        "content": block[:500] + ("..." if len(block) > 500 else ""),
                        "file_path": str(filepath.relative_to(self.path)),
                        "score": score,
                    })

        results.sort(key=lambda x: -x["score"])
        return results[:limit]

    def get_conversation_files(self, max_files: Optional[int] = None) -> List[Path]:
        """Get conversation archive files for current user.

        Args:
            max_files: Only return the most recent N files (None = return all)

        Returns:
            List of conversation file paths, newest first
        """
        conv_dir = self._get_user_conversations_dir()
        if not conv_dir.exists():
            return []
        files = sorted(conv_dir.glob("*.md"), reverse=True)
        if max_files is not None:
            files = files[:max_files]
        return files

    def __repr__(self) -> str:
        return f"Workspace(path={self.path}, exists={self.exists()}, user_id={self._user_id})"

    def __str__(self) -> str:
        return str(self.path)

    def list_users(self) -> List[str]:
        """List all registered user IDs.

        Returns:
            List of user IDs
        """
        users_dir = self.path / self.config.users_dir
        if not users_dir.exists():
            return []

        users = []
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir():
                users.append(user_dir.name)
        return sorted(users)

    def get_user_info(self, user_id: Optional[str] = None) -> Dict:
        """Get user information summary.

        Args:
            user_id: User ID, uses current user if not specified

        Returns:
            User info dictionary containing user_id, memory_count, last_activity, etc.
        """
        target_user = user_id or self._user_id
        old_user = self._user_id

        try:
            self._user_id = target_user

            memory_files = self.get_all_memory_files()
            memory_count = len(memory_files)

            last_activity = None
            if memory_files:
                # Get modification time of latest memory file
                latest_file = memory_files[0]
                if latest_file.exists():
                    mtime = latest_file.stat().st_mtime
                    last_activity = datetime.fromtimestamp(mtime).isoformat()

            return {
                "user_id": target_user,
                "memory_count": memory_count,
                "last_activity": last_activity,
                "user_path": str(self._get_user_path()),
            }
        finally:
            self._user_id = old_user

    def delete_user(self, user_id: str, confirm: bool = False) -> bool:
        """Delete user data.

        Args:
            user_id: User ID to delete
            confirm: Must be set to True to execute deletion

        Returns:
            Whether deletion was successful
        """
        if not confirm:
            raise ValueError("Must set confirm=True to delete user data")

        if not user_id:
            raise ValueError("user_id cannot be empty")

        safe_user_id = user_id.replace("/", "_").replace("\\", "_").replace("..", "_")
        user_path = self.path / self.config.users_dir / safe_user_id

        if not user_path.exists():
            return False

        shutil.rmtree(user_path)
        return True
