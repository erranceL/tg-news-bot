"""
Polymarket 预测市场监听模块
监控新开盘的市场以及交易量突增事件
"""

import asyncio
import logging
import time
import aiohttp
from collections import OrderedDict

from src.config import POLYMARKET_POLL_INTERVAL_SECONDS
from src.dedup import deduplicator
from src.formatter import format_polymarket_event
from src.telegram_bot import telegram_bot
from src.resonance_detector import resonance_detector

logger = logging.getLogger("polymarket_monitor")

POLYMARKET_CLOB_API = "https://clob.polymarket.com/markets"
POLYMARKET_GAMMA_API = "https://gamma-api.polymarket.com/markets"

# volume 突增倍数阈值（相比上一次轮询）
VOLUME_SURGE_MULTIPLIER = 3.0
# 最低 volume 阈值，过滤掉冷清市场（USD）
MIN_VOLUME_THRESHOLD = 50_000


class PolymarketMonitor:
    """Polymarket 预测市场监听器"""

    def __init__(self):
        self._running = False
        self._seen_market_ids: OrderedDict[str, int] = OrderedDict()
        self._volume_snapshot: dict[str, float] = {}
        self._first_run = True

    async def _fetch_markets(self, session: aiohttp.ClientSession) -> list:
        """获取最新活跃市场列表"""
        params = {
            "active": "true",
            "closed": "false",
            "limit": 50,
            "order": "volume",
            "ascending": "false",
        }
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
        try:
            async with session.get(
                POLYMARKET_GAMMA_API,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Polymarket API HTTP {resp.status}")
                    return []
                data = await resp.json()
                if isinstance(data, list):
                    return data
                return data.get("markets", []) if isinstance(data, dict) else []
        except aiohttp.ClientError as e:
            logger.error(f"Polymarket API 网络异常: {e}")
            return []
        except Exception as e:
            logger.error(f"Polymarket API 请求异常: {e}", exc_info=True)
            return []

    def _market_id(self, market: dict) -> str:
        return str(market.get("id", "") or market.get("conditionId", ""))

    def _market_volume(self, market: dict) -> float:
        try:
            return float(market.get("volume", 0) or market.get("volumeNum", 0) or 0)
        except (ValueError, TypeError):
            return 0.0

    async def start(self):
        self._running = True
        logger.info("Polymarket 预测市场监听器启动")

        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    markets = await self._fetch_markets(session)

                    if self._first_run:
                        for market in markets:
                            mid = self._market_id(market)
                            if mid:
                                self._seen_market_ids[mid] = int(time.time())
                                self._volume_snapshot[mid] = self._market_volume(market)
                        logger.info(f"Polymarket 初始化完成，已记录 {len(self._seen_market_ids)} 个市场基线")
                        self._first_run = False
                    else:
                        for market in markets:
                            mid = self._market_id(market)
                            question = market.get("question", "") or market.get("title", "")
                            if not mid or not question:
                                continue

                            current_volume = self._market_volume(market)

                            # 检测新市场
                            if mid not in self._seen_market_ids:
                                self._seen_market_ids[mid] = int(time.time())
                                self._volume_snapshot[mid] = current_volume

                                if deduplicator.is_duplicate(question, source="polymarket"):
                                    continue

                                logger.info(f"新 Polymarket 市场: {question[:100]}")
                                end_date = market.get("endDate", "") or market.get("end_date_iso", "")
                                url = market.get("url", "") or f"https://polymarket.com/event/{mid}"
                                formatted_msg = format_polymarket_event({
                                    "question": question,
                                    "volume": current_volume,
                                    "end_date_iso": end_date,
                                    "url": url,
                                    "alert_type": "new_market",
                                })
                                await telegram_bot.send_news(formatted_msg, source="polymarket")
                                resonance_detector.record(question, "Polymarket")

                            else:
                                # 检测 volume 突增
                                prev_volume = self._volume_snapshot.get(mid, 0)
                                self._volume_snapshot[mid] = current_volume

                                if (
                                    prev_volume >= MIN_VOLUME_THRESHOLD
                                    and current_volume >= MIN_VOLUME_THRESHOLD
                                    and prev_volume > 0
                                    and current_volume / prev_volume >= VOLUME_SURGE_MULTIPLIER
                                ):
                                    surge_key = f"polymarket_surge_{mid}_{int(time.time() // 3600)}"
                                    if deduplicator.is_duplicate(surge_key, source="polymarket"):
                                        continue

                                    logger.info(
                                        f"Polymarket 交易量突增: {question[:80]} "
                                        f"({prev_volume:,.0f} -> {current_volume:,.0f})"
                                    )
                                    url = market.get("url", "") or f"https://polymarket.com/event/{mid}"
                                    formatted_msg = format_polymarket_event({
                                        "question": question,
                                        "volume": current_volume,
                                        "url": url,
                                        "alert_type": "volume_surge",
                                    })
                                    await telegram_bot.send_news(formatted_msg, source="polymarket")
                                    resonance_detector.record(question, "Polymarket")

                        if len(self._seen_market_ids) > 5000:
                            keys_to_remove = list(self._seen_market_ids.keys())[:-3000]
                            for k in keys_to_remove:
                                del self._seen_market_ids[k]
                                self._volume_snapshot.pop(k, None)

                except Exception as e:
                    logger.error(f"Polymarket 处理异常: {e}", exc_info=True)

                await asyncio.sleep(POLYMARKET_POLL_INTERVAL_SECONDS)

    async def stop(self):
        self._running = False
        logger.info("Polymarket 预测市场监听器已停止")


polymarket_monitor = PolymarketMonitor()
