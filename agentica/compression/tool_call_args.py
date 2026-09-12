# -*- coding: utf-8 -*-
"""Helpers for shrinking tool-call argument JSON without breaking validity."""

import json
import re
from typing import Any


# The marker replaces one whole string leaf. Anchored so it matches the value
# eviction wrote and nothing else: a command that merely *mentions* the marker
# (``grep -rn '<evicted-tool-arg chars=' .``) carries other text and must still
# be allowed to run.
_OMITTED_ARG_RE = re.compile(r"^<evicted-tool-arg chars=\d+>?$")


def omitted_tool_arg(n: int) -> str:
    """Placeholder for a string leaf dropped from context.

    Must not be a prefix of the original payload. ``head + "...[truncated]"``
    looks like real ``write_file`` / ``apply_patch`` content, and the model
    copies it into the next write.
    """
    return f"<evicted-tool-arg chars={n}>"


def is_omitted_tool_arg(value: Any) -> bool:
    """True when ``value`` is the whole eviction placeholder for one argument.

    An omitted argument holds no payload: the string is not the call the model
    meant, it is a note saying the payload is gone. Executing it runs a shell
    command, writes a file, or messages a peer with the marker as content, so
    callers must fail closed instead.

    The optional trailing ``>`` accepts the truncated form a model sometimes
    emits from memory; the anchors still reject a longer string that merely
    contains the marker.
    """
    return isinstance(value, str) and bool(_OMITTED_ARG_RE.match(value))


def omitted_tool_args(value: Any) -> list:
    """Every ``(key_path, marker)`` under ``value`` that is an omitted argument.

    Only string leaves count, at any nesting depth, because that is the unit
    eviction replaces. Returned so a caller can name the affected fields in the
    error it raises.
    """
    found: list = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, str):
            if _OMITTED_ARG_RE.match(node):
                found.append((path, node))
            return
        if isinstance(node, dict):
            for key, item in node.items():
                walk(item, f"{path}.{key}" if path else str(key))
            return
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    walk(value, "")
    return found


def shrink_tool_arg_leaves(value: Any, max_string_chars: int) -> Any:
    """Replace oversize string leaves with ``omitted_tool_arg``, keeping JSON shape."""
    if isinstance(value, str):
        if len(value) > max_string_chars:
            return omitted_tool_arg(len(value))
        return value
    if isinstance(value, dict):
        return {key: shrink_tool_arg_leaves(item, max_string_chars) for key, item in value.items()}
    if isinstance(value, list):
        return [shrink_tool_arg_leaves(item, max_string_chars) for item in value]
    return value


def shrink_tool_call_arguments_json(arguments: str, max_string_chars: int = 200) -> str:
    """Shrink long string leaves inside tool-call arguments while preserving JSON."""
    try:
        parsed = json.loads(arguments)
    except (TypeError, ValueError):
        return arguments

    shrunken = shrink_tool_arg_leaves(parsed, max_string_chars)
    return json.dumps(shrunken, ensure_ascii=False)
