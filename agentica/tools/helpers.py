# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Helper functions for tool response serialization.

Eliminates boilerplate json.dumps across all tool implementations.
"""
import json
from typing import Any


def tool_error(message: str, **extra) -> str:
    """Return a JSON error string for tool handlers.

    Examples:
        >>> tool_error("file not found")
        '{"error": "file not found"}'
        >>> tool_error("bad input", success=False)
        '{"error": "bad input", "success": false}'
    """
    result: dict[str, Any] = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data: dict | None = None, **kwargs) -> str:
    """Return a JSON result string for tool handlers.

    Accepts a dict positional arg OR keyword arguments (not both):

    Examples:
        >>> tool_result(success=True, count=42)
        '{"success": true, "count": 42}'
        >>> tool_result({"key": "value"})
        '{"key": "value"}'
    """
    if data is not None:
        return json.dumps(data, ensure_ascii=False, indent=2)
    return json.dumps(kwargs, ensure_ascii=False, indent=2)
