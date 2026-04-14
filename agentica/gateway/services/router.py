"""消息路由器"""
from dataclasses import dataclass
from typing import List, Optional

from ..channels.base import Message, ChannelType


@dataclass
class RoutingRule:
    """路由规则

    Attributes:
        agent_id: 目标 Agent ID
        channel: 匹配的渠道类型
        channel_id: 匹配的会话ID
        sender_id: 匹配的发送者ID
        priority: 优先级（数值越大优先级越高）
    """
    agent_id: str
    channel: Optional[ChannelType] = None
    channel_id: Optional[str] = None
    sender_id: Optional[str] = None
    priority: int = 0


class MessageRouter:
    """消息路由器

    根据规则将消息路由到不同的 Agent
    """

    def __init__(self, default_agent: str = "main"):
        """
        Args:
            default_agent: 默认 Agent ID
        """
        self.default_agent = default_agent
        self.rules: List[RoutingRule] = []

    def add_rule(self, rule: RoutingRule):
        """添加路由规则

        Args:
            rule: 路由规则
        """
        self.rules.append(rule)
        # 按优先级排序（高优先级在前）
        self.rules.sort(key=lambda r: -r.priority)

    def remove_rule(self, agent_id: str, channel: Optional[ChannelType] = None):
        """移除路由规则

        Args:
            agent_id: Agent ID
            channel: 渠道类型（可选）
        """
        self.rules = [
            r for r in self.rules
            if not (r.agent_id == agent_id and (channel is None or r.channel == channel))
        ]

    def route(self, message: Message) -> str:
        """路由消息到 Agent

        匹配优先级：
        1. 精确匹配 sender_id
        2. 渠道+channel_id 匹配
        3. 渠道匹配
        4. 默认 Agent

        Args:
            message: 消息

        Returns:
            目标 Agent ID
        """
        for rule in self.rules:
            if self._match(message, rule):
                return rule.agent_id
        return self.default_agent

    def _match(self, message: Message, rule: RoutingRule) -> bool:
        """检查消息是否匹配规则

        Args:
            message: 消息
            rule: 路由规则

        Returns:
            是否匹配
        """
        # sender_id 必须精确匹配
        if rule.sender_id and message.sender_id != rule.sender_id:
            return False

        # channel 必须匹配
        if rule.channel and message.channel != rule.channel:
            return False

        # channel_id 必须匹配
        if rule.channel_id and message.channel_id != rule.channel_id:
            return False

        return True

    def get_session_id(self, message: Message, agent_id: str) -> str:
        """生成 session_id

        格式: agent:{agent_id}:{channel}:{channel_id}

        Args:
            message: 消息
            agent_id: Agent ID

        Returns:
            Session ID
        """
        return f"agent:{agent_id}:{message.channel.value}:{message.channel_id}"

    def list_rules(self) -> List[dict]:
        """列出所有规则

        Returns:
            规则列表
        """
        return [
            {
                "agent_id": r.agent_id,
                "channel": r.channel.value if r.channel else None,
                "channel_id": r.channel_id,
                "sender_id": r.sender_id,
                "priority": r.priority,
            }
            for r in self.rules
        ]
