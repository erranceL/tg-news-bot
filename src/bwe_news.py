"""
方程式新闻 (BWEnews) 监听模块
- 主通道: WebSocket wss://bwenews-api.bwe-ws.com/ws
- 备份通道: RSS 轮询 https://rss-public.bwe-ws.com/
双通道同时运行，去重后推送，确保不漏消息。
"""

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp
import websockets

from src.config import (
    BWE_WS_URL,
    WS_RECONNECT_DELAY_SECONDS,
    WS_RECONNECT_MAX_DELAY_SECONDS,
)
from src.dedup import deduplicator
from src.formatter import format_bwe_news

logger = logging.getLogger("bwe_news")

# BWE RSS 地址
BWE_RSS_URL = "https://rss-public.bwe-ws.com/"
BWE_RSS_POLL_INTERVAL = 60  # RSS 轮询间隔（秒）


class BWENewsMonitor:
    """方程式新闻监听器：WebSocket + RSS 双通道"""

    def __init__(self):
        self._running = False
        self.ws = None
        self._reconnect_delay = WS_RECONNECT_DELAY_SECONDS
        self._heartbeat_task = None
        self._latest_file = Path("bwe_latest.json")
        # RSS 已见 ID 集合
        self._rss_seen_links: set = set()
        self._rss_first_run = True
        # 全局已推送标题集合（用于 WS 与 RSS 之间去重）
        self._pushed_titles: set = set()

    # ==================== 缓存接口 ====================

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
            self._latest_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"保存 BWE 本地缓存失败: {e}")

    def _normalize_title(self, title: str) -> str:
        """标准化标题用于去重比较"""
        title = re.sub(r"<[^>]+>", " ", title)
        title = re.sub(r"\s+", " ", title).strip()
        return title[:100].lower()

    # ==================== WebSocket 通道 ====================

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

    async def _handle_ws_message(self, message: str):
        """处理收到的 WebSocket 消息"""
        from src.telegram_bot import telegram_bot

        try:
            if isinstance(message, str) and message.strip().lower() == "pong":
                logger.debug("收到 BWE 心跳响应: pong")
                return

            logger.info(f"BWE WS 原始消息: {str(message)[:500]}")
            data = json.loads(message)
            news_title = data.get("news_title", "")
            if not news_title:
                logger.debug(f"BWE WS 非新闻 JSON 消息: {str(message)[:200]}")
                return

            source_name = data.get("source_name", "BWEnews")
            logger.info(f"收到方程式新闻(WS): [{source_name}] {news_title[:100]}")

            # 保存缓存
            self._save_latest_news(data)

            # 标题去重（跨通道）
            norm = self._normalize_title(news_title)
            if norm in self._pushed_titles:
                logger.info(f"BWE WS 跨通道重复，已跳过: {news_title[:80]}")
                return
            self._pushed_titles.add(norm)

            # 全局去重
            if deduplicator.is_duplicate(news_title, source="bwe"):
                logger.info(f"BWE WS 重复新闻已过滤: {news_title[:50]}")
                return

            formatted_msg = format_bwe_news(data)
            await telegram_bot.send_news(formatted_msg, source="bwe")

        except json.JSONDecodeError:
            logger.warning(f"BWE WS 消息解析失败（非 JSON）: {str(message)[:200]}")
        except Exception as e:
            logger.error(f"处理 BWE WS 消息异常: {e}", exc_info=True)

    async def _connect_and_listen(self):
        """连接并监听 WebSocket"""
        logger.info(f"正在连接方程式新闻 WebSocket: {BWE_WS_URL}")
        try:
            async with websockets.connect(
                BWE_WS_URL,
                ping_interval=20,
                ping_timeout=30,
                close_timeout=5,
                additional_headers={
                    "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
                },
            ) as ws:
                self.ws = ws
                self._reconnect_delay = WS_RECONNECT_DELAY_SECONDS
                logger.info("方程式新闻 WebSocket 已连接")

                # 启动文本心跳
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                try:
                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_ws_message(message)
                finally:
                    if self._heartbeat_task:
                        self._heartbeat_task.cancel()
                        try:
                            await self._heartbeat_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        self._heartbeat_task = None
        except Exception as e:
            logger.warning(f"BWE WebSocket 连接异常: {e}")
            raise

    async def _ws_loop(self):
        """WebSocket 主循环（带自动重连）"""
        while self._running:
            try:
                await self._connect_and_listen()
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"BWE WebSocket 连接关闭: {e}")
            except websockets.exceptions.InvalidStatusCode as e:
                logger.error(f"BWE WebSocket 连接被拒绝: {e}")
            except ConnectionRefusedError as e:
                logger.error(f"BWE WebSocket 连接被拒绝: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"BWE WebSocket 异常: {e}", exc_info=True)

            if self._running:
                logger.info(f"将在 {self._reconnect_delay} 秒后重连 BWE WebSocket...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, WS_RECONNECT_MAX_DELAY_SECONDS
                )

    # ==================== RSS 备份通道 ====================

    def _parse_rss_items(self, xml_text: str) -> list:
        """解析 RSS XML 返回 item 列表"""
        items = []
        try:
            root = ET.fromstring(xml_text)
            channel = root.find("channel")
            if channel is None:
                return items
            for item_el in channel.findall("item"):
                title_el = item_el.find("title")
                link_el = item_el.find("link")
                pub_date_el = item_el.find("pubDate")
                title = title_el.text if title_el is not None and title_el.text else ""
                link = link_el.text if link_el is not None and link_el.text else ""
                pub_date = (
                    pub_date_el.text
                    if pub_date_el is not None and pub_date_el.text
                    else ""
                )
                if title:
                    items.append(
                        {"title": title, "link": link, "pubDate": pub_date}
                    )
        except ET.ParseError as e:
            logger.error(f"BWE RSS XML 解析失败: {e}")
        return items

    def _parse_pub_date(self, pub_date_str: str):
        """解析 RSS pubDate 字符串"""
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(pub_date_str.strip(), fmt)
            except ValueError:
                continue
        return None

    def _is_recent_rss(self, pub_date_str: str, window_days: int = 7) -> bool:
        """判断 RSS item 是否在近 N 天内"""
        dt = self._parse_pub_date(pub_date_str)
        if dt is None:
            return True  # 无法解析时默认视为近期
        now = datetime.now(timezone.utc)
        return (now - dt) < timedelta(days=window_days)

    def _clean_rss_title(self, raw_title: str) -> str:
        """清理 RSS 标题中的 HTML 标签"""
        cleaned = re.sub(r"<br\s*/?>", "\n", raw_title)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    async def _rss_loop(self):
        """RSS 轮询主循环"""
        logger.info(f"BWE RSS 备份通道启动，轮询间隔 {BWE_RSS_POLL_INTERVAL} 秒")
        from src.telegram_bot import telegram_bot

        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)",
                        "Accept": "application/xml, text/xml, */*",
                    }
                    async with session.get(
                        BWE_RSS_URL,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(f"BWE RSS HTTP {resp.status}")
                            await asyncio.sleep(BWE_RSS_POLL_INTERVAL)
                            continue
                        xml_text = await resp.text()

                    items = self._parse_rss_items(xml_text)
                    if not items:
                        logger.debug("BWE RSS 返回空列表")
                        await asyncio.sleep(BWE_RSS_POLL_INTERVAL)
                        continue

                    if self._rss_first_run:
                        # 首次运行：只记录基线，不推送
                        for item in items:
                            link = item.get("link", "")
                            if link:
                                self._rss_seen_links.add(link)
                            norm = self._normalize_title(item.get("title", ""))
                            if norm:
                                self._pushed_titles.add(norm)
                        self._rss_first_run = False
                        logger.info(
                            f"BWE RSS 初始化完成，已记录 {len(self._rss_seen_links)} 条历史基线"
                        )
                        await asyncio.sleep(BWE_RSS_POLL_INTERVAL)
                        continue

                    # 检测新 item
                    new_items = []
                    for item in items:
                        link = item.get("link", "")
                        if link and link in self._rss_seen_links:
                            continue
                        if link:
                            self._rss_seen_links.add(link)
                        # 检查是否近期
                        pub_date = item.get("pubDate", "")
                        if pub_date and not self._is_recent_rss(pub_date):
                            continue
                        new_items.append(item)

                    # 按时间正序发送
                    new_items.reverse()

                    for item in new_items:
                        title = item.get("title", "")
                        link = item.get("link", "")
                        pub_date = item.get("pubDate", "")

                        # 跨通道去重
                        norm = self._normalize_title(title)
                        if norm in self._pushed_titles:
                            logger.debug(f"BWE RSS 跨通道重复，已跳过: {title[:80]}")
                            continue
                        self._pushed_titles.add(norm)

                        # 全局去重
                        cleaned_title = self._clean_rss_title(title)
                        if deduplicator.is_duplicate(cleaned_title, source="bwe_rss"):
                            logger.info(f"BWE RSS 重复新闻已过滤: {cleaned_title[:80]}")
                            continue

                        logger.info(f"新方程式新闻(RSS): {cleaned_title[:100]}")

                        # 构造与 WS 兼容的数据结构
                        data = {
                            "news_title": cleaned_title,
                            "source_name": "BWEnews (RSS)",
                            "url": link,
                            "pubDate": pub_date,
                        }
                        self._save_latest_news(data)

                        formatted_msg = format_bwe_news(data)
                        await telegram_bot.send_news(formatted_msg, source="bwe_rss")

                    # 清理过大的已见集合
                    if len(self._rss_seen_links) > 5000:
                        self._rss_seen_links = set(
                            list(self._rss_seen_links)[-3000:]
                        )
                    if len(self._pushed_titles) > 10000:
                        self._pushed_titles = set(
                            list(self._pushed_titles)[-5000:]
                        )

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"BWE RSS 轮询异常: {e}", exc_info=True)

                await asyncio.sleep(BWE_RSS_POLL_INTERVAL)

    # ==================== 生命周期 ====================

    async def start(self):
        """启动双通道监听（WebSocket + RSS）"""
        self._running = True
        logger.info("方程式新闻监听器启动（WebSocket + RSS 双通道）")

        # 同时启动 WS 和 RSS 两个协程
        ws_task = asyncio.create_task(self._ws_loop())
        rss_task = asyncio.create_task(self._rss_loop())

        try:
            done, pending = await asyncio.wait(
                [ws_task, rss_task], return_when=asyncio.FIRST_EXCEPTION
            )
            for task in done:
                exc = task.exception()
                if exc:
                    logger.error(f"BWE 子任务异常退出: {exc}", exc_info=True)
            # 如果一个退出了，继续等另一个
            if pending:
                await asyncio.wait(pending)
        except asyncio.CancelledError:
            ws_task.cancel()
            rss_task.cancel()
            await asyncio.gather(ws_task, rss_task, return_exceptions=True)
            raise

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
