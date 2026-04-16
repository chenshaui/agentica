# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: 
Append-only raw experience event storage.

Writes raw events to events.jsonl as the source of truth.
Experience cards are compiled from these events by ExperienceCompiler.
"""
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List


class ExperienceEventStore:
    """Append-only JSONL event store for raw experience events.

    Each event is a single JSON line in events.jsonl under the experience dir.
    Events are never mutated or deleted -- they are the immutable audit trail.

    Usage::

        store = ExperienceEventStore(exp_dir=Path("/workspace/users/default/experiences"))
        await store.append({"event_type": "tool_error", "tool": "execute", "error": "..."})
        events = await store.read_all()
    """

    _EVENTS_FILE = "events.jsonl"

    def __init__(self, exp_dir: Path) -> None:
        self._exp_dir = exp_dir

    @property
    def events_path(self) -> Path:
        """Path to the events.jsonl file."""
        return self._exp_dir / self._EVENTS_FILE

    async def append(self, event: Dict[str, Any]) -> str:
        """Append a raw event to events.jsonl.

        Args:
            event: Raw event dict. Must include 'event_type' key.

        Returns:
            Absolute path to the events.jsonl file.
        """
        self._exp_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"

        def _write():
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write(line)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write)
        return str(self.events_path)

    async def read_all(self) -> List[Dict[str, Any]]:
        """Read all events from events.jsonl.

        Returns:
            List of event dicts, in chronological order.
        """
        if not self.events_path.exists():
            return []

        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(
            None,
            lambda: self.events_path.read_text(encoding="utf-8"),
        )

        events = []
        for line in text.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events
