# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Built-in tools for Agent

Built-in tool set for Agent, including:
- ls: List directory contents
- read_file: Read file content
- write_file: Write file content
- edit_file: Edit file (string replacement)
- multi_edit_file: Apply multiple edits to a file atomically
- glob: File pattern matching
- grep: Search file content
- execute: Execute command
- web_search: Web search (implemented using BaiduSearch)
- fetch_url: Fetch URL content (implemented using UrlCrawler)
- write_todos: Create and manage task list
- task: Launch subagent to handle complex tasks
"""
import asyncio
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
import time
import uuid
from pathlib import Path
from textwrap import dedent
from typing import Optional, List, Dict, Any, Literal, TYPE_CHECKING, Union

import aiofiles

from agentica.tools.base import Tool
from agentica.tools.builtin_task_tool import BuiltinTaskTool  # re-export after extraction
from agentica.utils.log import logger
from agentica.utils.string import truncate_if_too_long

if TYPE_CHECKING:
    from agentica.agent import Agent
    from agentica.model.base import Model


class BuiltinFileTool(Tool):
    """
    Built-in file system tool providing ls, read_file, write_file, edit_file, multi_edit_file, glob, grep functions.
    """

    def __init__(
            self,
            work_dir: Optional[str] = None,
            max_read_lines: int = 500,
            max_line_length: int = 2000,
            sandbox_config=None,
    ):
        """
        Initialize BuiltinFileTool.

        Args:
            work_dir: Work directory for file operations, defaults to current working directory
            max_read_lines: Maximum number of lines to read by default
            max_line_length: Maximum length per line, longer lines will be truncated
            sandbox_config: SandboxConfig instance for path restriction enforcement
        """
        super().__init__(name="builtin_file_tool")
        self.work_dir = Path(work_dir) if work_dir else Path.cwd()
        self.max_read_lines = max_read_lines
        self.max_line_length = max_line_length
        self._file_locks: Dict[str, asyncio.Lock] = {}
        self._sandbox_config = sandbox_config

        # mtime cache: detect external modifications before edit/write.
        # Key: resolved absolute path (str), Value: {"mtime": float}
        self._file_read_state: Dict[str, Dict[str, Any]] = {}

        # Register all file operation functions.
        # Read-only tools are concurrency_safe (can run in parallel with each other).
        # Write tools (write_file, edit_file, multi_edit_file) stay serialised.
        self.register(self.ls, concurrency_safe=True, is_read_only=True)
        self.register(self.read_file, concurrency_safe=True, is_read_only=True)
        self.register(self.write_file, sanitize_arguments=False, is_destructive=True)
        self.register(self.edit_file, sanitize_arguments=False, is_destructive=True)
        self.register(self.multi_edit_file, sanitize_arguments=False, is_destructive=True)
        self.register(self.glob, concurrency_safe=True, is_read_only=True)
        self.register(self.grep, concurrency_safe=True, is_read_only=True)

    def _resolve_path(self, path: str) -> Path:
        """Resolve path, supporting absolute, relative, and ~ paths.

        - ~ paths are expanded to user home directory
        - Absolute paths are used directly
        - Relative paths are resolved relative to work_dir
        """
        # Expand ~ to user home directory
        if path.startswith("~"):
            return Path(path).expanduser()
        p = Path(path)
        if p.is_absolute():
            return p
        return self.work_dir / p

    def _get_file_lock(self, path: str) -> asyncio.Lock:
        """Get or create a per-file asyncio.Lock to serialize concurrent edits."""
        return self._file_locks.setdefault(path, asyncio.Lock())

    def _validate_path(self, path: str) -> str:
        """Validate path against sandbox restrictions and blocked device files.

        Always checks:
        - Path must not resolve to a known device file (/dev/zero, etc.)

        When sandbox is enabled, also checks:
        - Path components do not match any blocked_paths entries
        - Uses path component matching (not substring) to avoid false positives
        - For write operations, caller should use _validate_write_path instead

        Raises:
            PermissionError: If path is blocked by sandbox config or is a device file
        """
        resolved = self._resolve_path(path).resolve()

        # Device-file guard: always active regardless of sandbox setting.
        # Reading /dev/zero or /dev/random hangs indefinitely or exhausts memory.
        if str(resolved) in self.BLOCKED_DEVICE_FILES:
            raise PermissionError(
                f"Reading device file '{path}' is blocked for safety. "
                f"Resolved path: {resolved}"
            )

        if self._sandbox_config is None or not self._sandbox_config.enabled:
            return path
        resolved_parts = set(resolved.parts)
        for blocked in self._sandbox_config.blocked_paths:
            if blocked in resolved_parts:
                raise PermissionError(f"Sandbox: access to path containing '{blocked}' is blocked")
        return path

    def _validate_write_path(self, path: str) -> str:
        """Validate that a write operation is allowed under sandbox restrictions.

        Checks blocked_paths and writable_dirs whitelist.

        Raises:
            PermissionError: If write is not allowed
        """
        self._validate_path(path)
        if self._sandbox_config is None or not self._sandbox_config.enabled:
            return path
        resolved = str(self._resolve_path(path).resolve())
        # If writable_dirs is configured, enforce whitelist
        if self._sandbox_config.writable_dirs:
            allowed = False
            for wd in self._sandbox_config.writable_dirs:
                wd_resolved = str(Path(wd).expanduser().resolve())
                if resolved.startswith(wd_resolved):
                    allowed = True
                    break
            if not allowed:
                # Also allow work_dir
                work_dir_str = str(self.work_dir.resolve())
                if not resolved.startswith(work_dir_str):
                    raise PermissionError(
                        f"Sandbox: write to '{path}' is not allowed. "
                        f"Writable dirs: {self._sandbox_config.writable_dirs}"
                    )
        return path

    async def ls(self, directory: str = ".") -> str:
        """Lists all files in the directory.

        Usage:
        - The directory parameter can be an absolute or relative path
        - The ls tool will return a list of all files in the specified directory.
        - This is very useful for exploring the file system and finding the right file to read or edit.
        - You should almost ALWAYS use this tool before using the Read or Edit tools.

        Args:
            directory: Directory path to list files, defaults to current directory

        Returns:
            str, JSON formatted file list
        """
        try:
            self._validate_path(directory)
            dir_path = self._resolve_path(directory)

            if not dir_path.exists():
                return f"Error: Directory not found: {directory}"
            if not dir_path.is_dir():
                return f"Error: Not a directory: {directory}"

            def _ls_sync():
                items = []
                for item in sorted(dir_path.iterdir()):
                    item_type = "dir" if item.is_dir() else "file"
                    items.append({
                        "name": item.name,
                        "path": str(item),
                        "type": item_type,
                    })
                return items

            items = await asyncio.get_event_loop().run_in_executor(None, _ls_sync)

            logger.debug(f"Listed {len(items)} items in {dir_path}")
            result = json.dumps(items, ensure_ascii=False, indent=2)
            result = truncate_if_too_long(result)
            return str(result)
        except Exception as e:
            logger.error(f"Error listing directory {directory}: {e}")
            return f"Error listing directory: {e}"

    # Maximum file size (bytes) for read_file.  Larger files must use offset+limit.
    # Mirrors CC's FileReadTool maxSizeBytes (256KB).
    MAX_FILE_SIZE_BYTES = 256_000

    # Device files that must never be read: reading /dev/zero or /dev/random
    # hangs indefinitely or exhausts memory.  Absolute paths only — checked
    # after resolving the input path so symlinks cannot bypass the guard.
    BLOCKED_DEVICE_FILES: frozenset = frozenset({
        "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
        "/dev/tty", "/dev/stdin", "/dev/stdout", "/dev/stderr",
        "/dev/mem", "/dev/kmem", "/dev/port",
    })

    async def read_file(
            self,
            file_path: str,
            offset: int = 0,
            limit: Optional[int] = 500,
    ) -> str:
        """Reads a file from the filesystem. You can access any file directly by using this tool.
        Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

        Usage:
        - The file_path parameter may be absolute, relative to the working directory, or `~`-prefixed
        - Relative paths are resolved relative to the base working directory
        - By default, it reads up to 500 lines starting from the beginning of the file
        - **IMPORTANT for large files and codebase exploration**: Use pagination with offset and limit parameters to avoid context overflow
        - First scan: read_file(path, limit=100) to see file structure
        - Read more sections: read_file(path, offset=100, limit=200) for next 200 lines
        - Only omit limit (read full file) when necessary for editing
        - Specify offset and limit: read_file(path, offset=0, limit=100) reads first 100 lines
        - Any lines longer than 2000 characters will be truncated
        - Results are returned in numbered-line format (line_number + content), starting at line 1
        - You have the capability to call multiple tools in a single response. It is always better to speculatively read multiple files as a batch that are potentially useful.
        - If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.
        - You should ALWAYS make sure a file has been read before editing it.

        Args:
            file_path: File path for md/txt/py/etc. Supports absolute paths, relative paths, and `~`
            offset: Starting line number (0-based)
            limit: Maximum number of lines to read, defaults to 500

        Returns:
            File content with line numbers
        """
        try:
            self._validate_path(file_path)
            path = self._resolve_path(file_path)

            if not path.exists():
                return f"Error: File not found: {file_path}"
            if not path.is_file():
                return f"Error: Not a file: {file_path}"

            abs_path = str(path.resolve())

            # --- Large-file guard (mirrors CC's maxSizeBytes) ---
            try:
                file_size = path.stat().st_size
                if file_size > self.MAX_FILE_SIZE_BYTES:
                    loop = asyncio.get_running_loop()
                    total_lines = await loop.run_in_executor(
                        None, lambda: sum(1 for _ in open(path, errors='ignore'))
                    )
                    return (
                        f"Error: File too large ({file_size:,} bytes, {total_lines:,} lines). "
                        f"Use offset and limit to read specific sections.\n"
                        f"Example: read_file('{file_path}', offset=0, limit=100)"
                    )
            except OSError:
                pass  # stat failed — proceed with read, let it fail naturally

            limit = limit if limit is not None else self.max_read_lines
            max_line_len = self.max_line_length

            # Async streaming read — only read the lines we need
            output_lines = []
            total_lines = 0
            end_line = offset + limit
            async with aiofiles.open(path, 'r', encoding='utf-8', errors='ignore') as f:
                async for line in f:
                    total_lines += 1
                    if total_lines > offset and total_lines <= end_line:
                        line = line.rstrip('\n\r')
                        if len(line) > max_line_len:
                            line = line[:max_line_len] + "..."
                        output_lines.append(f"{total_lines:6d}\t{line}")

            result = "\n".join(output_lines)

            # Add file info if truncated
            actual_end = min(offset + len(output_lines), total_lines)
            if actual_end < total_lines:
                result += f"\n\n[Showing lines {offset + 1}-{actual_end} of {total_lines} total lines]"

            # Record mtime so edit_file can detect external modifications
            try:
                self._file_read_state[abs_path] = {"mtime": path.stat().st_mtime}
            except OSError:
                pass

            logger.debug(f"Read file {file_path}: lines {offset + 1}-{actual_end}, total {total_lines} lines")
            return result
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return f"Error reading file: {e}"

    async def write_file(self, file_path: str, content: str) -> str:
        """Writes content to a file in the filesystem.

        Usage:
        - If this is an existing file, you MUST use read_file first to read the file's contents.
          This tool will create a new file or OVERWRITE the existing file entirely.
        - Prefer edit_file for modifying existing files — it only sends the diff.
          Only use write_file to create NEW files or for complete rewrites.
        - The file_path can be relative (e.g., "tmp/script.py", "./outputs/data.txt") or absolute path.
          Relative paths are resolved relative to the base working directory.
        - The tool returns the actual absolute path of the created file — ALWAYS use this returned
          path for subsequent operations (read_file, execute, etc.). Do NOT guess or construct paths.
        - Parent directories will be created automatically if they don't exist.

        Args:
            file_path: File path (relative or absolute). Examples: "tmp/script.py", "outputs/result.txt", "./tmp/main.py", use './tmp/' prefix file path for temporary files
            content: File content to write

        Returns:
            Operation result message containing the actual absolute path of the file
        """
        try:
            self._validate_write_path(file_path)
            path = self._resolve_path(file_path)
            # Ensure directory exists
            path.parent.mkdir(parents=True, exist_ok=True)
            action = "Created" if not path.exists() else "Updated"

            # Atomic write: write to temp file then rename to avoid partial writes
            tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                os.close(tmp_fd)
                async with aiofiles.open(tmp_path, 'w', encoding='utf-8') as f:
                    await f.write(content)
                # Atomic rename
                os.replace(tmp_path, str(path))
            except Exception:
                # Clean up temp file on error
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            # Return absolute path to help LLM use correct path in subsequent operations
            absolute_path = str(path.resolve())
            try:
                self._file_read_state[absolute_path] = {"mtime": path.stat().st_mtime}
            except OSError:
                self._file_read_state.pop(absolute_path, None)
            logger.debug(f"{action} file: {absolute_path}, file content length: {len(content)} characters")
            return f"{action} file, absolute path: {absolute_path}"
        except Exception as e:
            logger.error(f"Error writing file {file_path}: {e}")
            return f"Error writing file: {e}"

    async def edit_file(
            self,
            file_path: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
    ) -> str:
        """Replace a specific string in a file.

        You MUST use read_file at least once before editing a file.
        This tool will error if the file has been modified externally since your last read.

        Uses literal string matching (NOT regex). Multi-line strings are supported.
        Prefer this tool over write_file or shell `sed` for targeted changes.

        When editing text from read_file output, ensure you preserve the exact indentation
        (tabs/spaces) as it appears in the file. The line number prefix in read_file output
        is metadata only — never include it in old_string or new_string.

        The edit will FAIL if old_string is not unique in the file. Either provide a
        larger string with more surrounding context to make it unique, or use
        replace_all=True to change every instance.

        For multiple edits to the SAME file, prefer `multi_edit_file` to apply them
        atomically in one call. If you call `edit_file` multiple times on the same file
        in parallel, they will be serialized automatically to avoid race conditions.
        File paths may be absolute, relative to the working directory, or `~`-prefixed.

        Args:
            file_path: The path to the file to edit. Supports absolute paths, relative
                      paths, and `~`. Relative paths resolve from the working directory.
            old_string: The existing text to find and replace. Must match exactly.
            new_string: The replacement text.
            replace_all: Whether to replace all occurrences. Default: False (replace first
                        match only; errors if multiple matches found).

        Returns:
            Operation result message

        Examples:
            edit_file("app.py", "def foo():", "def bar():")
            edit_file("config.py", "DEBUG = True", "DEBUG = False")
            edit_file("test.py", "old_name", "new_name", replace_all=True)
        """
        try:
            self._validate_write_path(file_path)
            path = self._resolve_path(file_path)
            path_key = str(path)

            if not path.exists():
                return f"Error: File not found: {file_path}"
            if not path.is_file():
                return f"Error: Not a file: {file_path}"

            # Per-file lock to serialize concurrent edits on the same file
            lock = self._get_file_lock(path_key)
            async with lock:
                # mtime guard: detect external modifications since last read.
                # Mirrors CC's FileEdit read-before-write protocol.
                abs_path = str(path.resolve())
                try:
                    current_mtime = path.stat().st_mtime
                except OSError:
                    current_mtime = None
                if current_mtime is not None and abs_path in self._file_read_state:
                    prev_mtime = self._file_read_state[abs_path].get("mtime")
                    if prev_mtime is not None and current_mtime != prev_mtime:
                        logger.warning(
                            f"File '{file_path}' was modified externally since last read "
                            f"(mtime {prev_mtime} -> {current_mtime}). "
                            f"Please re-read the file before editing."
                        )
                        return (
                            f"Warning: File '{file_path}' was modified externally since your last read. "
                            f"Please re-read the file with read_file() before editing to avoid "
                            f"overwriting someone else's changes."
                        )

                async with aiofiles.open(path, 'r', encoding='utf-8') as f:
                    content = await f.read()

                result = self._str_replace(content, old_string, new_string, replace_all)

                if not result["success"]:
                    return f"Error: {result['error']}"

                # Atomic write back
                tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
                try:
                    os.close(tmp_fd)
                    async with aiofiles.open(tmp_path, 'w', encoding='utf-8') as f:
                        await f.write(result["new_content"])
                    os.replace(tmp_path, str(path))
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise

            logger.debug(f"Replaced {result['count']} occurrence(s) in {file_path}")
            try:
                self._file_read_state[abs_path] = {"mtime": path.stat().st_mtime}
            except OSError:
                self._file_read_state.pop(abs_path, None)
            return f"Successfully replaced {result['count']} occurrence(s) in '{file_path}'"

        except Exception as e:
            logger.error(f"Error editing file {file_path}: {e}")
            return f"Error editing file: {e}"

    async def multi_edit_file(
            self,
            file_path: str,
            edits: List[Dict[str, Any]],
    ) -> str:
        """Apply multiple edits to a single file atomically.

        All edits are applied sequentially on the same in-memory content,
        then written back once. If any edit fails, no changes are made.

        This is preferred over multiple parallel `edit_file` calls when you need
        to make several changes to the same file — it is faster, uses fewer tokens,
        and guarantees atomicity.

        Args:
            file_path: Path to the file to edit.
            edits: List of edit operations. Each dict must contain:
                - old_string (str): The existing text to find
                - new_string (str): The replacement text
                - replace_all (bool, optional): Replace all occurrences. Default: False

        Returns:
            Summary of all applied edits, or error message if any edit fails.

        Examples:
            multi_edit_file("app.py", [
                {"old_string": "foo", "new_string": "bar"},
                {"old_string": "DEBUG = True", "new_string": "DEBUG = False"},
            ])
        """
        try:
            self._validate_write_path(file_path)
            path = self._resolve_path(file_path)
            path_key = str(path)

            if not path.exists():
                return f"Error: File not found: {file_path}"
            if not path.is_file():
                return f"Error: Not a file: {file_path}"
            if not edits:
                return "Error: 'edits' list cannot be empty."

            lock = self._get_file_lock(path_key)
            async with lock:
                async with aiofiles.open(path, 'r', encoding='utf-8') as f:
                    content = await f.read()

                # Apply edits sequentially on in-memory content
                results = []
                for i, edit in enumerate(edits):
                    old_string = edit.get("old_string", "")
                    new_string = edit.get("new_string", "")
                    replace_all = edit.get("replace_all", False)

                    if not old_string:
                        return f"Error: Edit {i + 1} has empty old_string. No changes were made."

                    result = self._str_replace(content, old_string, new_string, replace_all)
                    if not result["success"]:
                        return f"Error in edit {i + 1}/{len(edits)}: {result['error']}. No changes were made."

                    content = result["new_content"]
                    results.append(f"Edit {i + 1}: replaced {result['count']} occurrence(s)")

                # Atomic write (once)
                tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
                try:
                    os.close(tmp_fd)
                    async with aiofiles.open(tmp_path, 'w', encoding='utf-8') as f:
                        await f.write(content)
                    os.replace(tmp_path, str(path))
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise

            summary = f"Successfully applied {len(edits)} edits to '{file_path}':\n" + "\n".join(results)
            logger.debug(summary)
            return summary

        except Exception as e:
            logger.error(f"Error multi-editing file {file_path}: {e}")
            return f"Error multi-editing file: {e}"

    @staticmethod
    def _normalize_quotes(s: str) -> str:
        """Replace curly/typographic quotes with their ASCII equivalents.

        LLMs sometimes emit curly quotes (\u201c\u201d\u2018\u2019) when the
        source file uses straight ASCII quotes, causing exact-match failures.
        """
        return (
            s.replace('\u201c', '"').replace('\u201d', '"')   # left/right double
             .replace('\u2018', "'").replace('\u2019', "'")   # left/right single
             .replace('\u2032', "'").replace('\u2033', '"')   # prime / double prime
        )

    def _str_replace(
            self,
            content: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
    ) -> dict:
        """Internal string replacement logic.

        Tries exact match first.  If that fails, retries after normalizing
        curly/typographic quotes in old_string to ASCII equivalents — LLMs
        sometimes emit curly quotes when the file uses straight ASCII quotes.

        Returns:
            {"success": bool, "new_content": str, "count": int, "error": str}
        """
        # Find all match positions
        matches = []
        start = 0
        while True:
            idx = content.find(old_string, start)
            if idx == -1:
                break
            matches.append(idx)
            start = idx + len(old_string)

        # Quote-normalization fallback: if exact match failed, retry with
        # normalized quotes.  We search the normalized content for the
        # normalized needle, then map positions back to the original content
        # so the actual replacement preserves the file's quote style.
        if not matches:
            norm_content = self._normalize_quotes(content)
            norm_old = self._normalize_quotes(old_string)
            if norm_old != old_string and norm_old in norm_content:
                # Find positions in normalized content, then use them on original
                # (lengths are identical because _normalize_quotes is 1-to-1).
                start = 0
                while True:
                    idx = norm_content.find(norm_old, start)
                    if idx == -1:
                        break
                    matches.append(idx)
                    start = idx + len(norm_old)
                if matches:
                    # Replace using the normalized needle on the original content
                    # (positions are identical because character counts don't change).
                    old_string = norm_old
                    content_for_replace = norm_content
                else:
                    content_for_replace = content
            else:
                content_for_replace = content
        else:
            content_for_replace = content

        if not matches:
            display_old = old_string[:100] + "..." if len(old_string) > 100 else old_string
            return {
                "success": False,
                "error": f"String not found: '{display_old}'",
                "new_content": content,
                "count": 0,
            }

        # If not replace_all and multiple matches, show context for each match
        if not replace_all and len(matches) > 1:
            contexts = []
            for idx in matches[:3]:  # Show first 3 matches
                line_num = content_for_replace[:idx].count('\n') + 1
                # Get surrounding context (up to 50 chars around the match)
                context_start = max(0, idx - 20)
                context_end = min(len(content_for_replace), idx + len(old_string) + 30)
                context = content_for_replace[context_start:context_end].replace('\n', '\\n')
                contexts.append(f"  Line {line_num}: ...{context}...")

            error_msg = (
                f"Found {len(matches)} occurrences of the string.\n"
                f"Use replace_all=True to replace all, or provide more context to make it unique.\n"
                f"Matches found at:\n" + '\n'.join(contexts)
            )
            if len(matches) > 3:
                error_msg += f"\n  ... and {len(matches) - 3} more"

            return {
                "success": False,
                "error": error_msg,
                "new_content": content,
                "count": len(matches),
            }

        # Perform replacement
        if replace_all:
            new_content = content_for_replace.replace(old_string, new_string)
            count = len(matches)
        else:
            # Replace only the first match (leftmost)
            idx = matches[0]
            new_content = content_for_replace[:idx] + new_string + content_for_replace[idx + len(old_string):]
            count = 1

        return {
            "success": True,
            "new_content": new_content,
            "count": count,
            "error": None,
        }

    async def glob(self, pattern: str, path: str = ".") -> str:
        """Find files matching a glob pattern (supports recursive search with `**`).

        Usage:
        - This tool searches for files by matching standard glob wildcards, returns JSON formatted absolute file paths
        - Core glob wildcards (key differences):
        1. `*`: Matches any files in the **current specified single directory** (non-recursive, no deep subdirectories)
        2. `**`: Matches any directories recursively (penetrates all deep subdirectories for cross-level search)
        3. `?`: Matches any single character (e.g., "file?.txt" matches "file1.txt", "filea.txt")
        - Patterns can be absolute (starting with `/`, e.g., "/home/user/*.py") or relative (e.g., "docs/*.md")
        - Automatically excludes common useless directories (.git, __pycache__, etc.) to filter valid files
        - Returns empty JSON list if no matching files are found

        Examples (clear parameter correspondence and function explanation):
        - pattern: `*.py`, path: "." - Find all Python files in the current working directory (non-recursive)
        - pattern: `*.txt`, path: "." - Find all text files in the current working directory (non-recursive)
        - pattern: `**/*.md`, path: "/path/to/subdir/" - Find all markdown files in all levels under /path/to/subdir/ (recursive)
        - pattern: `subdir/*.md`, path: "." - Find all markdown files directly in the "subdir" folder (non-recursive, no deep subdirs)

        Args:
            pattern: Valid glob search pattern, e.g., "*.py", "**/*.md", "src/?*.js"
            path: Starting search directory (relative or absolute), defaults to current working directory (".").

        Returns:
            JSON formatted string of sorted absolute file paths (filtered to exclude ignored directories).
            Error message string if directory not found or other exceptions occur.
        """
        try:
            self._validate_path(path)
            base_path = self._resolve_path(path)

            if not base_path.exists():
                return f"Error: Directory not found: {path}"

            # Run glob in executor to avoid blocking on large directory trees
            def _glob_sync():
                matches = list(base_path.glob(pattern))
                ignore_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.pytest_cache'}
                return sorted(
                    str(m) for m in matches
                    if not set(m.parts).intersection(ignore_dirs)
                )

            filtered = await asyncio.get_event_loop().run_in_executor(None, _glob_sync)

            logger.debug(f"Glob found {len(filtered)} files matching pattern '{pattern}' in directory '{path}'")
            # Convert to formatted JSON string
            result = json.dumps(filtered, ensure_ascii=False, indent=2)
            # Truncate if content exceeds the limit to avoid excessive output
            result = truncate_if_too_long(result)
            return str(result)
        except Exception as e:
            logger.error(f"Exception occurred during glob search (pattern: '{pattern}', path: '{path}'): {str(e)}")
            return f"Error in glob search: {str(e)}"

    async def grep(
            self,
            pattern: str,
            path: str = ".",
            *,
            include: Optional[str] = None,
            output_mode: Literal["files_with_matches", "content", "count"] = "files_with_matches",
            case_insensitive: bool = False,
            multiline: bool = False,
            context_lines: int = 0,
            before_context: int = 0,
            after_context: int = 0,
            max_results: int = 100,
            fixed_strings: bool = False,
    ) -> str:
        """Search for a pattern in files using ripgrep (rg).

        Usage:
        - Searches text patterns across files, powered by ripgrep for maximum speed
        - The pattern parameter supports regex by default (e.g., 'class \\w+', 'def \\w+')
        - Use fixed_strings=True to treat pattern as literal text (no regex interpretation)
        - The path parameter specifies the search directory (default: current working directory)
        - The include parameter filters files by glob (e.g., "*.py", "*.{ts,tsx}")
        - The output_mode parameter is a plain string, one of: "files_with_matches", "content", "count"
          - "files_with_matches": List only file paths (default)
          - "content": Show matching lines with file path and line numbers
          - "count": Show match count per file
        - To add context lines in "content" mode, use the separate context_lines / before_context / after_context parameters
          e.g.: grep(pattern="foo", output_mode="content", context_lines=3)
        - Automatically falls back to pure Python search if ripgrep is not installed

        Args:
            pattern: Text/regex to search for
            path: Starting directory for search (default: ".")
            include: File glob filter, e.g., "*.py", "*.{js,ts}" (maps to rg --glob)
            output_mode: Plain string, one of "files_with_matches" (default), "content", or "count". Do NOT pass a dict.
            case_insensitive: Ignore case when matching (default: False)
            multiline: Enable multiline matching where . matches newlines (default: False)
            context_lines: Show N lines before and after each match (default: 0, content mode only)
            before_context: Show N lines before each match (default: 0, content mode only)
            after_context: Show N lines after each match (default: 0, content mode only)
            max_results: Maximum results to return (default: 100)
            fixed_strings: Treat pattern as literal text, not regex (default: False)

        Returns:
            Search results as formatted string

        Examples:
            grep(pattern="TODO", path="/project")
            grep(pattern="class \\w+", include="*.py", output_mode="content")
            grep(pattern="enable_agentic_prompt", output_mode="content", context_lines=3)
            grep(pattern="import", include="*.py", output_mode="count")
            grep(pattern="exact phrase", fixed_strings=True, output_mode="content")
        """
        # Resolve and validate path
        self._validate_path(path)
        base_path = self._resolve_path(path)
        if not base_path.exists():
            return f"Error: Directory not found: {path}"

        # Check if rg is available
        rg_path = shutil.which("rg")
        if rg_path is None:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._grep_fallback, pattern, path, include, output_mode,
                max_results, fixed_strings, case_insensitive,
            )

        # Build rg command arguments
        cmd: List[str] = [rg_path]

        # Output mode flags
        if output_mode == "files_with_matches":
            cmd.append("--files-with-matches")
        elif output_mode == "count":
            cmd.append("--count")
        else:  # content
            cmd.append("--line-number")

        # Matching options
        if fixed_strings:
            cmd.append("--fixed-strings")
        if case_insensitive:
            cmd.append("--ignore-case")
        if multiline:
            cmd.extend(["--multiline", "--multiline-dotall"])

        # Context lines (content mode only)
        if output_mode == "content":
            if context_lines > 0:
                cmd.extend(["--context", str(context_lines)])
            else:
                if before_context > 0:
                    cmd.extend(["--before-context", str(before_context)])
                if after_context > 0:
                    cmd.extend(["--after-context", str(after_context)])

        # File filter
        if include:
            cmd.extend(["--glob", include])

        # Result limit: for content mode, limit matches per file
        if output_mode == "content":
            cmd.extend(["--max-count", str(max_results)])

        # Exclude common irrelevant directories (rg already ignores .git via .gitignore)
        for d in ["__pycache__", "node_modules", ".venv", "venv", ".idea", ".pytest_cache"]:
            cmd.extend(["--glob", f"!{d}/"])

        # Pattern and path
        cmd.append("--")
        cmd.append(pattern)
        cmd.append(str(base_path))

        # Execute asynchronously
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            return "Error: grep timed out after 30 seconds"
        except FileNotFoundError:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._grep_fallback, pattern, path, include, output_mode,
                max_results, fixed_strings, case_insensitive,
            )

        # rg exit codes: 0=matches found, 1=no matches, 2=error
        if proc.returncode == 2:
            err = stderr.decode("utf-8", errors="replace").strip()
            return f"Error: {err}"

        output = stdout.decode("utf-8", errors="replace").strip()
        if not output:
            return f"No matches found for '{pattern}'"

        # Truncate result lines for files_with_matches / count
        if output_mode in ("files_with_matches", "count"):
            lines = output.split("\n")
            if len(lines) > max_results:
                output = "\n".join(lines[:max_results])
                output += f"\n... ({len(lines) - max_results} more results truncated)"

        result = truncate_if_too_long(output)
        logger.debug(f"Grep(rg) for '{pattern}': result length {len(result)} chars")
        return result

    def _grep_fallback(
            self,
            pattern: str,
            path: str,
            include: Optional[str],
            output_mode: str,
            max_results: int,
            fixed_strings: bool,
            case_insensitive: bool = False,
    ) -> str:
        """Fallback grep using pure Python when ripgrep is not available."""
        try:
            base_path = self._resolve_path(path)

            # Compile regex
            regex_pattern = None
            if not fixed_strings:
                try:
                    flags = re.IGNORECASE if case_insensitive else 0
                    regex_pattern = re.compile(pattern, flags)
                except re.error as e:
                    return f"Error: Invalid regex pattern '{pattern}': {e}"

            # Determine files to search
            if include:
                files = list(base_path.glob(f"**/{include}"))
            else:
                files = list(base_path.glob("**/*"))

            # Exclude directories and ignored paths
            ignore_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.idea', '.pytest_cache'}
            files = [f for f in files if f.is_file() and not set(f.parts).intersection(ignore_dirs)]

            results = []
            file_counts = {}

            match_pattern = pattern.lower() if (case_insensitive and fixed_strings) else pattern

            for fp in files:
                if len(results) >= max_results:
                    break

                try:
                    with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()

                    file_matches = []
                    for line_num, line in enumerate(lines, 1):
                        if fixed_strings:
                            check_line = line.lower() if case_insensitive else line
                            matched = match_pattern in check_line
                        else:
                            matched = regex_pattern.search(line)
                        if matched:
                            file_matches.append({
                                "line_num": line_num,
                                "content": line.strip()[:200],
                            })

                    if file_matches:
                        file_counts[str(fp)] = len(file_matches)
                        if output_mode == "content":
                            for match in file_matches[:max_results - len(results)]:
                                results.append(f"{fp}:{match['line_num']}: {match['content']}")
                        elif output_mode == "files_with_matches":
                            results.append(str(fp))
                except Exception:
                    continue

            # Format output
            if output_mode == "count":
                output_lines = [f"{p}:{c}" for p, c in file_counts.items()]
                result = "\n".join(output_lines) if output_lines else f"No matches found for '{pattern}'"
            elif output_mode == "files_with_matches":
                result = "\n".join(sorted(set(results))) if results else f"No matches found for '{pattern}'"
            else:  # content
                result = "\n".join(results) if results else f"No matches found for '{pattern}'"

            result = truncate_if_too_long(result)
            logger.debug(f"Grep(fallback) for '{pattern}': found {len(file_counts)} files, result length: {len(result)} chars")
            return result

        except Exception as e:
            logger.error(f"Error in grep fallback: {e}")
            return f"Error in grep: {e}"


class BuiltinExecuteTool(Tool):
    """
    Built-in command execution tool using async subprocess.
    Exposed as execute function for consistent naming in Agent.
    """

    def __init__(self, work_dir: Optional[str] = None, timeout: int = 120, max_timeout: int = 600,
                 max_output_length: int = 20000, sandbox_config=None):
        """
        Initialize BuiltinExecuteTool.

        Args:
            work_dir: Work directory for command execution
            timeout: Default command execution timeout in seconds
            max_timeout: Maximum allowed timeout in seconds
            max_output_length: Maximum length of output to return
            sandbox_config: SandboxConfig instance for command restriction enforcement
        """
        super().__init__(name="builtin_execute_tool")
        self._work_dir: Optional[Path] = Path(work_dir) if work_dir else None
        self._timeout = timeout
        self._max_timeout = max_timeout
        self._max_output_length = max_output_length
        self._sandbox_config = sandbox_config
        # Override timeout from sandbox config if set
        if sandbox_config and sandbox_config.enabled and sandbox_config.max_execution_time:
            self._timeout = sandbox_config.max_execution_time
        # Import ShellTool for its syntax-fix helpers
        from agentica.tools.shell_tool import ShellTool
        self._shell = ShellTool(work_dir=work_dir, timeout=timeout)
        self.register(self.execute, is_destructive=True)
        # Large bash outputs are persisted to disk (context gets preview only).
        # read_file keeps max_result_size_chars=None (never persist — avoids
        # reading its own persisted output file in a loop).
        self.functions["execute"].max_result_size_chars = 50_000
        # Execute tool manages its own timeout internally via asyncio.wait_for
        # on the subprocess. Skip the outer timeout wrapper in Model.run_function_calls.
        self.functions["execute"].manages_own_timeout = True

    async def execute(self, command: str, timeout: Optional[int] = None) -> str:
        """Executes a shell command, capturing both stdout and stderr.

        IMPORTANT — Use dedicated tools instead of bash equivalents:
        - File search:    Use glob tool    (NOT find, ls -R, or locate)
        - Content search: Use grep tool    (NOT grep, rg, or ag)
        - Read files:     Use read_file    (NOT cat, head, tail, less, or more)
        - Edit files:     Use edit_file    (NOT sed, awk, or perl -i)
        - Write files:    Use write_file   (NOT echo >, tee, or cat <<EOF)
        - List files:     Use ls tool      (NOT ls command in bash)

        The execute tool is for commands that have NO dedicated tool equivalent:
        git, python, pytest, pip, npm, make, docker, curl (POST), etc.

        Before executing:
        1. Verify target directory exists (use ls tool first if unsure)
        2. Always quote file paths with spaces: cd "/path with spaces/"
        3. Use absolute paths; avoid cd when possible

        Usage notes:
        - Commands timeout after 120 seconds by default
        - You may specify a custom timeout up to 600 seconds (10 min) for long-running commands
        - Use '&&' to chain dependent commands; use ';' for independent commands
        - DO NOT use newlines in commands (newlines ok inside quoted strings)
        - For Python code, the tool auto-converts `python3 -c "..."` to heredoc format
        - When issuing multiple independent commands, make multiple execute calls in parallel

        Git safety:
        - Prefer creating new commits over amending existing ones
        - Before destructive operations (git reset --hard, git push --force),
          consider safer alternatives and check with the user first
        - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign)
          unless the user explicitly requests it

        Good examples:
            - execute(command="python3 /path/to/script.py")
            - execute(command="pytest /path/to/tests/ -v --tb=short")
            - execute(command="git status")
            - execute(command="npm install && npm test", timeout=300)

        Bad examples (use dedicated tools instead):
            - execute(command="find . -name '*.py'")   → use glob(pattern="**/*.py")
            - execute(command="grep -r 'TODO' .")      → use grep(pattern="TODO")
            - execute(command="cat file.txt")           → use read_file(file_path="file.txt")
            - execute(command="sed -i 's/old/new/' f")  → use edit_file(...)

        Args:
            command: shell command to execute
            timeout: optional timeout in seconds (default 120, max 600)

        Returns:
            str: The output of the command (stdout + stderr) with exit code
        """
        # Apply timeout: use provided value, clamped to max
        effective_timeout = self._timeout
        if timeout is not None:
            effective_timeout = min(max(1, timeout), self._max_timeout)

        # Use ShellTool's syntax fixers (python -c → heredoc conversion, null/true/false fix)
        command = self._shell._convert_python_c_to_heredoc(command)

        # Sandbox: check blocked commands (best-effort, not a true security sandbox)
        if self._sandbox_config and self._sandbox_config.enabled:
            cmd_lower = command.lower().strip()
            for blocked in self._sandbox_config.blocked_commands:
                # Use regex word boundary to reduce false positives (e.g. "rm" in "format")
                # while still catching the actual dangerous patterns
                pattern = re.escape(blocked.lower())
                if re.search(r'(?:^|[\s;|&])' + pattern, cmd_lower):
                    logger.warning(f"Sandbox: blocked command: {command[:100]}")
                    return "Error: Sandbox blocked this command for security reasons."

            # Sandbox: check allowed_commands whitelist (prefix match on first token)
            # Only enforced when allowed_commands is explicitly set (non-None).
            allowed = self._sandbox_config.allowed_commands
            if allowed is not None:
                # Extract the first token (bare executable name, strip path prefix)
                first_token = cmd_lower.split()[0] if cmd_lower.split() else ""
                # Normalize: strip leading path (e.g. "/usr/bin/python3" → "python3")
                first_token_base = os.path.basename(first_token)
                if not any(
                    first_token_base == a.lower() or first_token_base.startswith(a.lower())
                    for a in allowed
                ):
                    logger.warning(
                        f"Sandbox: command '{first_token_base}' not in allowed_commands "
                        f"{allowed}: {command[:100]}"
                    )
                    return (
                        f"Error: Sandbox blocked this command — '{first_token_base}' is not "
                        f"in the allowed_commands list: {allowed}"
                    )

        # Safety: check dangerous command patterns (always active, independent of sandbox)
        from agentica.tools.safety import check_command_safety, redact_sensitive_text
        safety = check_command_safety(command)
        if safety["action"] == "block":
            logger.warning(f"Safety blocked command: {safety['reason']} — {command[:100]}")
            return f"Error: {safety['reason']}. Use a safer alternative."
        if safety["action"] == "warn":
            logger.info(f"Safety warning: {safety['reason']} — {command[:100]}")

        logger.debug(f"Executing command: {command}")
        cwd = str(self._work_dir) if self._work_dir else None
        proc = None

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            # Graceful termination: SIGTERM first, then SIGKILL
            if proc is not None:
                try:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        proc.kill()
                except ProcessLookupError:
                    pass
            logger.warning(f"Command timed out after {effective_timeout}s: {command}")
            return f"Error: Command timed out after {effective_timeout} seconds (timeout {effective_timeout}s)"
        except Exception as e:
            logger.warning(f"Failed to run shell command: {e}")
            return f"Error: {e}"

        # Combine stdout and stderr
        output_parts = []
        if stdout:
            output_parts.append(stdout.decode("utf-8", errors="replace"))
        if stderr:
            output_parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")

        output = "\n".join(output_parts).strip()

        # Truncate if too long
        if len(output) > self._max_output_length:
            output = output[:self._max_output_length] + "\n... (output truncated)"

        # Add exit code info
        if proc.returncode != 0:
            output = f"{output}\n\n[Exit code: {proc.returncode}]"

        logger.debug(f"Command exit code: {proc.returncode}")
        result = output if output else f"Command executed successfully (exit code: {proc.returncode})"
        return redact_sensitive_text(result)


class BuiltinWebSearchTool(Tool):
    """
    Built-in web search tool using Baidu search.
    Exposed as web_search function.
    """

    def __init__(self):
        """
        Initialize BuiltinWebSearchTool.
        """
        super().__init__(name="builtin_web_search_tool")
        from agentica.tools.baidu_search_tool import BaiduSearchTool
        self._search = BaiduSearchTool()
        self.register(self.web_search, concurrency_safe=True, is_read_only=True)

    async def web_search(self, queries: Union[str, List[str]], max_results: int = 5) -> str:
        """Search the web using Baidu for multiple queries and return results

        Args:
            queries (Union[str, List[str]]): Search keyword(s), can be a single string or a list of strings
            max_results (int, optional): Number of results to return for each query, default 5

        Returns:
            str: A JSON formatted string containing the search results.

        IMPORTANT: After using this tool:
        1. Read through the 'content' field of each result
        2. Extract relevant information that answers the user's question
        3. Synthesize this into a clear, natural language response
        4. Cite sources by mentioning the page titles or URLs
        5. NEVER show the raw JSON to the user - always provide a formatted response
        """

        try:
            result = await self._search.baidu_search(queries, max_results=max_results)
            logger.debug(f"Web search for '{queries}', result length: {len(result)} characters.")
            return result
        except Exception as e:
            logger.error(f"Web search error: {e}")
            return json.dumps({"error": f"Web search error: {e}", "queries": queries}, ensure_ascii=False)


class BuiltinFetchUrlTool(Tool):
    """
    Built-in URL fetching tool that wraps UrlCrawlerTool.
    Exposed as fetch_url function for consistent naming in Agent.
    """

    def __init__(self, max_content_length: int = 16000):
        """
        Initialize BuiltinFetchUrlTool.

        Args:
            max_content_length: Maximum length of returned content
        """
        super().__init__(name="builtin_fetch_url_tool")
        self.max_content_length = max_content_length
        # Import and initialize UrlCrawlerTool (uses default cache dir ~/.cache/agentica/web_cache/)
        from agentica.tools.url_crawler_tool import UrlCrawlerTool
        self._crawler = UrlCrawlerTool(max_content_length=max_content_length)
        self.register(self.fetch_url, concurrency_safe=True, is_read_only=True)

    async def fetch_url(self, url: str) -> str:
        """Fetch URL content and convert to clean text format.

        Args:
            url: URL to fetch, url starts with http:// or https://

        Returns:
            str, JSON formatted fetch result containing url, content, and save_path

        IMPORTANT: After using this tool:
        1. Read through the return content
        2. Extract relevant information that answers the user's question
        3. Synthesize this into a clear, natural language response
        4. NEVER show the raw JSON to the user unless specifically requested
        """
        result = await self._crawler.url_crawl(url)
        logger.debug(f"Fetched URL: {url}, result length: {len(result)} characters.")
        return result


class BuiltinTodoTool(Tool):
    """
    Built-in task management tool providing write_todos function.
    Used for tracking progress of complex tasks.
    Todos are stored on the Agent instance when available, making them
    visible to the agent via tool_result and periodic reminders.

    Design (mirrors CC TodoWriteTool):
    - write_todos tool_result contains full todo state + guidance text
    - All-completed auto-clear: when every item is completed, list is cleared
    - Verification nudge: when 3+ tasks all completed and none is a verification
      step, tool_result appends a reminder to verify before reporting done
    - No system prompt injection of todos (avoids token waste / cache busting)
    - Periodic reminder injected by Runner when LLM hasn't called write_todos
      for N turns (see Runner._inject_todo_reminder)
    """
    # System prompt for todo tool usage guidance
    WRITE_TODOS_SYSTEM_PROMPT = dedent("""## `write_todos`

    Use this tool for complex objectives to track each necessary step and give the user visibility into your progress.
    Writing todos takes time and tokens — only use it for complex many-step problems (3+ distinct steps), not for simple few-step requests.

    Critical rules:
    - Mark todos as completed as soon as each step is done. Do not batch completions.
    - The `write_todos` tool should NEVER be called multiple times in parallel.
    - Revise the todo list as you go — new information may reveal new tasks or make old tasks irrelevant.
    - The todo list will be shown in your tool results when you update it.
    - If you haven't updated it in a while, you may receive a reminder with the current state.""")

    # Verification nudge message (mirrors CC's mapToolResultToToolResultBlockParam)
    _VERIFICATION_NUDGE = (
        "\n\nNOTE: You just closed out 3+ tasks and none of them was a verification step. "
        "Before writing your final summary, verify your work by running tests, linting, "
        "or checking the actual output. Do not self-declare completion without evidence -- "
        "review the results to confirm correctness."
    )

    def __init__(self):
        """Initialize BuiltinTodoTool."""
        super().__init__(name="builtin_todo_tool")
        self._agent: Optional["Agent"] = None
        self._todos: List[Dict[str, Any]] = []  # fallback when no agent
        self.register(self.write_todos, is_destructive=True)

    def set_agent(self, agent: "Agent") -> None:
        """Receive agent reference so todos are stored on the agent."""
        self._agent = agent

    @property
    def todos(self) -> List[Dict[str, Any]]:
        if self._agent is not None:
            return self._agent.todos
        return self._todos

    @todos.setter
    def todos(self, value: List[Dict[str, Any]]) -> None:
        if self._agent is not None:
            self._agent.todos = value
        else:
            self._todos = value

    def get_system_prompt(self) -> Optional[str]:
        """Get the system prompt for todo tool usage guidance."""
        return self.WRITE_TODOS_SYSTEM_PROMPT

    @staticmethod
    def _needs_verification_nudge(todos: List[Dict[str, str]]) -> bool:
        """Check if verification nudge should be appended to tool_result.

        Fires when:
        1. All todos are completed (the list is being "closed out")
        2. There are 3+ todos (non-trivial task)
        3. None of the todos mentions verification/verify/test/check/lint
           (the agent skipped verification steps)

        This mirrors CC's structural nudge in TodoWriteTool.call() and
        TaskUpdateTool.call() -- it catches the exact moment the agent
        declares "everything done" without having verified anything.
        """
        if len(todos) < 3:
            return False
        if not all(t.get("status") == "completed" for t in todos):
            return False
        # Check if any todo content mentions verification-related keywords
        verification_pattern = re.compile(r'verif|test|lint|check|review|validate', re.IGNORECASE)
        if any(verification_pattern.search(t.get("content", "")) for t in todos):
            return False
        return True

    def write_todos(self, todos: Optional[List[Dict[str, str]]] = None) -> str:
        """Create and manage a structured task list.

        Use this tool to create and manage a structured task list for your current work session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.

        Only use this tool if you think it will be helpful in staying organized. If the user's request is trivial and takes less than 3 steps, it is better to NOT use this tool and just do the task directly.

        ## When to Use This Tool
        Use this tool in these scenarios:
        1. Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
        2. Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
        3. User explicitly requests todo list - When the user directly asks you to use the todo list
        4. User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
        5. The plan may need future revisions or updates based on results from the first few steps

        ## How to Use This Tool
        1. When you start working on a task - Mark it as in_progress BEFORE beginning work.
        2. After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation.
        3. You can also update future tasks, such as deleting them if they are no longer necessary, or adding new tasks that are necessary. Don't change previously completed tasks.
        4. You can make several updates to the todo list at once. For example, when you complete a task, you can mark the next task you need to start as in_progress.

        ## When NOT to Use This Tool
        It is important to skip using this tool when:
        1. There is only a single, straightforward task
        2. The task is trivial and tracking it provides no benefit
        3. The task can be completed in less than 3 trivial steps
        4. The task is purely conversational or informational

        ## Task States and Management

        1. **Task States**: Use these states to track progress:
        - pending: Task not yet started
        - in_progress: Currently working on (you can have multiple tasks in_progress at a time if they are not related to each other and can be run in parallel)
        - completed: Task finished successfully

        2. **Task Management**:
        - Update task status in real-time as you work
        - Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
        - Complete current tasks before starting new ones
        - Remove tasks that are no longer relevant from the list entirely
        - IMPORTANT: When you write this todo list, you should mark your first task (or tasks) as in_progress immediately!.
        - IMPORTANT: Unless all tasks are completed, you should always have at least one task in_progress to show the user that you are working on something.

        3. **Task Completion Requirements**:
        - ONLY mark a task as completed when you have FULLY accomplished it
        - If you encounter errors, blockers, or cannot finish, keep the task as in_progress
        - When blocked, create a new task describing what needs to be resolved
        - Never mark a task as completed if:
            - There are unresolved issues or errors
            - Work is partial or incomplete
            - You encountered blockers that prevent completion
            - You couldn't find necessary resources or dependencies
            - Quality standards haven't been met

        4. **Task Breakdown**:
        - Create specific, actionable items
        - Break complex tasks into smaller, manageable steps
        - Use clear, descriptive task names

        Being proactive with task management demonstrates attentiveness and ensures you complete all requirements successfully
        Remember: If you only need to make a few tool calls to complete a task, and it is clear what you need to do, it is better to just do the task directly and NOT call this tool at all.

        Each task item should contain:
        - content: Task description
        - status: Task status ("pending", "in_progress", "completed")

        Args:
            todos: Task list, each task is a dict with content and status. Required.
            Example: [{"content": "Write a report", "status": "pending"}, {"content": "Review report", "status": "pending"}]

        Returns:
            Updated task list with guidance message
        """
        try:
            # Validate todos parameter
            if todos is None:
                return "Error: 'todos' parameter is required. Please provide a list of tasks with 'content' and 'status' fields."
            if len(todos) == 0:
                return "Error: 'todos' list cannot be empty. Please provide at least one task."
            valid_statuses = {"pending", "in_progress", "completed"}
            validated_todos = []

            for i, todo in enumerate(todos):
                if not isinstance(todo, dict):
                    return f"Error: Todo item {i} must be a dictionary"

                content = todo.get("content", "")
                status = todo.get("status", "pending")

                if not content:
                    return f"Error: Todo item {i} must have 'content' field"
                if status not in valid_statuses:
                    return f"Error: Invalid status '{status}' for todo item {i}. Must be one of: {valid_statuses}"

                validated_todos.append({
                    "id": str(i + 1),
                    "content": content,
                    "status": status,
                })

            # Check verification nudge BEFORE auto-clear (need original todos)
            nudge_needed = self._needs_verification_nudge(validated_todos)

            # Auto-clear: all completed -> clear list (mirrors CC's allDone logic)
            all_done = all(t["status"] == "completed" for t in validated_todos)
            if all_done:
                self.todos = []
            else:
                self.todos = validated_todos

            logger.debug(f"Updated todo list: {len(validated_todos)} items, all_done={all_done}")

            # Build tool_result message (mirrors CC's mapToolResultToToolResultBlockParam)
            result_message = (
                f"Todos have been modified successfully ({len(validated_todos)} items). "
                "Ensure that you continue to use the todo list to track your progress. "
                "Please proceed with the current tasks if applicable."
            )
            if nudge_needed:
                result_message += self._VERIFICATION_NUDGE

            return json.dumps({
                "message": result_message,
                "todos": validated_todos,
                "all_completed": all_done,
                "verification_nudge": nudge_needed,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error updating todos: {e}")
            return f"Error updating todos: {e}"



class BuiltinMemoryTool(Tool):
    """
    Built-in memory tool for LLM to autonomously save and search long-term memories.

    When an Agent has a Workspace, this tool lets the LLM decide what information
    is worth remembering across sessions. Memories are stored as individual Markdown
    files under the workspace's memory directory and indexed in MEMORY.md.

    This mirrors Claude Code's design where the LLM uses Write/Edit tools to persist
    important information, but provides a dedicated interface for clarity.
    """

    # Uses shared MEMORY_TYPE_SPEC / MEMORY_EXCLUSION_SPEC from hooks.py
    # to keep type definitions in sync with MemoryExtractHooks._EXTRACT_PROMPT.
    MEMORY_SYSTEM_PROMPT: str = ""  # Built in __init__ from shared constants

    def __init__(self):
        super().__init__(name="builtin_memory_tool")
        self._workspace = None
        self._sync_memories_to_global_agent_md = False

        # Build system prompt from shared constants (avoids duplicating type defs)
        from agentica.hooks import MEMORY_TYPE_SPEC, MEMORY_EXCLUSION_SPEC
        self.MEMORY_SYSTEM_PROMPT = dedent("""\
        ## Long-term Memory

        You have access to `save_memory` and `search_memory` tools for persistent memory across sessions.

        Memories capture context NOT derivable from the current project state.
        Code patterns, architecture, git history, and file structure are derivable
        (via grep/git/AGENTS.md) and must NOT be saved as memories.

        If the user explicitly asks you to remember something, save it immediately
        as whichever type fits best. If they ask you to forget, tell them to delete
        the relevant memory file.

        ### Memory types

        """) + MEMORY_TYPE_SPEC + dedent("""
        ### How to save
        Call `save_memory` with:
        - `title`: short, searchable name (e.g. "user_role", "prefer_pytest")
        - `content`: what to remember and how to apply it (include Why + How to apply for feedback type)
        - `memory_type`: one of "user", "feedback", "project", "reference"

        ### What NOT to save

        """) + MEMORY_EXCLUSION_SPEC + (
            "\n- Duplicate of existing memory (search first before saving)."
        )

        self.register(self.save_memory, is_destructive=True)
        self.register(self.search_memory, concurrency_safe=True, is_read_only=True)

    def set_workspace(self, workspace) -> None:
        """Set the workspace reference for memory persistence."""
        self._workspace = workspace

    def set_sync_global_agent_md(self, enabled: bool) -> None:
        """Enable syncing user/feedback memories into ~/.agentica/AGENTS.md."""
        self._sync_memories_to_global_agent_md = enabled

    def get_system_prompt(self) -> Optional[str]:
        return self.MEMORY_SYSTEM_PROMPT

    async def save_memory(
        self,
        title: str,
        content: str,
        memory_type: str = "project",
    ) -> str:
        """Save important information to long-term memory for future sessions.

        Use this tool when you detect information worth remembering across sessions:
        user preferences, corrections, project context, or external references.

        Before saving, consider: is this already in an existing memory? Use search_memory to check.

        Args:
            title: Short, searchable name for this memory (e.g. "user_role", "prefer_pytest", "deploy_target")
            content: What to remember and how to apply it. Be specific and actionable.
            memory_type: Category — "user" (preferences/role), "feedback" (corrections),
                        "project" (non-code context), or "reference" (external pointers)

        Returns:
            Confirmation message with the saved memory file path
        """
        if self._workspace is None:
            return "Error: No workspace configured. Memory cannot be saved."

        valid_types = {"user", "feedback", "project", "reference"}
        if memory_type not in valid_types:
            return f"Error: Invalid memory_type '{memory_type}'. Must be one of: {valid_types}"

        if not title.strip():
            return "Error: title cannot be empty."
        if not content.strip():
            return "Error: content cannot be empty."

        filepath = await self._workspace.write_memory_entry(
            title=title.strip(),
            content=content.strip(),
            memory_type=memory_type,
            description=title.strip(),
            sync_to_global_agent_md=(
                self._sync_memories_to_global_agent_md and memory_type in {"user", "feedback"}
            ),
        )

        logger.debug(f"Memory saved: {title} -> {filepath}")
        return f"Memory saved: '{title}' (type: {memory_type}) -> {filepath}"

    def search_memory(self, query: str, limit: int = 5) -> str:
        """Search existing long-term memories by keyword.

        Use this before saving a new memory to avoid duplicates,
        or to recall previously saved information.

        Args:
            query: Search keywords (e.g. "user preferences", "deploy", "testing framework")
            limit: Maximum number of results to return (default: 5)

        Returns:
            JSON formatted search results with matching memories
        """
        if self._workspace is None:
            return "Error: No workspace configured."

        results = self._workspace.search_memory(query=query, limit=limit)

        if not results:
            return f"No memories found matching '{query}'"

        return json.dumps(results, ensure_ascii=False, indent=2)


def get_builtin_tools(
        work_dir: Optional[str] = None,
        include_file_tools: bool = True,
        include_execute: bool = True,
        include_web_search: bool = True,
        include_fetch_url: bool = True,
        include_todos: bool = True,
        include_task: bool = True,
        include_skills: bool = False,
        include_user_input: bool = False,
        task_model: Optional["Model"] = None,
        task_tools: Optional[List[Any]] = None,
        custom_skill_dirs: Optional[List[str]] = None,
        user_input_callback=None,
        sandbox_config=None,
    ) -> List[Tool]:
    """
    Get the list of built-in tools for Agent.

    Args:
        work_dir: Work directory for file operations
        include_file_tools: Whether to include file tools (ls, read_file, write_file, edit_file, glob, grep)
        include_execute: Whether to include code execution tool
        include_web_search: Whether to include web search tool
        include_fetch_url: Whether to include URL fetching tool
        include_todos: Whether to include task management tools
        include_task: Whether to include subagent task tool
        include_skills: Whether to include skill tool for executing skills (default: False)
        include_user_input: Whether to include user input tool for human-in-the-loop (default: False)
        task_model: Model for subagent tasks (optional, will use parent agent's model if not set)
        task_tools: Tools for subagent tasks (optional)
        custom_skill_dirs: Custom skill directories to load (optional)
        user_input_callback: Custom callback for user input tool (optional)
        sandbox_config: SandboxConfig instance for security isolation (optional)

    Returns:
        List of tools
    """
    tools = []

    if include_file_tools:
        tools.append(BuiltinFileTool(work_dir=work_dir, sandbox_config=sandbox_config))

    if include_execute:
        tools.append(BuiltinExecuteTool(work_dir=work_dir, sandbox_config=sandbox_config))

    if include_web_search:
        tools.append(BuiltinWebSearchTool())

    if include_fetch_url:
        tools.append(BuiltinFetchUrlTool())

    if include_todos:
        tools.append(BuiltinTodoTool())

    if include_task:
        tools.append(BuiltinTaskTool(model=task_model, tools=task_tools))

    if include_skills:
        from agentica.tools.skill_tool import SkillTool
        tools.append(SkillTool(custom_skill_dirs=custom_skill_dirs, auto_load=True))

    if include_user_input:
        from agentica.tools.user_input_tool import UserInputTool
        tools.append(UserInputTool(input_callback=user_input_callback))

    return tools


if __name__ == '__main__':
    # Test file tool
    file_tool = BuiltinFileTool()
    print("=== ls test ===")
    print(file_tool.ls("."))

    print("\n=== glob test ===")
    print(file_tool.glob("*.py", "."))

    # Test search tool
    search_tool = BuiltinWebSearchTool()
    print("\n=== web_search test ===")
    print(search_tool.web_search("Python programming", max_results=2))

    # Test todo tool
    todo_tool = BuiltinTodoTool()
    print("\n=== write_todos test ===")
    print(todo_tool.write_todos([
        {"content": "Task 1", "status": "in_progress"},
        {"content": "Task 2", "status": "pending"},
    ]))
