# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: MCP: Model Context Protocol client/server.

Requires the [mcp] extras:
    pip install agentica[mcp]
"""
try:
    import mcp  # noqa: F401
except ImportError as e:
    raise ImportError(
        "agentica.mcp requires the [mcp] extras. Install with:\n\n"
        "    pip install agentica[mcp]\n"
    ) from e

from .server import (
    MCPServer,
    MCPServerSse,
    MCPServerSseParams,
    MCPServerStdio,
    MCPServerStdioParams,
    MCPServerStreamableHttp,
    MCPServerStreamableHttpParams,
)
from .client import MCPClient

__all__ = [
    "MCPServer",
    "MCPServerSse",
    "MCPServerSseParams",
    "MCPServerStdio",
    "MCPServerStdioParams",
    "MCPServerStreamableHttp",
    "MCPServerStreamableHttpParams",
    "MCPClient",
]
