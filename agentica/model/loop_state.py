# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Centralized state for the agentic tool loop.

Replaces scattered per-run counters (_loop_turn_count, _max_tokens_recovery_count,
_reactive_compact_done, _consecutive_all_error_turns) that were stored as mutable
attributes on the Model instance.

Created fresh at the start of each agentic_loop() / agentic_loop_stream() call.
"""
from dataclasses import dataclass, field


@dataclass
class LoopState:
    """Centralized state for one agentic loop invocation.

    All counters are per-loop (not per-run). A fresh LoopState is created
    each time agentic_loop() or agentic_loop_stream() is entered.
    """

    # Turn tracking (no hard limit -- mirrors original behaviour)
    turn_count: int = 0

    # Max-tokens recovery (finish_reason == "length")
    max_tokens_recovery_count: int = 0
    max_tokens_recovery_limit: int = 3

    # API retry ceiling
    max_api_retry: int = 3

    # Death spiral detection
    consecutive_all_error_turns: int = 0
    death_spiral_threshold: int = 5

    # Reactive compact (one-shot per loop invocation)
    reactive_compact_done: bool = False

    # Retryable error patterns
    RETRYABLE_SUBSTRINGS: tuple = field(
        default=(
            "rate_limit", "rate limit", "429", "503", "502",
            "connection", "timeout", "overloaded",
        ),
        repr=False,
    )
    PROMPT_TOO_LONG_HINTS: tuple = field(
        default=(
            "prompt_too_long", "context_length_exceeded",
            "maximum context", "too many tokens", "413",
        ),
        repr=False,
    )
