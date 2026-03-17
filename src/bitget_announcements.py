"""
Bitget 公告监听模块
通过 Bitget 官方 REST API 轮询上币、下架等公告
"""

import asyncio
import logging
import time
import aiohttp
from collections import OrderedDict

from src.config import BITGET_POLL_INTERVAL_SECONDS
from src.dedup import deduplicator
from src.formatter import format_bitget_announcement
from src.telegram_bot import telegram_bot
from src.resonance_detector import send_resonance_alerts

logger = logging.getLogger("bitget_announcements")

BITGET_ANNOUNCEMENT_API = "https://www.bitget.com/v1/cms/article/list/query"

LISTING_KEYWORDS = {
    "listing", "listed", "launchpad", "launchpool", "new pair",
    "delisting", "delist", "futures", "options", "perpetual",
    "上币", "下架", "上线", "新币"
}

RECENT_WINDOW_DAYS = 7
RECENT_WINDOW_MS = RECENT_WINDOW_DAYS * 24 * 60 * 60 * 1000

# Bitget 公告分类 ID：上新币 = 1, 下架 = 2, 活动 = 3
BITGET_CATALOG_IDS = [1, 2]


class BitgetAnnouncementMonitor:
    """Bitget 公告监听器"""

    def __init__(self):
        self._running = False
        self._session: aiohttp.ClientSession | None = None
        self._seen_ids: OrderedDict[str, int] = OrderedDict()
        self._first_run = True

    def _is_relevant(self, title: str) -> bool:
        return any(kw in title.lower() for kw in LISTING_KEYWORDS)

    def _ann_time_ms(self, ann: dict) -> int:
        for field in ("ctime", "publishTime", "releaseTime"):
            val = ann.get(field)
            if val:
                try:
                    return int(val)
                except (ValueError, TypeError):
                    pass
        return 0

    def _is_recent(self, ann: dict) -> bool:
        ts = self._ann_time_ms(ann)
        return ts > 0 and ts >= int(time.time() * 1000) - RECENT_WINDOW_MS

    async def _fetch_catalog(self, catalog_id: int) -> list:
        params = {
            "catalogId": catalog_id,
            "pageNo": 1,
            "pageSize": 20,
            "languageType": "en_US",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)",
            "Accept": "application/json",
        }
        try:
            async with self._session.get(
                BITGET_ANNOUNCEMENT_API,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Bitget API HTTP {resp.status} (catalogId={catalog_id})")
                    return []
                data = await resp.json()
                code = data.get("code", "")
                if str(code) not in ("0", "00000"):
                    logger.error(f"Bitget API 错误: code={code}, msg={data.get('msg', '')}")
                    return []
                articles = data.get("data", {}).get("list", []) or data.get("data", []) or []
                return articles
        except aiohttp.ClientError as e:
            logger.error(f"Bitget API 网络异常: {e}")
            return []
        except Exception as e:
            logger.error(f"Bitget API 请求异常: {e}", exc_info=True)
            return []

    async def start(self):
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("Bitget 公告监听器启动")

        try:
            while self._running:
                try:
                    all_articles = []
                    for catalog_id in BITGET_CATALOG_IDS:
                        articles = await self._fetch_catalog(catalog_id)
                        all_articles.extend(articles)

                    all_articles.sort(key=self._ann_time_ms)

                    if self._first_run:
                        for ann in all_articles:
                            ann_id = str(ann.get("id", "") or ann.get("articleId", ""))
                            if ann_id:
                                self._seen_ids[ann_id] = int(time.time())
                        logger.info(f"Bitget 初始化完成，已记录 {len(self._seen_ids)} 条历史公告基线")
                        self._first_run = False
                    else:
                        new_count = 0
                        for ann in all_articles:
                            ann_id = str(ann.get("id", "") or ann.get("articleId", ""))
                            title = ann.get("title", "")
                            if not ann_id or not title:
                                continue
                            if ann_id in self._seen_ids:
                                continue

                            self._seen_ids[ann_id] = int(time.time())

                            if not self._is_relevant(title):
                                continue

                            if not self._is_recent(ann):
                                logger.info(f"跳过过旧 Bitget 公告: {title[:80]}")
                                continue

                            if deduplicator.is_duplicate(title, source="bitget"):
                                logger.info(f"Bitget 重复公告已过滤: {title[:80]}")
                                continue

                            logger.info(f"新 Bitget 公告: {title}")
                            url = ann.get("url", "") or f"https://www.bitget.com/support/articles/{ann_id}"
                            formatted_msg = format_bitget_announcement({
                                "title": title,
                                "description": ann.get("summary", "") or ann.get("description", ""),
                                "url": url,
                                "ctime": self._ann_time_ms(ann),
                                "annType": ann.get("catalogName", ""),
                            })
                            await telegram_bot.send_news(formatted_msg, source="bitget")
                            await send_resonance_alerts(title, "Bitget")
                            new_count += 1

                        if new_count:
                            logger.info(f"本轮 Bitget 新推送公告数: {new_count}")

                        if len(self._seen_ids) > 5000:
                            keys_to_remove = list(self._seen_ids.keys())[:-3000]
                            for k in keys_to_remove:
                                del self._seen_ids[k]

                except Exception as e:
                    logger.error(f"Bitget 公告处理异常: {e}", exc_info=True)

                await asyncio.sleep(BITGET_POLL_INTERVAL_SECONDS)
        finally:
            if self._session:
                await self._session.close()

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
        logger.info("Bitget 公告监听器已停止")


bitget_monitor = BitgetAnnouncementMonitor()
