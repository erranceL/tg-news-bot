"""
OKX 公告监听模块
通过 REST API 轮询 OKX 上币、下架等公告
"""

import asyncio
import json
import logging
import aiohttp

from src.config import (
    OKX_API_BASE_URL, OKX_ANNOUNCEMENT_PATH,
    OKX_ANNOUNCEMENT_TYPES_PATH, OKX_POLL_INTERVAL_SECONDS
)
from src.dedup import deduplicator
from src.formatter import format_okx_announcement
from src.telegram_bot import telegram_bot

logger = logging.getLogger("okx_announcements")

# OKX 公告类型中与上币/下架相关的关键词
LISTING_KEYWORDS = [
    "listing", "list", "delist", "上币", "下架", "上线", "下线",
    "new", "launch", "remove", "suspend", "resume",
    "token", "trading pair", "交易对"
]


class OKXAnnouncementMonitor:
    """OKX 公告监听器"""

    def __init__(self):
        self._running = False
        self._session: aiohttp.ClientSession = None
        self._last_seen_ids: set = set()
        self._first_run = True

    def _is_relevant_announcement(self, title: str, ann_type: str = "") -> bool:
        """判断公告是否与上币/下架相关"""
        text = (title + " " + ann_type).lower()
        return any(kw in text for kw in LISTING_KEYWORDS)

    async def _fetch_announcements(self) -> list:
        """获取 OKX 最新公告列表"""
        url = f"{OKX_API_BASE_URL}{OKX_ANNOUNCEMENT_PATH}"
        headers = {
            "Accept-Language": "en-US",
            "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
        }

        try:
            async with self._session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
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

                return announcements

        except aiohttp.ClientError as e:
            logger.error(f"OKX API 网络请求异常: {e}")
            return []
        except Exception as e:
            logger.error(f"OKX API 请求异常: {e}", exc_info=True)
            return []

    async def _process_announcements(self, announcements: list):
        """处理公告列表"""
        for ann in announcements:
            title = ann.get("title", "")
            ann_type = ann.get("annType", "")
            p_time = ann.get("pTime", "")

            # 使用 pTime + title 作为唯一标识
            ann_id = f"{p_time}_{title}"

            # 首次运行时只记录已有公告，不发送
            if self._first_run:
                self._last_seen_ids.add(ann_id)
                continue

            # 检查是否已处理过
            if ann_id in self._last_seen_ids:
                continue

            self._last_seen_ids.add(ann_id)

            # 去重检查（跨来源）
            if deduplicator.is_duplicate(title, source="okx"):
                logger.info(f"OKX 重复公告已过滤: {title[:50]}")
                continue

            logger.info(f"新 OKX 公告: [{ann_type}] {title}")

            # 格式化并发送
            formatted_msg = format_okx_announcement(ann)
            await telegram_bot.send_news(formatted_msg, source="okx")

        # 限制缓存大小
        if len(self._last_seen_ids) > 5000:
            # 保留最新的 3000 条
            self._last_seen_ids = set(list(self._last_seen_ids)[-3000:])

    async def start(self):
        """启动 OKX 公告轮询"""
        self._running = True
        self._session = aiohttp.ClientSession()
        logger.info("OKX 公告监听器启动")

        try:
            while self._running:
                try:
                    announcements = await self._fetch_announcements()
                    if announcements:
                        await self._process_announcements(announcements)
                        if self._first_run:
                            self._first_run = False
                            logger.info(f"OKX 初始化完成，已记录 {len(self._last_seen_ids)} 条历史公告")
                except Exception as e:
                    logger.error(f"OKX 公告处理异常: {e}", exc_info=True)

                await asyncio.sleep(OKX_POLL_INTERVAL_SECONDS)

        finally:
            if self._session:
                await self._session.close()

    async def stop(self):
        """停止轮询"""
        self._running = False
        if self._session:
            await self._session.close()
        logger.info("OKX 公告监听器已停止")


# 全局实例
okx_monitor = OKXAnnouncementMonitor()
