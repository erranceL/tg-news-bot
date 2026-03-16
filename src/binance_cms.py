"""
币安 CMS WebSocket 监听模块
监听币安上币、下架等官方公告
"""

import asyncio
import json
import time
import hmac
import hashlib
import uuid
import logging
import websockets

from src.config import (
    BINANCE_CMS_WS_URL, BINANCE_CMS_TOPIC,
    BINANCE_API_KEY, BINANCE_API_SECRET,
    WS_RECONNECT_DELAY_SECONDS, WS_RECONNECT_MAX_DELAY_SECONDS
)
from src.dedup import deduplicator
from src.formatter import format_binance_announcement
from src.telegram_bot import telegram_bot

logger = logging.getLogger("binance_cms")


class BinanceCMSMonitor:
    """币安 CMS WebSocket 公告监听器"""

    def __init__(self):
        self.ws = None
        self._running = False
        self._reconnect_delay = WS_RECONNECT_DELAY_SECONDS

    def _build_ws_url(self) -> str:
        """构建带签名的 WebSocket 连接 URL"""
        random_str = uuid.uuid4().hex[:32]
        timestamp = str(int(time.time() * 1000))
        recv_window = "30000"

        # 按字母顺序排列参数（排除 signature）
        params = {
            "random": random_str,
            "recvWindow": recv_window,
            "timestamp": timestamp,
            "topic": BINANCE_CMS_TOPIC,
        }

        # 构建签名 payload（按 key 字母排序）
        sorted_params = sorted(params.items(), key=lambda x: x[0])
        payload = "&".join(f"{k}={v}" for k, v in sorted_params)

        # HMAC SHA256 签名
        signature = hmac.new(
            BINANCE_API_SECRET.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        # 构建完整 URL
        url = f"{BINANCE_CMS_WS_URL}?{payload}&signature={signature}"
        return url

    async def _handle_message(self, message: str):
        """处理收到的 WebSocket 消息"""
        try:
            msg = json.loads(message)
            msg_type = msg.get("type", "")

            if msg_type == "COMMAND":
                # 订阅/取消订阅的响应
                sub_type = msg.get("subType", "")
                code = msg.get("code", "")
                logger.info(f"币安 CMS 命令响应: {sub_type} - {code}")
                return

            if msg_type == "DATA":
                # 公告数据
                topic = msg.get("topic", "")
                data_str = msg.get("data", "")

                if not data_str:
                    return

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.error(f"解析公告数据失败: {data_str[:200]}")
                    return

                title = data.get("title", "")
                catalog_name = data.get("catalogName", "")

                logger.info(f"收到币安公告: [{catalog_name}] {title}")

                # 去重检查
                if deduplicator.is_duplicate(title, source="binance"):
                    logger.info(f"重复公告已过滤: {title[:50]}")
                    return

                # 格式化并发送
                formatted_msg = format_binance_announcement(data)
                await telegram_bot.send_news(formatted_msg, source="binance")

            else:
                logger.debug(f"币安 CMS 未知消息类型: {msg_type}")

        except json.JSONDecodeError:
            logger.error(f"解析 WebSocket 消息失败: {message[:200]}")
        except Exception as e:
            logger.error(f"处理币安 CMS 消息异常: {e}", exc_info=True)

    async def _connect_and_listen(self):
        """连接并监听 WebSocket"""
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            logger.warning(
                "币安 API Key 或 Secret Key 未配置，CMS WebSocket 监听已跳过。"
                "请在 config.py 中配置 BINANCE_API_KEY 和 BINANCE_API_SECRET。"
            )
            # 即使没有 API Key，也保持运行状态以便后续配置
            while self._running:
                await asyncio.sleep(60)
            return

        url = self._build_ws_url()
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}

        logger.info("正在连接币安 CMS WebSocket...")

        async with websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=25,
            ping_timeout=10,
            close_timeout=5
        ) as ws:
            self.ws = ws
            self._reconnect_delay = WS_RECONNECT_DELAY_SECONDS
            logger.info("币安 CMS WebSocket 已连接")

            # 发送订阅请求
            subscribe_msg = json.dumps({
                "command": "SUBSCRIBE",
                "value": BINANCE_CMS_TOPIC
            })
            await ws.send(subscribe_msg)
            logger.info(f"已发送订阅请求: {BINANCE_CMS_TOPIC}")

            # 持续监听消息
            async for message in ws:
                if not self._running:
                    break
                await self._handle_message(message)

    async def start(self):
        """启动监听（带自动重连）"""
        self._running = True
        logger.info("币安 CMS 监听器启动")

        while self._running:
            try:
                await self._connect_and_listen()
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"币安 CMS WebSocket 连接关闭: {e}")
            except Exception as e:
                logger.error(f"币安 CMS WebSocket 异常: {e}", exc_info=True)

            if self._running:
                logger.info(f"将在 {self._reconnect_delay} 秒后重连...")
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
        logger.info("币安 CMS 监听器已停止")


# 全局实例
binance_cms_monitor = BinanceCMSMonitor()
