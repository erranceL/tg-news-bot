"""
方程式新闻 (BWEnews) WebSocket 监听模块
接入 BWEnews 实时新闻推送
"""

import asyncio
import json
import logging
import websockets

from src.config import BWE_WS_URL, WS_RECONNECT_DELAY_SECONDS, WS_RECONNECT_MAX_DELAY_SECONDS
from src.dedup import deduplicator
from src.formatter import format_bwe_news
from src.telegram_bot import telegram_bot

logger = logging.getLogger("bwe_news")


class BWENewsMonitor:
    """方程式新闻 WebSocket 监听器"""

    def __init__(self):
        self.ws = None
        self._running = False
        self._reconnect_delay = WS_RECONNECT_DELAY_SECONDS

    async def _handle_message(self, message: str):
        """处理收到的 WebSocket 消息"""
        try:
            data = json.loads(message)

            # 检查是否是新闻消息（包含 news_title 字段）
            news_title = data.get("news_title", "")
            if not news_title:
                # 可能是心跳或其他控制消息
                logger.debug(f"BWE 非新闻消息: {message[:200]}")
                return

            source_name = data.get("source_name", "BWEnews")
            coins = data.get("coins_included", [])
            url = data.get("url", "")

            logger.info(f"收到方程式新闻: [{source_name}] {news_title}")

            # 去重检查
            if deduplicator.is_duplicate(news_title, source="bwe"):
                logger.info(f"BWE 重复新闻已过滤: {news_title[:50]}")
                return

            # 格式化并发送
            formatted_msg = format_bwe_news(data)
            await telegram_bot.send_news(formatted_msg, source="bwe")

        except json.JSONDecodeError:
            logger.warning(f"BWE 消息解析失败（非 JSON）: {message[:200]}")
        except Exception as e:
            logger.error(f"处理 BWE 消息异常: {e}", exc_info=True)

    async def _connect_and_listen(self):
        """连接并监听 WebSocket"""
        logger.info(f"正在连接方程式新闻 WebSocket: {BWE_WS_URL}")

        async with websockets.connect(
            BWE_WS_URL,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
            additional_headers={
                "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
            }
        ) as ws:
            self.ws = ws
            self._reconnect_delay = WS_RECONNECT_DELAY_SECONDS
            logger.info("方程式新闻 WebSocket 已连接")

            # 持续监听消息
            async for message in ws:
                if not self._running:
                    break
                await self._handle_message(message)

    async def start(self):
        """启动监听（带自动重连）"""
        self._running = True
        logger.info("方程式新闻监听器启动")

        while self._running:
            try:
                await self._connect_and_listen()
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"BWE WebSocket 连接关闭: {e}")
            except websockets.exceptions.InvalidStatusCode as e:
                logger.error(f"BWE WebSocket 连接被拒绝: {e}")
            except ConnectionRefusedError as e:
                logger.error(f"BWE WebSocket 连接被拒绝: {e}")
            except Exception as e:
                logger.error(f"BWE WebSocket 异常: {e}", exc_info=True)

            if self._running:
                logger.info(f"将在 {self._reconnect_delay} 秒后重连 BWE...")
                await asyncio.sleep(self._reconnect_delay)
                # 指数退避
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    WS_RECONNECT_MAX_DELAY_SECONDS
                )

    async def stop(self):
        """停止监听"""
        self._running = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        logger.info("方程式新闻监听器已停止")


# 全局实例
bwe_monitor = BWENewsMonitor()
