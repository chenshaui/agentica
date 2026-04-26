# -*- coding: utf-8 -*-
"""Helpers for shrinking tool-call argument JSON without breaking validity."""

import json
from typing import Any


def _shrink_string_leaves(value: Any, max_string_chars: int) -> Any:
    if isinstance(value, str):
        if len(value) > max_string_chars:
            return value[:max_string_chars] + "...[truncated]"
        return value
    if isinstance(value, dict):
        return {key: _shrink_string_leaves(item, max_string_chars) for key, item in value.items()}
    if isinstance(value, list):
        return [_shrink_string_leaves(item, max_string_chars) for item in value]
    return value


def shrink_tool_call_arguments_json(arguments: str, max_string_chars: int = 200) -> str:
    """Shrink long string leaves inside tool-call arguments while preserving JSON."""
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError):
        return arguments

    shrunken = _shrink_string_leaves(parsed, max_string_chars)
    return json.dumps(shrunken, ensure_ascii=False)
