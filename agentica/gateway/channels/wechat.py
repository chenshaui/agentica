# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description:
Personal WeChat channel implementation using an inline ``WxBotClient``.

Talks to ``https://ilinkai.weixin.qq.com`` via HTTP long-polling and
supports QR-code login. This is the only practical way to interact with
personal WeChat accounts from Python — there is no official SDK. The
client itself is synchronous and runs ``run_loop`` in a background daemon
thread; inbound messages are dispatched to the main asyncio event loop
via :py:meth:`asyncio.AbstractEventLoop.call_soon_threadsafe`.

Transport-only port: image/video/file media handling is intentionally
omitted (drop ``_send_media``, ``_dl_media`` and the ``pycryptodome``
dependency).
"""
import asyncio
import base64
import json
import os
import struct
import threading
import time
import uuid
import webbrowser
from collections import deque
from pathlib import Path
from typing import Optional, List, Callable

import requests

from agentica.utils.log import logger

from .base import Channel, ChannelType, Message
from ..config import settings

# ── ilinkai protocol constants ──
_API = "https://ilinkai.weixin.qq.com"
_DEFAULT_TOKEN_FILE = Path.home() / ".wxbot" / "token.json"
_VERSION = "2.1.8"
_MSG_USER, _MSG_BOT = 1, 2
_ITEM_TEXT = 1
_STATE_FINISH = 2

# ``qrcode`` is only needed when the token cache is empty; lazy-import to
# avoid pulling Pillow into core gateway installs.
qrcode = None


def _ensure_qrcode():
    """Ensure the ``qrcode`` package has been imported (lazy)."""
    global qrcode
    if qrcode is None:
        try:
            import qrcode as _qr
            qrcode = _qr
        except ImportError:
            raise ImportError(
                "qrcode not installed. Run: pip install agentica[wechat]"
            )


def _uin() -> str:
    """Generate a one-shot ``X-WECHAT-UIN`` header value."""
    return base64.b64encode(str(struct.unpack(">I", os.urandom(4))[0]).encode()).decode()


class WxBotClient:
    """Minimal text-only WeChat bot client (inline ilinkai HTTP transport).

    Adapted from ``GenericAgent/frontends/wechatapp.py``. Media-related
    methods (image/video/file upload & download) are dropped since the
    gateway port is transport-only.
    """

    def __init__(self, token: Optional[str] = None, token_file: Optional[str] = None):
        self._tf = Path(token_file) if token_file else _DEFAULT_TOKEN_FILE
        self._tf.parent.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.bot_id: Optional[str] = None
        self._buf = ""
        if not self.token:
            self._load()

    def _load(self) -> None:
        """Load cached token from disk if present."""
        if self._tf.exists():
            d = json.loads(self._tf.read_text("utf-8"))
            self.token = d.get("bot_token", "")
            self.bot_id = d.get("ilink_bot_id", "")
            self._buf = d.get("updates_buf", "")

    def _save(self, **kw) -> None:
        """Persist current token state to disk."""
        d = {
            "bot_token": self.token or "",
            "ilink_bot_id": self.bot_id or "",
            "updates_buf": self._buf or "",
            **kw,
        }
        self._tf.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")

    def _post(self, ep: str, body: dict, timeout: int = 15) -> dict:
        """POST to an ilinkai endpoint with auth headers."""
        h = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _uin(),
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        r = requests.post(f"{_API}/{ep}", json=body, headers=h, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def login_qr(self, poll_interval: int = 2) -> dict:
        """Interactive QR-code login flow.

        Saves the QR PNG to ``<token_file_dir>/wx_qr.png``, opens it in the
        default web browser, and polls until the QR is confirmed or expires.
        Returns the final status payload on success.
        """
        _ensure_qrcode()
        r = requests.get(f"{_API}/ilink/bot/get_bot_qrcode", params={"bot_type": 3}, timeout=10)
        r.raise_for_status()
        d = r.json()
        qr_id, url = d["qrcode"], d.get("qrcode_img_content", "")
        logger.info(f"WeChat: QR login ID = {qr_id}")
        if url:
            img = self._tf.parent / "wx_qr.png"
            qrcode.make(url).save(str(img))
            try:
                webbrowser.open(str(img))
            except Exception:
                pass
            logger.info(f"WeChat: scan QR at {img}")
        last = ""
        while True:
            time.sleep(poll_interval)
            try:
                s = requests.get(
                    f"{_API}/ilink/bot/get_qrcode_status",
                    params={"qrcode": qr_id},
                    timeout=60,
                ).json()
            except requests.exceptions.ReadTimeout:
                continue
            st = s.get("status", "")
            if st != last:
                logger.info(f"WeChat: QR status = {st}")
                last = st
            if st == "confirmed":
                self.token = s.get("bot_token", "")
                self.bot_id = s.get("ilink_bot_id", "")
                self._save(login_time=time.strftime("%Y-%m-%d %H:%M:%S"))
                logger.info(f"WeChat: QR login OK (bot_id={self.bot_id})")
                return s
            if st == "expired":
                raise RuntimeError("WeChat: QR code expired")

    def get_updates(self, timeout: int = 30) -> list:
        """Long-poll for new messages."""
        try:
            resp = self._post(
                "ilink/bot/getupdates",
                {"get_updates_buf": self._buf or "", "base_info": {"channel_version": _VERSION}},
                timeout=timeout + 5,
            )
        except requests.exceptions.ReadTimeout:
            return []
        if resp.get("errcode"):
            logger.warning(f"WeChat: getUpdates err {resp.get('errcode')} {resp.get('errmsg', '')}")
            if resp["errcode"] == -14:
                self._buf = ""
                self._save()
            return []
        nb = resp.get("get_updates_buf", "")
        if nb:
            self._buf = nb
            self._save()
        return resp.get("msgs") or []

    def send_text(self, to_user_id: str, text: str, context_token: str = "") -> dict:
        """Send a text message to a user."""
        msg = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": f"pyclient-{uuid.uuid4().hex[:16]}",
            "message_type": _MSG_BOT,
            "message_state": _STATE_FINISH,
            "item_list": [{"type": _ITEM_TEXT, "text_item": {"text": text}}],
        }
        if context_token:
            msg["context_token"] = context_token
        return self._post(
            "ilink/bot/sendmessage",
            {"msg": msg, "base_info": {"channel_version": _VERSION}},
        )

    def send_typing(self, to_user_id: str, typing_ticket: str = "", cancel: bool = False) -> dict:
        """Show / cancel the typing indicator."""
        return self._post("ilink/bot/sendtyping", {
            "to_user_id": to_user_id,
            "typing_ticket": typing_ticket,
            "typing_status": 2 if cancel else 1,
            "base_info": {"channel_version": _VERSION},
        })

    @staticmethod
    def extract_text(msg: dict) -> str:
        """Concatenate all text items from an inbound message."""
        return "\n".join(
            it["text_item"].get("text", "")
            for it in msg.get("item_list", [])
            if it.get("type") == _ITEM_TEXT and it.get("text_item")
        )

    @staticmethod
    def is_user_msg(msg: dict) -> bool:
        return msg.get("message_type") == _MSG_USER

    def run_loop(self, on_message: Callable[["WxBotClient", dict], None], poll_timeout: int = 30) -> None:
        """Blocking inbound message loop. Run in a background thread.

        ``on_message(client, msg)`` is invoked once per new user message.
        Errors raised by the callback are logged but do not stop the loop.
        """
        logger.info(f"WeChat: listening (bot_id={self.bot_id})")
        seen: set = set()
        while True:
            try:
                for msg in self.get_updates(poll_timeout):
                    mid = msg.get("message_id", 0)
                    if not self.is_user_msg(msg) or mid in seen:
                        continue
                    seen.add(mid)
                    if len(seen) > 5000:
                        seen = set(list(seen)[-2000:])
                    try:
                        on_message(self, msg)
                    except Exception as e:
                        logger.error(f"WeChat: callback error: {e}")
            except KeyboardInterrupt:
                logger.info("WeChat: loop interrupted")
                break
            except Exception as e:
                logger.error(f"WeChat: loop error: {e}, retry in 5s")
                time.sleep(5)


class WeChatChannel(Channel):
    """Personal WeChat messaging channel.

    Wraps :class:`WxBotClient` (sync, blocking) inside the async
    ``Channel`` ABC. Inbound messages are received on a daemon thread and
    dispatched to the main event loop via ``call_soon_threadsafe``.
    """

    SPLIT_LIMIT = 1800

    def __init__(
        self,
        token_file: Optional[str] = None,
        allowed_users: Optional[List[str]] = None,
    ):
        super().__init__(allowed_users=allowed_users or settings.wechat_allowed_users or [])
        self.token_file = token_file or settings.wechat_token_file
        self._bot: Optional[WxBotClient] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._processed_ids: deque = deque(maxlen=2000)
        # Cache the latest ``context_token`` per user so replies stay in
        # the same WeChat conversation thread.
        self._user_ctx: dict = {}

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.WECHAT

    async def connect(self) -> bool:
        """Initialize WxBotClient (running QR login if no token)."""
        try:
            self._bot = WxBotClient(token_file=self.token_file)
            self._main_loop = asyncio.get_running_loop()

            if not self._bot.token:
                logger.warning("WeChat: no cached token, running QR login (interactive)...")
                # login_qr() blocks until QR is scanned; run off the event loop
                await asyncio.to_thread(self._bot.login_qr)

            self._loop_thread = threading.Thread(
                target=self._bot.run_loop,
                args=(self._on_native_message,),
                daemon=True,
                name="wechat-poll",
            )
            self._loop_thread.start()

            self._connected = True
            logger.info(f"WeChat: Connected (bot_id={self._bot.bot_id})")
            return True

        except ImportError as e:
            logger.error(f"WeChat: dependency missing: {e}")
            return False
        except Exception as e:
            logger.error(f"WeChat: Connect failed: {e}")
            return False

    async def disconnect(self):
        """Mark disconnected. The polling thread is daemon and exits with the process."""
        self._connected = False
        logger.info("WeChat: Disconnected")

    async def send(self, channel_id: str, content: str, **kwargs) -> bool:  # noqa: ARG002
        """Send a text reply to a WeChat user.

        ``channel_id`` is the WeChat ``user_id``. The cached
        ``context_token`` (if any) is included to keep the reply in the
        same conversation thread.
        """
        if not self._bot:
            logger.warning("WeChat: Not connected")
            return False

        ctx = self._user_ctx.get(channel_id, "")
        try:
            for chunk in self.split_text(content, self.SPLIT_LIMIT):
                await asyncio.to_thread(self._bot.send_text, channel_id, chunk, ctx)
            return True
        except Exception as e:
            logger.error(f"WeChat: Send error: {e} channel_id={channel_id}")
            return False

    def _on_native_message(self, bot: WxBotClient, msg: dict) -> None:
        """Sync callback (runs in poll thread). Dispatches to main loop."""
        try:
            mid = msg.get("message_id")
            if mid in self._processed_ids:
                return
            if mid:
                self._processed_ids.append(mid)

            text = bot.extract_text(msg).strip()
            if not text:
                return

            uid = msg.get("from_user_id", "") or "unknown"
            ctx = msg.get("context_token", "")
            if ctx:
                self._user_ctx[uid] = ctx

            if not self.check_allowlist(uid):
                logger.debug(f"WeChat: User {uid} not in allowlist")
                return

            message = Message(
                channel=ChannelType.WECHAT,
                channel_id=uid,
                sender_id=uid,
                sender_name=uid,
                content=text,
                message_id=str(mid) if mid else "",
                metadata={"context_token": ctx},
            )

            if self._message_handler and self._main_loop:
                def _dispatch():
                    fut = asyncio.ensure_future(self._emit_message(message))
                    fut.add_done_callback(WeChatChannel._log_dispatch_error)

                self._main_loop.call_soon_threadsafe(_dispatch)
        except Exception as e:
            logger.error(f"WeChat: native message error: {e}")

    @staticmethod
    def _log_dispatch_error(fut: asyncio.Future) -> None:
        """Surface errors from dispatched message tasks."""
        if fut.exception():
            logger.error(f"WeChat: Dispatch error: {fut.exception()}")
