# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: 
Dependency injection helpers for FastAPI routes.

All routes access shared service instances through these Depends() functions.
Global instances are set during app lifespan startup.
"""
from typing import Optional
from fastapi import HTTPException

from .services.agent_service import AgentService
from .services.channel_manager import ChannelManager
from .services.router import MessageRouter

# Global service instances — set in main.py lifespan
agent_service: Optional[AgentService] = None
channel_manager: Optional[ChannelManager] = None
message_router: Optional[MessageRouter] = None


def get_agent_service() -> AgentService:
    if not agent_service:
        raise HTTPException(status_code=503, detail="Service not ready")
    return agent_service


def get_channel_manager() -> ChannelManager:
    if not channel_manager:
        raise HTTPException(status_code=503, detail="Service not ready")
    return channel_manager
