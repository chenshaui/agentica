# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Provider-agnostic reasoning content extraction.

Extracts reasoning/thinking from LLM response messages regardless of provider format.
Handles OpenAI o-series (reasoning_content), Anthropic extended thinking (content blocks),
and generic fallback fields.
"""
from typing import Any, Optional


def extract_reasoning(message: Any) -> Optional[str]:
    """Extract reasoning/thinking content from any provider's response message.

    Supported formats:
    - OpenAI o-series: message.reasoning_content field
    - Anthropic extended thinking: message.content list with type="thinking" blocks
    - Generic: message.reasoning / message.thinking / message.thinking_content

    Args:
        message: Raw LLM response message object (provider-specific).

    Returns:
        Extracted reasoning text, or None if no reasoning found.
    """
    # OpenAI o-series: reasoning_content field
    if hasattr(message, "reasoning_content") and message.reasoning_content:
        return message.reasoning_content

    # Anthropic extended thinking: content block with type="thinking"
    if hasattr(message, "content") and isinstance(message.content, list):
        thinking_parts = []
        for block in message.content:
            if hasattr(block, "type") and block.type == "thinking":
                text = getattr(block, "thinking", None) or getattr(block, "text", None)
                if text:
                    thinking_parts.append(text)
        if thinking_parts:
            return "\n".join(thinking_parts)

    # Intentional getattr: provider-agnostic extraction must probe
    # undocumented attributes across diverse LLM SDK message types.
    for attr in ("reasoning", "thinking", "thinking_content"):
        val = getattr(message, attr, None)
        if val:
            return val

    return None
