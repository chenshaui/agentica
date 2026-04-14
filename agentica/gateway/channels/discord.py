"""Discord 渠道实现"""
import asyncio
from typing import Optional, List

from .base import Channel, ChannelType, Message
from ..config import settings

# 延迟导入 Discord SDK
discord = None


def _ensure_discord_sdk():
    """确保 Discord SDK 已导入"""
    global discord
    if discord is None:
        try:
            import discord as _discord
            discord = _discord
        except ImportError:
            raise ImportError(
                "Discord SDK 未安装，请运行: pip install discord.py"
            )


class DiscordChannel(Channel):
    """Discord 渠道

    使用 discord.py 库实现
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        allowed_users: Optional[List[str]] = None,
        allowed_guilds: Optional[List[str]] = None,
    ):
        super().__init__()
        self.bot_token = bot_token or settings.discord_bot_token
        self.allowed_users = allowed_users or []
        self.allowed_guilds = allowed_guilds or []
        self._client = None
        self._ready_event = asyncio.Event()

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.DISCORD

    async def connect(self) -> bool:
        """建立连接"""
        if not self.bot_token:
            print("[Discord] Missing bot token, skipped")
            return False

        try:
            _ensure_discord_sdk()

            # 配置 intents
            intents = discord.Intents.default()
            intents.message_content = True
            intents.guilds = True

            # 创建 Client
            self._client = discord.Client(intents=intents)

            # 注册事件处理器
            @self._client.event
            async def on_ready():
                print(f"[Discord] Logged in as {self._client.user}")
                self._ready_event.set()

            @self._client.event
            async def on_message(message):
                await self._on_message(message)

            # 在后台启动
            asyncio.create_task(self._start_client())

            # 等待 ready
            try:
                await asyncio.wait_for(self._ready_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                print("[Discord] Connection timeout")
                return False

            self._connected = True
            print("[Discord] Connected")
            return True

        except ImportError as e:
            print(f"[Discord] SDK not installed: {e}")
            return False
        except Exception as e:
            print(f"[Discord] Connect failed: {e}")
            return False

    async def _start_client(self):
        """启动客户端"""
        try:
            await self._client.start(self.bot_token)
        except Exception as e:
            print(f"[Discord] Client error: {e}")
            self._connected = False

    async def disconnect(self):
        """断开连接"""
        if self._client:
            try:
                await self._client.close()
            except Exception as e:
                print(f"[Discord] Disconnect error: {e}")

        self._connected = False
        print("[Discord] Disconnected")

    async def send(self, channel_id: str, content: str, **kwargs) -> bool:
        """发送消息"""
        if not self._client:
            print("[Discord] Not connected")
            return False

        try:
            channel = self._client.get_channel(int(channel_id))
            if not channel:
                # 尝试获取 DM channel
                try:
                    user = await self._client.fetch_user(int(channel_id))
                    channel = await user.create_dm()
                except Exception:
                    print(f"[Discord] Channel not found: {channel_id}")
                    return False

            # 分片长消息（Discord 限制 2000 字符）
            for chunk in self._split_text(content, 1900):
                await channel.send(chunk)

            return True

        except Exception as e:
            print(f"[Discord] Send error: {e}")
            return False

    async def _on_message(self, message) -> None:
        """处理 Discord 消息"""
        try:
            # 忽略 bot 自己的消息
            if message.author == self._client.user:
                return

            # 忽略 bot 消息
            if message.author.bot:
                return

            user_id = str(message.author.id)

            # 白名单检查
            if self.allowed_users and user_id not in self.allowed_users:
                return

            # Guild 白名单检查
            if self.allowed_guilds and message.guild:
                if str(message.guild.id) not in self.allowed_guilds:
                    return

            # 构造统一消息
            msg = Message(
                channel=ChannelType.DISCORD,
                channel_id=str(message.channel.id),
                sender_id=user_id,
                sender_name=message.author.display_name,
                content=message.content,
                message_id=str(message.id),
                timestamp=message.created_at.timestamp() if message.created_at else 0,
                metadata={
                    "guild_id": str(message.guild.id) if message.guild else None,
                    "guild_name": message.guild.name if message.guild else None,
                    "channel_name": message.channel.name if hasattr(message.channel, 'name') else "DM",
                }
            )

            # 异步处理
            if self._message_handler:
                await self._emit_message(msg)

        except Exception as e:
            print(f"[Discord] Message error: {e}")

    @staticmethod
    def _split_text(text: str, max_len: int) -> List[str]:
        """分片长文本"""
        if not text:
            return [""]
        return [text[i:i + max_len] for i in range(0, len(text), max_len)]
