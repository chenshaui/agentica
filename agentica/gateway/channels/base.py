"""渠道抽象基类"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Callable, Any, List
from enum import Enum


class ChannelType(Enum):
    """渠道类型"""
    WEB = "web"
    FEISHU = "feishu"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WECHAT = "wechat"
    DINGTALK = "dingtalk"


@dataclass
class Message:
    """统一消息格式"""
    channel: ChannelType
    channel_id: str          # 渠道内会话ID (chat_id)
    sender_id: str           # 发送者ID
    sender_name: str         # 发送者名称
    content: str             # 消息内容
    message_id: str          # 消息ID
    timestamp: float = 0
    reply_to: Optional[str] = None
    attachments: List[Any] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class Channel(ABC):
    """渠道抽象基类

    所有渠道实现都需要继承此类并实现抽象方法
    """

    def __init__(self):
        self._message_handler: Optional[Callable[[Message], Any]] = None
        self._connected = False

    @property
    @abstractmethod
    def channel_type(self) -> ChannelType:
        """渠道类型"""
        pass

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected

    @abstractmethod
    async def connect(self) -> bool:
        """建立连接

        Returns:
            是否连接成功
        """
        pass

    @abstractmethod
    async def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    async def send(self, channel_id: str, content: str, **kwargs) -> bool:
        """发送消息

        Args:
            channel_id: 目标会话ID
            content: 消息内容
            **kwargs: 其他参数

        Returns:
            是否发送成功
        """
        pass

    def set_handler(self, handler: Callable[[Message], Any]):
        """设置消息处理器

        Args:
            handler: 消息处理回调函数
        """
        self._message_handler = handler

    async def _emit_message(self, message: Message):
        """触发消息事件

        Args:
            message: 收到的消息
        """
        if self._message_handler:
            await self._message_handler(message)
