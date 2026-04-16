# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: 
Pure/stateless experience compiler.

Transforms raw captured data (tool errors, user messages, success patterns)
into compiled experience cards. No I/O, no state — takes inputs, returns outputs.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class CompiledCard:
    """An experience card compiled from raw events.

    Attributes:
        title: Unique identifier for the experience (snake_case)
        content: Full experience text (what happened + lesson)
        experience_type: One of "tool_error", "correction", "success_pattern"
        tool_name: Tool that triggered this experience (empty if N/A)
    """
    title: str
    content: str
    experience_type: str
    tool_name: str = ""


class ExperienceCompiler:
    """Pure compiler: raw data -> compiled cards.

    No I/O, no mutable state, no model calls. All methods are static or
    class methods that take inputs and return outputs.

    Usage::

        cards = ExperienceCompiler.compile_tool_errors(errors)
        card = ExperienceCompiler.compile_success_pattern(successes)
        card = ExperienceCompiler.compile_correction(classification)
    """

    @staticmethod
    def compile_tool_errors(errors: List[Dict]) -> List[CompiledCard]:
        """Compile tool error dicts into experience cards.

        Each error produces one card. Dedup key is tool + error_type prefix,
        so different error types from the same tool remain separate.

        Args:
            errors: List of dicts with keys: tool, args, error, elapsed.

        Returns:
            List of CompiledCard, one per unique error.
        """
        cards = []
        for err in errors:
            tool = err.get("tool", "unknown")
            error_msg = err.get("error", "")
            error_type = error_msg.split(":")[0][:30] if error_msg else "unknown"
            title = f"{tool}_{error_type}"
            args_summary = str(err.get("args", {}))[:200]
            elapsed = err.get("elapsed", 0.0)

            content = (
                f"Tool `{tool}` failed.\n"
                f"Args: {args_summary}\n"
                f"Error: {error_msg}\n"
                f"Elapsed: {elapsed:.2f}s"
            )
            cards.append(CompiledCard(
                title=title,
                content=content,
                experience_type="tool_error",
                tool_name=tool,
            ))
        return cards

    @staticmethod
    def compile_success_pattern(successes: List[Dict]) -> Optional[CompiledCard]:
        """Compile a success pattern from tool success records.

        Only produces a card when 3+ tools all succeeded with no errors.

        Args:
            successes: List of dicts with keys: tool, elapsed.

        Returns:
            CompiledCard if >= 3 successes, None otherwise.
        """
        if len(successes) < 3:
            return None

        tool_names = [s.get("tool", "unknown") for s in successes]
        unique_tools = "_".join(sorted(set(tool_names)))[:40]
        title = f"success_{unique_tools}" if unique_tools else "success_unknown"
        content = (
            f"Successful tool sequence ({len(successes)} calls, all passed):\n"
            + "\n".join(f"- {t}" for t in tool_names)
        )
        return CompiledCard(
            title=title,
            content=content,
            experience_type="success_pattern",
        )

    @staticmethod
    def compile_correction(classification: Dict) -> Optional[CompiledCard]:
        """Compile a user correction from LLM classification output.

        Args:
            classification: Dict from LLM with keys: is_correction, confidence,
                title, rule, why, how_to_apply, category, scope, persist_target.

        Returns:
            CompiledCard if correction should be persisted as experience,
            None if not a correction or persist_target != "experience".
        """
        if not classification.get("is_correction", False):
            return None
        if not classification.get("should_persist", False):
            return None
        if classification.get("persist_target", "none") != "experience":
            return None

        title = classification.get("title", "user_correction")
        rule = classification.get("rule", "")
        why = classification.get("why", "")
        how_to_apply = classification.get("how_to_apply", "")
        category = classification.get("category", "")
        confidence = classification.get("confidence", 0.0)
        scope = classification.get("scope", "cross_session")

        content = (
            f"Rule: {rule}\n"
            f"Why: {why}\n"
            f"How to apply: {how_to_apply}\n"
            f"Category: {category}\n"
            f"Confidence: {confidence:.2f}\n"
            f"Scope: {scope}"
        )
        return CompiledCard(
            title=title,
            content=content,
            experience_type="correction",
        )

    @staticmethod
    def build_raw_events(
        errors: List[Dict],
        user_msg: Optional[str],
        previous_assistant: Optional[str],
        successes: List[Dict],
        capture_corrections: bool = True,
    ) -> List[Dict]:
        """Build raw event dicts from captured data.

        Pure function: does not write anything, just constructs the event list.

        Args:
            errors: Tool error dicts.
            user_msg: Current user message (or None).
            previous_assistant: Previous assistant text (or None).
            successes: Successful tool call dicts.
            capture_corrections: Whether to include user message events.

        Returns:
            List of event dicts ready for ExperienceEventStore.append().
        """
        events = []

        for err in errors:
            events.append({
                "event_type": "tool_error",
                "tool": err.get("tool", ""),
                "args": str(err.get("args", {}))[:200],
                "error": err.get("error", ""),
                "elapsed": err.get("elapsed", 0.0),
            })

        if capture_corrections and user_msg:
            events.append({
                "event_type": "user_message",
                "user_message": user_msg[:500],
                "previous_assistant": (previous_assistant or "")[:500],
            })

        if len(successes) >= 3 and not errors:
            events.append({
                "event_type": "success_pattern",
                "tool_count": len(successes),
                "tools": [s.get("tool", "") for s in successes],
            })

        return events
