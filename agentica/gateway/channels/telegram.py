"""Telegram 渠道实现"""
import asyncio
from typing import Optional, List

from .base import Channel, ChannelType, Message
from ..config import settings

# 延迟导入 Telegram SDK
telegram = None
Application = None


def _ensure_telegram_sdk():
    """确保 Telegram SDK 已导入"""
    global telegram, Application
    if telegram is None:
        try:
            from telegram import Bot, Update
            from telegram.ext import Application as _Application, MessageHandler, filters
            telegram = type('telegram', (), {'Bot': Bot, 'Update': Update, 'MessageHandler': MessageHandler, 'filters': filters})()
            Application = _Application
        except ImportError:
            raise ImportError(
                "Telegram SDK 未安装，请运行: pip install python-telegram-bot"
            )


class TelegramChannel(Channel):
    """Telegram 渠道

    使用 python-telegram-bot 库实现
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        allowed_users: Optional[List[str]] = None,
    ):
        super().__init__()
        self.bot_token = bot_token or settings.telegram_bot_token
        self.allowed_users = allowed_users or []
        self._app = None
        self._bot = None

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.TELEGRAM

    async def connect(self) -> bool:
        """建立连接"""
        if not self.bot_token:
            print("[Telegram] Missing bot token, skipped")
            return False

        try:
            _ensure_telegram_sdk()

            # 创建 Application
            self._app = Application.builder().token(self.bot_token).build()
            self._bot = self._app.bot

            # 注册消息处理器
            self._app.add_handler(
                telegram.MessageHandler(
                    telegram.filters.TEXT & ~telegram.filters.COMMAND,
                    self._on_message,
                )
            )

            # 启动轮询（在后台运行）
            asyncio.create_task(self._start_polling())

            self._connected = True
            print("[Telegram] Connected")
            return True

        except ImportError as e:
            print(f"[Telegram] SDK not installed: {e}")
            return False
        except Exception as e:
            print(f"[Telegram] Connect failed: {e}")
            return False

    async def _start_polling(self):
        """启动轮询"""
        try:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)
        except Exception as e:
            print(f"[Telegram] Polling error: {e}")
            self._connected = False

    async def disconnect(self):
        """断开连接"""
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as e:
                print(f"[Telegram] Disconnect error: {e}")

        self._connected = False
        print("[Telegram] Disconnected")

    async def send(self, channel_id: str, content: str, **kwargs) -> bool:
        """发送消息"""
        if not self._bot:
            print("[Telegram] Not connected")
            return False

        try:
            # 分片长消息（Telegram 限制 4096 字符）
            for chunk in self._split_text(content, 4000):
                await self._bot.send_message(
                    chat_id=int(channel_id),
                    text=chunk,
                    parse_mode=kwargs.get("parse_mode", "Markdown"),
                )
            return True

        except Exception as e:
            print(f"[Telegram] Send error: {e}")
            return False

    async def _on_message(self, update, context) -> None:  # noqa: ARG002
        """处理 Telegram 消息"""
        try:
            msg = update.message
            if not msg or not msg.text:
                return

            user = msg.from_user
            user_id = str(user.id) if user else ""

            # 白名单检查
            if self.allowed_users and user_id not in self.allowed_users:
                print(f"[Telegram] User {user_id} not in allowlist")
                return

            # 构造统一消息
            message = Message(
                channel=ChannelType.TELEGRAM,
                channel_id=str(msg.chat_id),
                sender_id=user_id,
                sender_name=user.username or user.first_name or "",
                content=msg.text,
                message_id=str(msg.message_id),
                timestamp=msg.date.timestamp() if msg.date else 0,
                metadata={
                    "chat_type": msg.chat.type,
                    "first_name": user.first_name if user else "",
                    "last_name": user.last_name if user else "",
                }
            )

            # 异步处理
            if self._message_handler:
                await self._emit_message(message)

        except Exception as e:
            print(f"[Telegram] Message error: {e}")

    @staticmethod
    def _split_text(text: str, max_len: int) -> List[str]:
        """分片长文本"""
        if not text:
            return [""]
        return [text[i:i + max_len] for i in range(0, len(text), max_len)]
