"""
OKX 公告监听模块
通过 REST API 轮询 OKX 上币、下架等公告
"""

import asyncio
import logging
import time
import aiohttp
from collections import OrderedDict

from src.config import (
    OKX_API_BASE_URL, OKX_ANNOUNCEMENT_PATH,
    OKX_POLL_INTERVAL_SECONDS
)
from src.dedup import deduplicator
from src.formatter import format_okx_announcement
from src.telegram_bot import telegram_bot
from src.resonance_detector import send_resonance_alerts

logger = logging.getLogger("okx_announcements")

LISTING_KEYWORDS = [
    "listing", "list", "delist", "上币", "下架", "上线", "下线",
    "launch", "remove", "suspend", "resume",
    "token", "trading pair", "交易对"
]

RECENT_WINDOW_DAYS = 7
RECENT_WINDOW_MS = RECENT_WINDOW_DAYS * 24 * 60 * 60 * 1000


class OKXAnnouncementMonitor:
    """OKX 公告监听器"""

    def __init__(self):
        self._running = False
        self._session: aiohttp.ClientSession | None = None
        self._last_seen_ids: OrderedDict[str, int] = OrderedDict()
        self._first_run = True

    def _is_relevant_announcement(self, title: str, ann_type: str = "") -> bool:
        text = (title + " " + ann_type).lower()
        return any(kw in text for kw in LISTING_KEYWORDS)

    def _announcement_id(self, ann: dict) -> str:
        title = ann.get("title", "")
        p_time = ann.get("pTime", "")
        url = ann.get("url", "")
        return f"{p_time}_{title}_{url}"

    def _announcement_time_ms(self, ann: dict) -> int:
        p_time = ann.get("pTime", 0)
        try:
            return int(p_time)
        except (ValueError, TypeError):
            return 0

    def _is_recent(self, ann: dict) -> bool:
        ts = self._announcement_time_ms(ann)
        if ts <= 0:
            return False
        return ts >= int(time.time() * 1000) - RECENT_WINDOW_MS

    async def _fetch_announcements(self) -> list:
        """获取 OKX 最新公告列表，仅取第一页最新数据"""
        url = f"{OKX_API_BASE_URL}{OKX_ANNOUNCEMENT_PATH}"
        headers = {
            "Accept-Language": "en-US",
            "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
        }
        params = {
            "page": 1,
            "perPage": 20,
        }

        try:
            async with self._session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.error(f"OKX API 请求失败: HTTP {resp.status}")
                    return []

                data = await resp.json()
                code = data.get("code", "")
                if code != "0":
                    logger.error(f"OKX API 返回错误: code={code}, msg={data.get('msg', '')}")
                    return []

                details = data.get("data", [])
                announcements = []
                for item in details:
                    if isinstance(item, dict) and "details" in item:
                        announcements.extend(item["details"])
                    elif isinstance(item, dict):
                        announcements.append(item)

                announcements = [a for a in announcements if self._is_relevant_announcement(a.get("title", ""), a.get("annType", ""))]
                announcements.sort(key=self._announcement_time_ms, reverse=True)
                return announcements

        except aiohttp.ClientError as e:
            logger.error(f"OKX API 网络请求异常: {e}")
            return []
        except Exception as e:
            logger.error(f"OKX API 请求异常: {e}", exc_info=True)
            return []

    async def _process_announcements(self, announcements: list):
        """处理公告列表：首次仅建立基线；后续仅推送新增且近7天公告"""
        if self._first_run:
            for ann in announcements:
                self._last_seen_ids[self._announcement_id(ann)] = int(time.time())
            logger.info(f"OKX 初始化完成，已记录 {len(self._last_seen_ids)} 条历史公告基线，不推送历史消息")
            self._first_run = False
            return

        new_count = 0
        for ann in sorted(announcements, key=self._announcement_time_ms):
            ann_id = self._announcement_id(ann)
            title = ann.get("title", "")
            ann_type = ann.get("annType", "")

            if ann_id in self._last_seen_ids:
                continue

            self._last_seen_ids[ann_id] = int(time.time())

            if not self._is_recent(ann):
                logger.info(f"跳过过旧 OKX 公告（超过{RECENT_WINDOW_DAYS}天）: {title[:80]}")
                continue

            if deduplicator.is_duplicate(title, source="okx"):
                logger.info(f"OKX 重复公告已过滤: {title[:80]}")
                continue

            logger.info(f"新 OKX 公告: [{ann_type}] {title}")
            formatted_msg = format_okx_announcement(ann)
            await telegram_bot.send_news(formatted_msg, source="okx")
            await send_resonance_alerts(title, "OKX")
            new_count += 1

        if new_count:
            logger.info(f"本轮 OKX 新推送公告数: {new_count}")

        if len(self._last_seen_ids) > 5000:
            keys_to_remove = list(self._last_seen_ids.keys())[:-3000]
            for k in keys_to_remove:
                del self._last_seen_ids[k]

    async def start(self):
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("OKX 公告监听器启动")

        try:
            while self._running:
                try:
                    announcements = await self._fetch_announcements()
                    if announcements:
                        await self._process_announcements(announcements)
                except Exception as e:
                    logger.error(f"OKX 公告处理异常: {e}", exc_info=True)

                await asyncio.sleep(OKX_POLL_INTERVAL_SECONDS)
        finally:
            if self._session:
                await self._session.close()

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
        logger.info("OKX 公告监听器已停止")


okx_monitor = OKXAnnouncementMonitor()
