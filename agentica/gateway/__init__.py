# -*- coding: utf-8 -*-
"""
Agentica Gateway - FastAPI server + IM bots + cron scheduler.

Requires the [gateway] extras:
    pip install agentica[gateway]            # FastAPI + IM + cron
    pip install agentica[telegram]           # + Telegram bot
    pip install agentica[discord]            # + Discord bot
    pip install agentica[slack]              # + Slack bot
"""
try:
    import fastapi  # noqa: F401
except ImportError as e:
    raise ImportError(
        "agentica.gateway requires the [gateway] extras. Install with:\n\n"
        "    pip install agentica[gateway]            # FastAPI + IM + cron\n"
        "    pip install agentica[telegram]           # + Telegram bot\n"
        "    pip install agentica[discord]            # + Discord bot\n"
    ) from e

from agentica.version import __version__
