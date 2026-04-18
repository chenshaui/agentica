# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: Unified database module for agentica

Database implementations are lazy-loaded to improve import speed.
Only base types are imported eagerly.
"""
import importlib
from typing import TYPE_CHECKING

# Base types (fast import, no heavy dependencies)
from agentica.db.base import (
    BaseDb,
    SessionRow,
    MemoryRow,
    MetricsRow,
    KnowledgeRow,
    filter_base64_images,
    filter_base64_media,
    clean_media_placeholders,
    BASE64_PLACEHOLDER,
)

# Lazy imports mapping (module_path, required_extras)
_LAZY_DB_IMPORTS = {
    # Zero external deps: InMemoryDb, JsonDb (stdlib only)
    "InMemoryDb": ("agentica.db.memory", None),
    "JsonDb": ("agentica.db.json", None),
    # sqlite: Python stdlib, but SqliteDb impl uses sqlalchemy
    "SqliteDb": ("agentica.db.sqlite", "sql"),
    "PostgresDb": ("agentica.db.postgres", "postgres"),
    "MysqlDb": ("agentica.db.mysql", "mysql"),
    "RedisDb": ("agentica.db.redis", "redis"),
}

_DB_CACHE = {}


def __getattr__(name: str):
    """Lazy import handler for database implementations with friendly ImportError."""
    if name in _LAZY_DB_IMPORTS:
        if name not in _DB_CACHE:
            module_path, extras = _LAZY_DB_IMPORTS[name]
            try:
                module = importlib.import_module(module_path)
            except ImportError as e:
                if extras is None:
                    raise
                raise ImportError(
                    f"agentica.db.{name} requires the [{extras}] extras. "
                    f"Install with:\n\n"
                    f"    pip install agentica[{extras}]\n"
                ) from e
            _DB_CACHE[name] = getattr(module, name)
        return _DB_CACHE[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """List all available names including lazy imports."""
    eager_names = [name for name in globals() if not name.startswith('_')]
    return sorted(set(eager_names) | set(_LAZY_DB_IMPORTS.keys()))


# Type hints for IDE support
if TYPE_CHECKING:
    from agentica.db.sqlite import SqliteDb
    from agentica.db.postgres import PostgresDb
    from agentica.db.memory import InMemoryDb
    from agentica.db.json import JsonDb
    from agentica.db.mysql import MysqlDb
    from agentica.db.redis import RedisDb


__all__ = [
    # Base types
    "BaseDb",
    "SessionRow",
    "MemoryRow",
    "MetricsRow",
    "KnowledgeRow",
    "filter_base64_images",
    "filter_base64_media",
    "clean_media_placeholders",
    "BASE64_PLACEHOLDER",
    # Lazy loaded implementations
    "SqliteDb",
    "PostgresDb",
    "InMemoryDb",
    "JsonDb",
    "MysqlDb",
    "RedisDb",
]
