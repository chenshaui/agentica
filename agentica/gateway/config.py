# -*- coding: utf-8 -*-
"""
@author: XuMing(xuming624@qq.com)
@description: Configuration management for the gateway service.

Gateway-specific settings only. Model/path/workspace config is in agentica/config.py.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv

load_dotenv()

from agentica.config import (
    AGENTICA_DATA_DIR,
    AGENTICA_WORKSPACE_DIR,
)


@dataclass
class Settings:
    """Gateway service configuration."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8789
    debug: bool = False
    gateway_token: Optional[str] = None

    # Default user ID (single-user scenario)
    default_user_id: str = "default"

    # Agent cache: max number of concurrent Agent instances kept in LRU cache
    agent_max_sessions: int = 50

    # File upload limits
    upload_max_size_mb: int = 50
    upload_allowed_extensions: str = (
        ".txt,.md,.py,.js,.ts,.jsx,.tsx,.json,.yaml,.yml,.toml,.csv,"
        ".pdf,.png,.jpg,.jpeg,.gif,.webp,.svg,.zip,.tar,.gz"
    )

    # Run history retention (days); runs older than this are pruned on startup
    job_runs_retention_days: int = 30

    # Feishu
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_allowed_users: List[str] = field(default_factory=list)
    feishu_allowed_groups: List[str] = field(default_factory=list)

    # Telegram
    telegram_bot_token: Optional[str] = None
    telegram_allowed_users: List[str] = field(default_factory=list)

    # Discord
    discord_bot_token: Optional[str] = None
    discord_allowed_users: List[str] = field(default_factory=list)
    discord_allowed_guilds: List[str] = field(default_factory=list)

    # Bridged from SDK config (read-only properties)
    @property
    def model_provider(self) -> str:
        return os.getenv("AGENTICA_MODEL_PROVIDER", "zhipuai")

    @property
    def model_name(self) -> str:
        return os.getenv("AGENTICA_MODEL_NAME", "glm-4.7-flash")

    @property
    def model_thinking(self) -> str:
        return os.getenv("AGENTICA_MODEL_THINKING", "")

    @property
    def workspace_path(self) -> Path:
        return Path(AGENTICA_WORKSPACE_DIR)

    @property
    def data_dir(self) -> Path:
        return Path(AGENTICA_DATA_DIR)

    @property
    def base_dir(self) -> Path:
        return Path(os.getenv("AGENTICA_BASE_DIR", str(Path.home())))

    @property
    def upload_allowed_ext_set(self) -> set[str]:
        """Return upload_allowed_extensions as a lowercase set for fast lookup."""
        return {
            e.strip().lower()
            for e in self.upload_allowed_extensions.split(",")
            if e.strip()
        }

    @classmethod
    def from_env(cls) -> "Settings":
        """Load configuration from environment variables."""
        allowed_users = os.getenv("FEISHU_ALLOWED_USERS", "")
        allowed_groups = os.getenv("FEISHU_ALLOWED_GROUPS", "")

        return cls(
            # Server
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8789")),
            debug=os.getenv("DEBUG", "").lower() in ("1", "true"),
            gateway_token=os.getenv("GATEWAY_TOKEN") or None,

            # Default user
            default_user_id=os.getenv("DEFAULT_USER_ID", "default"),

            # Agent cache
            agent_max_sessions=int(os.getenv("AGENT_MAX_SESSIONS", "50")),

            # Upload limits
            upload_max_size_mb=int(os.getenv("UPLOAD_MAX_SIZE_MB", "50")),
            upload_allowed_extensions=os.getenv(
                "UPLOAD_ALLOWED_EXTENSIONS",
                ".txt,.md,.py,.js,.ts,.jsx,.tsx,.json,.yaml,.yml,.toml,.csv,"
                ".pdf,.png,.jpg,.jpeg,.gif,.webp,.svg,.zip,.tar,.gz",
            ),

            # Run history retention
            job_runs_retention_days=int(os.getenv("JOB_RUNS_RETENTION_DAYS", "30")),

            # Feishu
            feishu_app_id=os.getenv("FEISHU_APP_ID"),
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET"),
            feishu_allowed_users=[
                u.strip() for u in allowed_users.split(",") if u.strip()
            ],
            feishu_allowed_groups=[
                g.strip() for g in allowed_groups.split(",") if g.strip()
            ],

            # Telegram
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_allowed_users=[
                u.strip() for u in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",") if u.strip()
            ],

            # Discord
            discord_bot_token=os.getenv("DISCORD_BOT_TOKEN"),
            discord_allowed_users=[
                u.strip() for u in os.getenv("DISCORD_ALLOWED_USERS", "").split(",") if u.strip()
            ],
            discord_allowed_guilds=[
                g.strip() for g in os.getenv("DISCORD_ALLOWED_GUILDS", "").split(",") if g.strip()
            ],
        )


# Global settings instance
settings = Settings.from_env()
