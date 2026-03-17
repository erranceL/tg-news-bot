"""
方程式新闻 (BWEnews) WebSocket 监听模块
接入 BWEnews 实时新闻推送，并缓存最近一条快讯供 /latest 使用
"""

import asyncio
import json
import logging
from pathlib import Path
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
        self._heartbeat_task = None
        self._latest_file = Path("bwe_latest.json")

    def get_latest_cached_news(self):
        """读取本地缓存的最近一条 BWE 快讯"""
        try:
            if self._latest_file.exists():
                return json.loads(self._latest_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取 BWE 本地缓存失败: {e}")
        return None

    def _save_latest_news(self, data: dict):
        """保存最近一条 BWE 快讯到本地"""
        try:
            self._latest_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"保存 BWE 本地缓存失败: {e}")

    async def _heartbeat_loop(self):
        """发送文档要求的文本心跳 ping，并记录 pong"""
        while self._running and self.ws:
            try:
                await asyncio.sleep(15)
                if not self._running or not self.ws:
                    break
                await self.ws.send("ping")
                logger.debug("已发送 BWE 文本心跳: ping")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"BWE 心跳发送失败: {e}")
                return

    async def _handle_message(self, message: str):
        """处理收到的 WebSocket 消息"""
        try:
            if isinstance(message, str) and message.strip().lower() == "pong":
                logger.debug("收到 BWE 心跳响应: pong")
                return

            logger.info(f"BWE 原始消息: {str(message)[:500]}")
            data = json.loads(message)

            news_title = data.get("news_title", "")
            if not news_title:
                logger.debug(f"BWE 非新闻 JSON 消息: {str(message)[:200]}")
                return

            source_name = data.get("source_name", "BWEnews")
            logger.info(f"收到方程式新闻: [{source_name}] {news_title}")

            self._save_latest_news(data)

            if deduplicator.is_duplicate(news_title, source="bwe"):
                logger.info(f"BWE 重复新闻已过滤: {news_title[:50]}")
                return

            formatted_msg = format_bwe_news(data)
            await telegram_bot.send_news(formatted_msg, source="bwe")

        except json.JSONDecodeError:
            logger.warning(f"BWE 消息解析失败（非 JSON）: {str(message)[:200]}")
        except Exception as e:
            logger.error(f"处理 BWE 消息异常: {e}", exc_info=True)

    async def _connect_and_listen(self):
        """连接并监听 WebSocket"""
        logger.info(f"正在连接方程式新闻 WebSocket: {BWE_WS_URL}")

        async with websockets.connect(
            BWE_WS_URL,
            ping_interval=None,
            close_timeout=5,
            additional_headers={
                "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
            }
        ) as ws:
            self.ws = ws
            self._reconnect_delay = WS_RECONNECT_DELAY_SECONDS
            logger.info("方程式新闻 WebSocket 已连接")

            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            try:
                async for message in ws:
                    if not self._running:
                        break
                    await self._handle_message(message)
            finally:
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    try:
                        await self._heartbeat_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                    self._heartbeat_task = None

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
                self._reconnect_delay = min(self._reconnect_delay * 2, WS_RECONNECT_MAX_DELAY_SECONDS)

    async def stop(self):
        """停止监听"""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except Exception:
                pass
            self._heartbeat_task = None
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        logger.info("方程式新闻监听器已停止")


bwe_monitor = BWENewsMonitor()
