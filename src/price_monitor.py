"""
币安价格波动监控模块
监控币安已上架币种在5分钟内15%的价格波动
"""

import asyncio
import json
import time
import logging
from collections import defaultdict, deque
import aiohttp
import websockets

from src.config import (
    BINANCE_STREAM_URL, BINANCE_REST_URL,
    PRICE_ALERT_WINDOW_MINUTES, PRICE_ALERT_THRESHOLD_PERCENT,
    WS_RECONNECT_DELAY_SECONDS, WS_RECONNECT_MAX_DELAY_SECONDS
)
from src.dedup import deduplicator
from src.formatter import format_price_alert
from src.telegram_bot import telegram_bot

logger = logging.getLogger("price_monitor")


class PriceMonitor:
    """币安价格波动监控器"""

    def __init__(self):
        self.ws = None
        self._running = False
        self._reconnect_delay = WS_RECONNECT_DELAY_SECONDS

        # 价格历史记录: {symbol: deque([(timestamp, price), ...])}
        self._price_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=600))

        # 已发送提醒的冷却期: {symbol: last_alert_timestamp}
        self._alert_cooldown: dict[str, float] = {}
        self._cooldown_seconds = 300  # 同一币种 5 分钟内不重复提醒

        # 已上架的 USDT 交易对列表
        self._usdt_symbols: set = set()

    async def _fetch_usdt_symbols(self):
        """从币安 REST API 获取所有已上架的 USDT 交易对"""
        url = f"{BINANCE_REST_URL}/api/v3/exchangeInfo"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.error(f"获取交易对信息失败: HTTP {resp.status}")
                        return

                    data = await resp.json()
                    symbols = data.get("symbols", [])

                    self._usdt_symbols = set()
                    for s in symbols:
                        if (s.get("quoteAsset") == "USDT"
                                and s.get("status") == "TRADING"):
                            self._usdt_symbols.add(s["symbol"].lower())

                    logger.info(f"已加载 {len(self._usdt_symbols)} 个 USDT 交易对")

        except Exception as e:
            logger.error(f"获取交易对信息异常: {e}", exc_info=True)

    def _check_price_alert(self, symbol: str, current_price: float, current_time: float):
        """
        检查价格是否在时间窗口内超过阈值波动
        """
        history = self._price_history[symbol]

        if len(history) < 2:
            return

        window_start = current_time - (PRICE_ALERT_WINDOW_MINUTES * 60)

        # 找到时间窗口内最早的价格
        base_price = None
        for ts, price in history:
            if ts >= window_start:
                base_price = price
                break

        if base_price is None or base_price == 0:
            return

        # 计算波动百分比
        change_percent = ((current_price - base_price) / base_price) * 100

        if abs(change_percent) >= PRICE_ALERT_THRESHOLD_PERCENT:
            # 检查冷却期
            last_alert = self._alert_cooldown.get(symbol, 0)
            if current_time - last_alert < self._cooldown_seconds:
                return

            self._alert_cooldown[symbol] = current_time

            # 生成提醒标题用于去重
            alert_title = f"price_alert_{symbol}_{int(current_time // 300)}"
            if deduplicator.is_duplicate(alert_title, source="price"):
                return

            logger.warning(
                f"价格波动提醒: {symbol.upper()} "
                f"从 {base_price} 到 {current_price} "
                f"({change_percent:+.2f}%) 在 {PRICE_ALERT_WINDOW_MINUTES} 分钟内"
            )

            # 异步发送提醒
            formatted_msg = format_price_alert(
                symbol=symbol.upper(),
                current_price=current_price,
                base_price=base_price,
                change_percent=change_percent,
                window_minutes=PRICE_ALERT_WINDOW_MINUTES
            )
            asyncio.create_task(telegram_bot.send_news(formatted_msg, source="price"))

    async def _handle_message(self, message: str):
        """处理 miniTicker 消息"""
        try:
            data = json.loads(message)

            # 处理数组格式（!miniTicker@arr）
            if isinstance(data, list):
                for item in data:
                    self._process_ticker(item)
            elif isinstance(data, dict):
                # 处理单个 stream 的包装格式
                if "data" in data:
                    inner = data["data"]
                    if isinstance(inner, list):
                        for item in inner:
                            self._process_ticker(item)
                    else:
                        self._process_ticker(inner)
                else:
                    self._process_ticker(data)

        except json.JSONDecodeError:
            logger.warning(f"价格消息解析失败: {message[:200]}")
        except Exception as e:
            logger.error(f"处理价格消息异常: {e}", exc_info=True)

    def _process_ticker(self, ticker: dict):
        """处理单个 ticker 数据"""
        symbol = ticker.get("s", "").lower()
        if not symbol or symbol not in self._usdt_symbols:
            return

        try:
            close_price = float(ticker.get("c", 0))
        except (ValueError, TypeError):
            return

        if close_price <= 0:
            return

        current_time = time.time()
        history = self._price_history[symbol]

        # 记录价格
        history.append((current_time, close_price))

        # 清理超出时间窗口的旧数据
        window_start = current_time - (PRICE_ALERT_WINDOW_MINUTES * 60) - 60  # 多保留1分钟
        while history and history[0][0] < window_start:
            history.popleft()

        # 检查波动
        self._check_price_alert(symbol, close_price, current_time)

    async def _connect_and_listen(self):
        """连接币安行情 WebSocket 并监听"""
        # 使用 !miniTicker@arr 订阅所有交易对的 miniTicker
        stream_url = f"{BINANCE_STREAM_URL}/ws/!miniTicker@arr"

        logger.info(f"正在连接币安行情 WebSocket: {stream_url}")

        async with websockets.connect(
            stream_url,
            ping_interval=25,
            ping_timeout=10,
            close_timeout=5,
            max_size=10 * 1024 * 1024  # 10MB，因为 all tickers 数据量大
        ) as ws:
            self.ws = ws
            self._reconnect_delay = WS_RECONNECT_DELAY_SECONDS
            logger.info("币安行情 WebSocket 已连接")

            async for message in ws:
                if not self._running:
                    break
                await self._handle_message(message)

    async def _periodic_refresh_symbols(self):
        """定期刷新交易对列表"""
        while self._running:
            await asyncio.sleep(3600)  # 每小时刷新一次
            try:
                await self._fetch_usdt_symbols()
            except Exception as e:
                logger.error(f"刷新交易对列表失败: {e}")

    async def _periodic_cleanup(self):
        """定期清理过期的价格历史和冷却记录"""
        while self._running:
            await asyncio.sleep(600)  # 每10分钟清理一次
            try:
                now = time.time()
                # 清理冷却记录
                expired = [k for k, v in self._alert_cooldown.items()
                           if now - v > self._cooldown_seconds * 2]
                for k in expired:
                    del self._alert_cooldown[k]

                # 清理无数据的 symbol
                empty_symbols = [k for k, v in self._price_history.items() if not v]
                for k in empty_symbols:
                    del self._price_history[k]

                logger.debug(f"清理完成: 监控 {len(self._price_history)} 个交易对")
            except Exception as e:
                logger.error(f"清理异常: {e}")

    async def start(self):
        """启动价格监控"""
        self._running = True
        logger.info("价格波动监控器启动")

        # 首先获取交易对列表
        await self._fetch_usdt_symbols()

        if not self._usdt_symbols:
            logger.error("未获取到交易对列表，价格监控将在重试后启动")

        # 启动定期任务
        asyncio.create_task(self._periodic_refresh_symbols())
        asyncio.create_task(self._periodic_cleanup())

        # 主循环：连接 WebSocket
        while self._running:
            try:
                await self._connect_and_listen()
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"行情 WebSocket 连接关闭: {e}")
            except Exception as e:
                logger.error(f"行情 WebSocket 异常: {e}", exc_info=True)

            if self._running:
                logger.info(f"将在 {self._reconnect_delay} 秒后重连行情...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    WS_RECONNECT_MAX_DELAY_SECONDS
                )

    async def stop(self):
        """停止监控"""
        self._running = False
        if self.ws:
            try:
                await self.ws.close()
            except (Exception, asyncio.CancelledError):
                pass
        self.ws = None
        logger.info("价格波动监控器已停止")


# 全局实例
price_monitor = PriceMonitor()
