"""渠道管理器"""
from typing import Dict, Optional, Callable, Any, Union

from loguru import logger

from ..channels.base import Channel, ChannelType, Message


class ChannelManager:
    """渠道管理器

    管理所有渠道的生命周期和消息分发
    """

    def __init__(self):
        self.channels: Dict[ChannelType, Channel] = {}
        self._message_handler: Optional[Callable[[Message], Any]] = None

    def register(self, channel: Channel):
        """注册渠道

        Args:
            channel: 渠道实例
        """
        channel.set_handler(self._on_message)
        self.channels[channel.channel_type] = channel
        logger.info(f"Channel registered: {channel.channel_type.value}")

    def set_handler(self, handler: Callable[[Message], Any]):
        """设置统一消息处理器

        Args:
            handler: 消息处理回调
        """
        self._message_handler = handler

    async def _on_message(self, message: Message):
        """处理来自任意渠道的消息"""
        if self._message_handler:
            await self._message_handler(message)

    async def connect_all(self):
        """连接所有渠道"""
        for channel in self.channels.values():
            try:
                await channel.connect()
            except Exception as e:
                logger.error(f"Failed to connect {channel.channel_type.value}: {e}")

    async def disconnect_all(self):
        """断开所有渠道"""
        for channel in self.channels.values():
            try:
                await channel.disconnect()
            except Exception as e:
                logger.error(f"Failed to disconnect {channel.channel_type.value}: {e}")

    async def send(
        self,
        channel_type: Union[ChannelType, str],
        channel_id: str,
        content: str,
        **kwargs
    ) -> bool:
        """发送消息到指定渠道

        Args:
            channel_type: 渠道类型
            channel_id: 目标会话ID
            content: 消息内容
            **kwargs: 其他参数

        Returns:
            是否发送成功
        """
        # 支持字符串类型
        if isinstance(channel_type, str):
            try:
                channel_type = ChannelType(channel_type)
            except ValueError:
                logger.warning(f"Unknown channel type: {channel_type}")
                return False

        channel = self.channels.get(channel_type)
        if not channel:
            logger.warning(f"Channel not registered: {channel_type.value}")
            return False

        if not channel.is_connected:
            logger.warning(f"Channel not connected: {channel_type.value}")
            return False

        return await channel.send(channel_id, content, **kwargs)

    def get_status(self) -> Dict[str, dict]:
        """获取所有渠道状态

        Returns:
            渠道状态字典
        """
        return {
            ct.value: {
                "connected": ch.is_connected,
            }
            for ct, ch in self.channels.items()
        }

    def get_channel(self, channel_type: ChannelType) -> Optional[Channel]:
        """获取渠道实例

        Args:
            channel_type: 渠道类型

        Returns:
            渠道实例或 None
        """
        return self.channels.get(channel_type)

    def list_channels(self) -> list:
        """列出所有已注册渠道

        Returns:
            渠道类型列表
        """
        return [ct.value for ct in self.channels.keys()]
