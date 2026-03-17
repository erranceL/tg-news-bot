"""
多源共振检测器
在 5 分钟时间窗口内，同一 token/关键词被 2 个以上不同来源提及时，触发共振预警
"""

import re
import time
import logging
import asyncio
from collections import defaultdict

logger = logging.getLogger("resonance_detector")

RESONANCE_WINDOW_SECONDS = 300  # 5 分钟窗口
RESONANCE_THRESHOLD = 2          # 至少 2 个不同来源才触发
RESONANCE_COOLDOWN_SECONDS = 600 # 同一 token 10 分钟内不重复预警

# 提取 token 名称时的停用词（避免 BTC/ETH 这种高频词每次都触发）
TOKEN_STOPWORDS = {
    "BTC", "ETH", "USDT", "USD", "USDC", "BNB", "SOL",
    "THE", "AND", "NEW", "FOR", "WITH", "ARE", "ITS",
    "API", "CEO", "SEC", "ETF", "NFT",
}


class ResonanceDetector:
    """多源共振检测器"""

    def __init__(self):
        # {token: [(timestamp, source), ...]}
        self._mentions: dict[str, list] = defaultdict(list)
        # {token: last_alert_timestamp}
        self._alerted: dict[str, float] = {}

    def _extract_tokens(self, text: str) -> list[str]:
        """从文本中提取大写 token 候选词"""
        candidates = re.findall(r"\b[A-Z0-9]{2,10}\b", text.upper())
        return [c for c in candidates if c not in TOKEN_STOPWORDS]

    def record(self, text: str, source: str):
        """记录一条新闻来源的提及，并返回是否触发了共振（内部使用）"""
        tokens = self._extract_tokens(text)
        now = time.time()

        for token in tokens:
            # 清理过期记录
            self._mentions[token] = [
                (ts, src) for ts, src in self._mentions[token]
                if now - ts <= RESONANCE_WINDOW_SECONDS
            ]
            # 加入本次记录（同一来源在窗口内去重）
            existing_sources = {src for _, src in self._mentions[token]}
            if source not in existing_sources:
                self._mentions[token].append((now, source))

    def check_and_alert(self, text: str, source: str) -> list[tuple[str, list[str]]]:
        """
        记录提及并检查是否需要触发共振预警。
        返回需要预警的 [(token, [sources, ...]), ...] 列表。
        """
        tokens = self._extract_tokens(text)
        now = time.time()
        alerts = []

        for token in tokens:
            # 清理过期记录
            self._mentions[token] = [
                (ts, src) for ts, src in self._mentions[token]
                if now - ts <= RESONANCE_WINDOW_SECONDS
            ]

            existing_sources = {src for _, src in self._mentions[token]}
            if source not in existing_sources:
                self._mentions[token].append((now, source))

            all_sources = list({src for _, src in self._mentions[token]})

            if len(all_sources) >= RESONANCE_THRESHOLD:
                last_alert = self._alerted.get(token, 0)
                if now - last_alert >= RESONANCE_COOLDOWN_SECONDS:
                    self._alerted[token] = now
                    alerts.append((token, all_sources))

        return alerts

    def cleanup(self):
        """清理过期的 token 记录（定期调用）"""
        now = time.time()
        empty_tokens = []
        for token, mentions in self._mentions.items():
            valid = [(ts, src) for ts, src in mentions if now - ts <= RESONANCE_WINDOW_SECONDS]
            if valid:
                self._mentions[token] = valid
            else:
                empty_tokens.append(token)
        for token in empty_tokens:
            del self._mentions[token]

        expired_alerts = [t for t, ts in self._alerted.items() if now - ts > RESONANCE_COOLDOWN_SECONDS * 2]
        for token in expired_alerts:
            del self._alerted[token]


resonance_detector = ResonanceDetector()


async def send_resonance_alerts(text: str, source: str):
    """
    便捷函数：检查共振并自动发送预警消息。
    在各监控模块推送新闻前调用此函数。
    """
    from src.telegram_bot import telegram_bot
    from src.formatter import format_resonance_alert

    alerts = resonance_detector.check_and_alert(text, source)
    for token, sources in alerts:
        logger.warning(f"多重共振预警: {token} 被 {sources} 同时提及")
        msg = format_resonance_alert(token, sources)
        await telegram_bot.send_news(msg, source="resonance")
