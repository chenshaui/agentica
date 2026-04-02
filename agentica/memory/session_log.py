# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Append-only JSONL session log with compact boundary support.

Mirrors CC's sessionStorage.ts: every message is appended as a JSON line,
compact boundaries mark resume points, and load() replays from the last boundary.

Entry types:
- message:          A conversation message (user/assistant/system/tool)
- tool_result:      A tool call result with metadata
- compact_boundary: A compaction summary — resume starts from here

Usage:
    log = SessionLog("session-123")
    log.append_message("user", "hello")
    log.append_message("assistant", "hi there")
    log.append_compact_boundary("User greeted assistant.")

    # Later, or after process restart:
    messages = log.load()  # resumes from last compact_boundary
"""
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentica.utils.log import logger


class SessionLog:
    """Append-only JSONL session log. Enables session resume and audit.

    Each entry is a single JSON line, appended atomically.
    Compact boundaries act as checkpoints — resume replays only from the
    last boundary, avoiding re-processing the entire conversation history.
    """

    def __init__(self, session_id: str, base_dir: str = ".sessions"):
        self.session_id = session_id
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / f"{session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Append operations (write-only, atomic per line)
    # ------------------------------------------------------------------

    def append_message(self, role: str, content: str, **meta: Any) -> None:
        """Append a conversation message entry."""
        self._append({
            "type": "message",
            "role": role,
            "content": content,
            "ts": time.time(),
            **meta,
        })

    def append_tool_result(
        self,
        tool_name: str,
        tool_call_id: str,
        content: str,
        is_error: bool = False,
        **meta: Any,
    ) -> None:
        """Append a tool result entry."""
        self._append({
            "type": "tool_result",
            "role": "tool",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "content": content,
            "is_error": is_error,
            "ts": time.time(),
            **meta,
        })

    def append_compact_boundary(self, summary: str) -> None:
        """Mark a compaction boundary.

        On resume, all entries before the last boundary are discarded.
        The summary becomes the starting context.
        """
        self._append({
            "type": "compact_boundary",
            "summary": summary,
            "ts": time.time(),
        })

    # ------------------------------------------------------------------
    # Load / Resume
    # ------------------------------------------------------------------

    def load(self) -> List[Dict[str, str]]:
        """Replay JSONL log for session resume.

        Strategy (mirrors CC's sessionStorage):
        - Scan all entries
        - On compact_boundary: reset message list, use summary as starting context
        - On message/tool_result after last boundary: append to message list

        Returns:
            List of message dicts with 'role' and 'content' keys,
            ready to inject into WorkingMemory.
        """
        if not self.path.exists():
            return []

        messages: List[Dict[str, str]] = []
        last_boundary_summary: Optional[str] = None
        last_boundary_line: int = -1

        # First pass: find last compact_boundary
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if entry.get("type") == "compact_boundary":
                    last_boundary_summary = entry.get("summary", "")
                    last_boundary_line = i
            except json.JSONDecodeError:
                continue

        # Second pass: build messages from last boundary onward
        start_from = last_boundary_line + 1 if last_boundary_line >= 0 else 0

        if last_boundary_summary is not None:
            messages.append({
                "role": "user",
                "content": f"[Resumed session — previous context summary]\n\n{last_boundary_summary}",
            })
            messages.append({
                "role": "assistant",
                "content": "Understood. I have the conversation context. Continuing.",
            })

        for line in lines[start_from:]:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type")
            if entry_type == "message":
                messages.append({
                    "role": entry["role"],
                    "content": entry.get("content", ""),
                })
            elif entry_type == "tool_result":
                messages.append({
                    "role": "tool",
                    "content": entry.get("content", ""),
                })

        logger.debug(
            f"SessionLog.load({self.session_id}): "
            f"{len(lines)} entries, boundary at line {last_boundary_line}, "
            f"resumed with {len(messages)} messages"
        )
        return messages

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        """Check if the session log file exists."""
        return self.path.exists()

    def entry_count(self) -> int:
        """Count total entries in the log."""
        if not self.path.exists():
            return 0
        return sum(1 for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip())

    def _append(self, entry: Dict) -> None:
        """Append a single JSON entry as a new line (atomic write)."""
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
