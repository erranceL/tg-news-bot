"""
Coinbase 博客监听模块
通过 RSS Feed 轮询 Coinbase 官方博客，过滤上币/产品更新相关内容
"""

import asyncio
import logging
import time
import aiohttp
import feedparser
from collections import OrderedDict

from src.config import COINBASE_POLL_INTERVAL_SECONDS
from src.dedup import deduplicator
from src.formatter import format_coinbase_announcement
from src.telegram_bot import telegram_bot
from src.resonance_detector import send_resonance_alerts

logger = logging.getLogger("coinbase_monitor")

COINBASE_RSS_URL = "https://www.coinbase.com/blog/rss"

LISTING_KEYWORDS = {
    "listing", "listed", "asset", "launch", "trading",
    "new", "support", "add", "futures", "perpetual",
    "上币", "上线"
}

RECENT_WINDOW_DAYS = 7


class CoinbaseMonitor:
    """Coinbase 博客 RSS 监听器"""

    def __init__(self):
        self._running = False
        self._seen_ids: OrderedDict[str, int] = OrderedDict()
        self._first_run = True

    def _is_relevant(self, title: str, summary: str = "") -> bool:
        text = (title + " " + summary).lower()
        return any(kw in text for kw in LISTING_KEYWORDS)

    async def _fetch_feed(self) -> list:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    COINBASE_RSS_URL,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"Coinbase RSS HTTP {resp.status}")
                        return []
                    content = await resp.text()

            feed = feedparser.parse(content)
            items = []
            for entry in feed.entries:
                items.append({
                    "id": entry.get("id", "") or entry.get("link", ""),
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "") or entry.get("description", ""),
                    "url": entry.get("link", ""),
                    "pubDate": entry.get("published", ""),
                })
            return items
        except aiohttp.ClientError as e:
            logger.error(f"Coinbase RSS 网络异常: {e}")
            return []
        except Exception as e:
            logger.error(f"Coinbase RSS 解析异常: {e}", exc_info=True)
            return []

    async def start(self):
        self._running = True
        logger.info("Coinbase 博客监听器启动")

        while self._running:
            try:
                items = await self._fetch_feed()

                if self._first_run:
                    for item in items:
                        item_id = item.get("id", "")
                        if item_id:
                            self._seen_ids[item_id] = int(time.time())
                    logger.info(f"Coinbase 初始化完成，已记录 {len(self._seen_ids)} 条历史基线")
                    self._first_run = False
                else:
                    new_count = 0
                    for item in items:
                        item_id = item.get("id", "")
                        title = item.get("title", "")
                        if not item_id or not title:
                            continue
                        if item_id in self._seen_ids:
                            continue

                        self._seen_ids[item_id] = int(time.time())

                        if not self._is_relevant(title, item.get("summary", "")):
                            continue

                        if deduplicator.is_duplicate(title, source="coinbase"):
                            logger.info(f"Coinbase 重复文章已过滤: {title[:80]}")
                            continue

                        logger.info(f"新 Coinbase 博客: {title}")
                        formatted_msg = format_coinbase_announcement(item)
                        await telegram_bot.send_news(formatted_msg, source="coinbase")
                        await send_resonance_alerts(title, "Coinbase")
                        new_count += 1

                    if new_count:
                        logger.info(f"本轮 Coinbase 新推送数: {new_count}")

                    if len(self._seen_ids) > 2000:
                        keys_to_remove = list(self._seen_ids.keys())[:-1000]
                        for k in keys_to_remove:
                            del self._seen_ids[k]

            except Exception as e:
                logger.error(f"Coinbase 博客处理异常: {e}", exc_info=True)

            await asyncio.sleep(COINBASE_POLL_INTERVAL_SECONDS)

    async def stop(self):
        self._running = False
        logger.info("Coinbase 博客监听器已停止")


coinbase_monitor = CoinbaseMonitor()
