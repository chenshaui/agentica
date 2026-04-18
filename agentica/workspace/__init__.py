# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Workspace: Persistent project context, long-term memory, skills, experience.

This is a default-installed core capability (no extras required).

Public API
----------
::

    from agentica.workspace import Workspace, WorkspaceConfig

    ws = Workspace("./my-project")
    ws.initialize()

Architecture
------------
v1.3.6 split ``workspace.py`` (1402 lines) into a package for incremental
modularization. Currently the entire implementation lives in ``base.py``;
future stages will extract:

- ``memory.py``      — long-term memory operations (write_memory_entry, get_relevant_memories)
- ``git_context.py`` — get_git_context()
- ``experience.py``  — experience-related methods
- ``index.py``       — MEMORY.md index parsing

Imports remain stable: ``from agentica.workspace import Workspace`` always works.
"""
from agentica.workspace.base import Workspace, WorkspaceConfig

# Re-export module-level constants for convenience (read-only public API).
# Note: tests that need to patch these must use `agentica.workspace.base.AGENTICA_HOME`
# (the actual binding inside base.py), not `agentica.workspace.AGENTICA_HOME`.
from agentica.workspace.base import (
    AGENTICA_HOME,
    AGENTICA_WORKSPACE_DIR,
    AGENTICA_MAX_MEMORY_CHARACTER_COUNT,
)

__all__ = [
    "Workspace",
    "WorkspaceConfig",
    "AGENTICA_HOME",
    "AGENTICA_WORKSPACE_DIR",
    "AGENTICA_MAX_MEMORY_CHARACTER_COUNT",
]
