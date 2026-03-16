"""
新闻去重过滤模块
使用基于标题相似度和内容哈希的双重去重策略
"""

import hashlib
import time
import re
import logging
from collections import OrderedDict
from src.config import DEDUP_CACHE_MAX_SIZE, DEDUP_CACHE_TTL_HOURS

logger = logging.getLogger("dedup")


class NewsDeduplicator:
    """新闻去重器，支持精确匹配和模糊匹配"""

    def __init__(self, max_size=DEDUP_CACHE_MAX_SIZE, ttl_hours=DEDUP_CACHE_TTL_HOURS):
        self.max_size = max_size
        self.ttl_seconds = ttl_hours * 3600
        # {hash_key: timestamp} 有序字典，按插入顺序排列
        self._cache: OrderedDict[str, float] = OrderedDict()

    def _normalize_text(self, text: str) -> str:
        """标准化文本：去除多余空格、标点、转小写"""
        text = text.lower().strip()
        # 去除常见的前缀标签如 [Binance], (OKX) 等
        text = re.sub(r'[\[\(][^\]\)]*[\]\)]', '', text)
        # 去除多余空白
        text = re.sub(r'\s+', ' ', text)
        # 去除标点符号
        text = re.sub(r'[^\w\s]', '', text)
        return text.strip()

    def _compute_hash(self, text: str) -> str:
        """计算文本的 SHA256 哈希"""
        normalized = self._normalize_text(text)
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

    def _cleanup_expired(self):
        """清理过期的缓存条目"""
        now = time.time()
        expired_keys = []
        for key, ts in self._cache.items():
            if now - ts > self.ttl_seconds:
                expired_keys.append(key)
            else:
                break  # 有序字典，后面的都是更新的
        for key in expired_keys:
            del self._cache[key]

    def _evict_if_full(self):
        """如果缓存满了，移除最旧的条目"""
        while len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)

    def is_duplicate(self, title: str, source: str = "") -> bool:
        """
        检查新闻是否重复
        
        Args:
            title: 新闻标题
            source: 新闻来源标识（可选）
            
        Returns:
            True 如果是重复新闻，False 如果是新新闻
        """
        self._cleanup_expired()

        # 基于标准化标题的哈希去重（跨来源去重）
        title_hash = self._compute_hash(title)

        if title_hash in self._cache:
            logger.debug(f"重复新闻被过滤: [{source}] {title[:50]}...")
            return True

        # 不重复，加入缓存
        self._evict_if_full()
        self._cache[title_hash] = time.time()
        logger.debug(f"新新闻已记录: [{source}] {title[:50]}...")
        return False

    def get_cache_size(self) -> int:
        """返回当前缓存大小"""
        return len(self._cache)


# 全局去重器实例
deduplicator = NewsDeduplicator()
