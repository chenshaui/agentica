# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Shared interrupt signaling for all tools.

Provides a global threading.Event that any tool can check to determine
if the user has requested an interrupt. Long-running tools poll this
during execution for cooperative cancellation.

Usage in tools:
    from agentica.tools.interrupt import is_interrupted
    if is_interrupted():
        return "Operation interrupted by user."
"""
import threading

_interrupt_event = threading.Event()


def set_interrupt(active: bool) -> None:
    """Signal or clear the global interrupt.

    Called by the agent/CLI to request cancellation of long-running tools.
    """
    if active:
        _interrupt_event.set()
    else:
        _interrupt_event.clear()


def is_interrupted() -> bool:
    """Check if an interrupt has been requested. Safe to call from any thread."""
    return _interrupt_event.is_set()
