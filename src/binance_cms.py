"""
币安公告公开 HTTP 轮询模块
使用币安公开 CMS 列表接口监听上币、下架公告
"""

import asyncio
import logging
import time
import aiohttp
from collections import OrderedDict

from src.config import (
    BINANCE_ANNOUNCEMENT_API,
    BINANCE_ANNOUNCEMENT_DETAIL_BASE,
    BINANCE_LISTING_CATALOG_ID,
    BINANCE_DELISTING_CATALOG_ID,
    BINANCE_LAUNCHPAD_CATALOG_ID,
    BINANCE_POLL_INTERVAL_SECONDS,
)
from src.dedup import deduplicator
from src.formatter import format_binance_announcement
from src.telegram_bot import telegram_bot
from src.resonance_detector import send_resonance_alerts

logger = logging.getLogger("binance_announcements")

RECENT_WINDOW_DAYS = 7
RECENT_WINDOW_MS = RECENT_WINDOW_DAYS * 24 * 60 * 60 * 1000


class BinanceCMSMonitor:
    """币安公告监听器（公开 HTTP 轮询）"""

    def __init__(self):
        self._running = False
        self._session: aiohttp.ClientSession | None = None
        self._seen_ids: OrderedDict[str, int] = OrderedDict()
        self._first_run = True

    def _article_time_ms(self, article: dict) -> int:
        try:
            return int(article.get("releaseDate") or article.get("publishDate") or 0)
        except (ValueError, TypeError):
            return 0

    def _is_recent(self, article: dict) -> bool:
        ts = self._article_time_ms(article)
        if ts <= 0:
            return False
        return ts >= int(time.time() * 1000) - RECENT_WINDOW_MS

    async def _fetch_catalog(self, catalog_id: int, page_size: int = 20) -> list[dict]:
        """获取指定分类下的最新公告列表"""
        params = {
            "catalogId": catalog_id,
            "pageNo": 1,
            "pageSize": page_size,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)",
            "Accept": "application/json, text/plain, */*",
        }

        async with self._session.get(
            BINANCE_ANNOUNCEMENT_API,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Binance 公告接口 HTTP {resp.status}")

            payload = await resp.json()
            if payload.get("code") != "000000":
                raise RuntimeError(
                    f"Binance 公告接口返回异常: {payload.get('code')} {payload.get('message')}"
                )

            articles = payload.get("data", {}).get("articles", [])
            articles.sort(key=self._article_time_ms, reverse=True)
            return articles

    def _build_article_url(self, article: dict) -> str:
        code = article.get("code", "")
        if not code:
            return ""
        return f"{BINANCE_ANNOUNCEMENT_DETAIL_BASE}/{code}"

    async def _process_articles(self, articles: list[dict], catalog_name: str):
        for article in sorted(articles, key=self._article_time_ms):
            article_id = str(article.get("id", ""))
            title = article.get("title", "")
            if not article_id or not title:
                continue

            unique_key = f"binance_{catalog_name}_{article_id}"
            if unique_key in self._seen_ids:
                continue

            self._seen_ids[unique_key] = int(time.time())

            if not self._is_recent(article):
                logger.info(f"跳过过旧币安公告（超过{RECENT_WINDOW_DAYS}天）: {title[:80]}")
                continue

            if deduplicator.is_duplicate(title, source="binance"):
                logger.info(f"币安重复公告已过滤: {title[:80]}")
                continue

            logger.info(f"新币安公告: [{catalog_name}] {title}")

            formatted_msg = format_binance_announcement({
                "catalogName": catalog_name,
                "title": title,
                "body": "",
                "publishDate": article.get("releaseDate") or article.get("publishDate") or 0,
                "url": self._build_article_url(article),
            })
            await telegram_bot.send_news(formatted_msg, source="binance")
            await send_resonance_alerts(title, "Binance")

        if len(self._seen_ids) > 5000:
            keys_to_remove = list(self._seen_ids.keys())[:-3000]
            for k in keys_to_remove:
                del self._seen_ids[k]

    async def start(self):
        """启动币安公告轮询"""
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("币安公告监听器启动（公开 HTTP 轮询）")

        try:
            while self._running:
                try:
                    catalogs = [
                        (BINANCE_LISTING_CATALOG_ID, "New Cryptocurrency Listing"),
                        (BINANCE_DELISTING_CATALOG_ID, "Delisting"),
                        (BINANCE_LAUNCHPAD_CATALOG_ID, "Launchpad/Launchpool"),
                    ]

                    all_fetched = {}
                    for catalog_id, catalog_name in catalogs:
                        articles = await self._fetch_catalog(catalog_id)
                        all_fetched[catalog_name] = articles

                    if self._first_run:
                        for catalog_name, articles in all_fetched.items():
                            for article in articles:
                                article_id = str(article.get("id", ""))
                                if article_id:
                                    self._seen_ids[f"binance_{catalog_name}_{article_id}"] = int(time.time())
                        self._first_run = False
                        logger.info(f"币安初始化完成，已记录 {len(self._seen_ids)} 条历史公告基线，不推送历史消息")
                    else:
                        for catalog_name, articles in all_fetched.items():
                            if articles:
                                await self._process_articles(articles, catalog_name)

                except Exception as e:
                    logger.error(f"币安公告轮询异常: {e}", exc_info=True)

                await asyncio.sleep(BINANCE_POLL_INTERVAL_SECONDS)
        finally:
            if self._session:
                await self._session.close()

    async def stop(self):
        """停止轮询"""
        self._running = False
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        logger.info("币安公告监听器已停止")


binance_cms_monitor = BinanceCMSMonitor()
