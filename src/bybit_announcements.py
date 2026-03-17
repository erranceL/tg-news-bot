"""
Bybit 公告监听模块
通过 Bybit 官方 REST API 轮询上币、下架等公告
"""

import asyncio
import logging
import time
import aiohttp
from collections import OrderedDict

from src.config import BYBIT_POLL_INTERVAL_SECONDS
from src.dedup import deduplicator
from src.formatter import format_bybit_announcement
from src.telegram_bot import telegram_bot
from src.resonance_detector import send_resonance_alerts

logger = logging.getLogger("bybit_announcements")

BYBIT_ANNOUNCEMENT_API = "https://api.bybit.com/v5/announcements/index"

LISTING_KEYWORDS = {
    "listing", "listed", "launchpool", "launchpad", "new pair",
    "delisting", "delist", "perpetual", "futures", "margin",
    "上币", "下架", "上线", "合约"
}

RECENT_WINDOW_DAYS = 7
RECENT_WINDOW_MS = RECENT_WINDOW_DAYS * 24 * 60 * 60 * 1000


class BybitAnnouncementMonitor:
    """Bybit 公告监听器"""

    def __init__(self):
        self._running = False
        self._session: aiohttp.ClientSession | None = None
        self._seen_ids: OrderedDict[str, int] = OrderedDict()
        self._first_run = True

    def _is_relevant(self, title: str, tag: str = "") -> bool:
        text = (title + " " + tag).lower()
        return any(kw in text for kw in LISTING_KEYWORDS)

    def _ann_time_ms(self, ann: dict) -> int:
        try:
            return int(ann.get("publishTime", 0))
        except (ValueError, TypeError):
            return 0

    def _is_recent(self, ann: dict) -> bool:
        ts = self._ann_time_ms(ann)
        return ts > 0 and ts >= int(time.time() * 1000) - RECENT_WINDOW_MS

    async def _fetch_announcements(self) -> list:
        params = {"locale": "en-US", "page": 1, "limit": 20}
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
        try:
            async with self._session.get(
                BYBIT_ANNOUNCEMENT_API,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Bybit API HTTP {resp.status}")
                    return []
                data = await resp.json()
                if data.get("retCode") != 0:
                    logger.error(f"Bybit API 错误: {data.get('retMsg')}")
                    return []
                items = data.get("result", {}).get("list", [])
                relevant = [
                    ann for ann in items
                    if self._is_relevant(ann.get("title", ""), ann.get("type", {}).get("title", ""))
                ]
                relevant.sort(key=self._ann_time_ms)
                return relevant
        except aiohttp.ClientError as e:
            logger.error(f"Bybit API 网络异常: {e}")
            return []
        except Exception as e:
            logger.error(f"Bybit API 请求异常: {e}", exc_info=True)
            return []

    async def start(self):
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("Bybit 公告监听器启动")

        try:
            while self._running:
                try:
                    announcements = await self._fetch_announcements()

                    if self._first_run:
                        for ann in announcements:
                            ann_id = str(ann.get("id", ""))
                            if ann_id:
                                self._seen_ids[ann_id] = int(time.time())
                        logger.info(f"Bybit 初始化完成，已记录 {len(self._seen_ids)} 条历史公告基线")
                        self._first_run = False
                    else:
                        new_count = 0
                        for ann in announcements:
                            ann_id = str(ann.get("id", ""))
                            title = ann.get("title", "")
                            if not ann_id or not title:
                                continue
                            if ann_id in self._seen_ids:
                                continue

                            self._seen_ids[ann_id] = int(time.time())

                            if not self._is_recent(ann):
                                logger.info(f"跳过过旧 Bybit 公告: {title[:80]}")
                                continue

                            if deduplicator.is_duplicate(title, source="bybit"):
                                logger.info(f"Bybit 重复公告已过滤: {title[:80]}")
                                continue

                            logger.info(f"新 Bybit 公告: {title}")
                            formatted_msg = format_bybit_announcement({
                                "title": title,
                                "description": ann.get("description", ""),
                                "url": ann.get("url", ""),
                                "publishTime": self._ann_time_ms(ann),
                                "type": ann.get("type", {}).get("title", ""),
                            })
                            await telegram_bot.send_news(formatted_msg, source="bybit")
                            await send_resonance_alerts(title, "Bybit")
                            new_count += 1

                        if new_count:
                            logger.info(f"本轮 Bybit 新推送公告数: {new_count}")

                        if len(self._seen_ids) > 5000:
                            keys_to_remove = list(self._seen_ids.keys())[:-3000]
                            for k in keys_to_remove:
                                del self._seen_ids[k]

                except Exception as e:
                    logger.error(f"Bybit 公告处理异常: {e}", exc_info=True)

                await asyncio.sleep(BYBIT_POLL_INTERVAL_SECONDS)
        finally:
            if self._session:
                await self._session.close()

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
        logger.info("Bybit 公告监听器已停止")


bybit_monitor = BybitAnnouncementMonitor()
