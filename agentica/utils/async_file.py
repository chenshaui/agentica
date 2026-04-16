# -*- coding: utf-8 -*-
"""
Shared async file I/O and frontmatter parsing utilities.

Used by workspace.py and agentica/experience/compiled_store.py.
Single source of truth — do not duplicate these functions.
"""
import asyncio
import functools
import re
from pathlib import Path
from typing import Optional


async def async_read_text(path: Path, encoding: str = "utf-8") -> str:
    """Read text file in executor to avoid blocking event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(path.read_text, encoding=encoding))


async def async_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write text file in executor to avoid blocking event loop."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, functools.partial(path.write_text, content, encoding=encoding))


def extract_frontmatter_value(content: str, key: str) -> Optional[str]:
    """Extract a value from YAML frontmatter."""
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else None


def extract_frontmatter_int(content: str, key: str, default: int = 0) -> int:
    """Extract an integer value from YAML frontmatter."""
    m = re.search(rf"^{re.escape(key)}:\s*(\d+)", content, re.MULTILINE)
    return int(m.group(1)) if m else default


def strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter block (---...---) from content."""
    stripped = re.sub(r"^---[\s\S]*?---\s*", "", content, flags=re.MULTILINE).strip()
    return stripped
