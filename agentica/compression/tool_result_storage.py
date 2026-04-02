# -*- coding: utf-8 -*-
"""
@description: Tool result storage — persist large tool outputs to disk.

When a tool result exceeds a threshold, the full content is saved to disk
and the context message is replaced with a short preview + file path.
This prevents large bash outputs / web fetches from bloating the context.

Mirrors CC's toolResultStorage.ts pattern:
- Per-tool maxResultSizeChars threshold
- Full content persisted to `.tool_results/<session>/<tool_use_id>.txt`
- Context gets a preview (first PREVIEW_CHARS characters) + disk path

Usage (automatic — called from Model.run_function_calls):
    from agentica.compression.tool_result_storage import maybe_persist_result
    content = maybe_persist_result(
        tool_name="execute", tool_use_id="call_abc123",
        content=huge_bash_output, session_id="sess_xyz",
    )
"""
from pathlib import Path
from typing import Optional

from agentica.utils.log import logger

# Max chars to keep inline in the context (preview)
PREVIEW_CHARS = 2000

# Default max result size before persisting to disk.
# Individual tools can override via Function.max_result_size_chars.
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000

# Storage directory (relative to cwd)
TOOL_RESULTS_DIR = ".tool_results"


def maybe_persist_result(
    tool_name: str,
    tool_use_id: str,
    content: str,
    session_id: str = "default",
    max_result_size_chars: Optional[int] = DEFAULT_MAX_RESULT_SIZE_CHARS,
) -> str:
    """If content exceeds threshold, persist to disk and return preview.

    Args:
        tool_name:              Name of the tool that produced the result.
        tool_use_id:            Unique call ID (used as filename).
        content:                Full tool output string.
        session_id:             Session identifier for directory isolation.
        max_result_size_chars:  Threshold in chars. None = never persist.

    Returns:
        Original content (if under threshold) or preview + disk path.
    """
    if max_result_size_chars is None:
        return content
    if len(content) <= max_result_size_chars:
        return content

    # Persist full content to disk
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_use_id)
    dir_path = Path(TOOL_RESULTS_DIR) / session_id
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{safe_id}.txt"
    try:
        file_path.write_text(content, encoding="utf-8")
    except OSError as e:
        logger.warning(f"Failed to persist tool result to {file_path}: {e}")
        # Fallback: truncate in-place
        return content[:max_result_size_chars] + "\n... (output truncated)"

    # Build preview message for context
    preview = content[:PREVIEW_CHARS]
    has_more = len(content) > PREVIEW_CHARS
    size_kb = len(content.encode("utf-8", errors="ignore")) / 1024

    msg = (
        f"[Output too large ({size_kb:.1f} KB). "
        f"Full output saved to: {file_path}]\n\n"
        f"Preview (first {PREVIEW_CHARS} chars):\n"
        f"{preview}"
    )
    if has_more:
        msg += "\n..."

    logger.debug(
        f"Persisted {tool_name} result ({len(content):,} chars) to {file_path}"
    )
    return msg
