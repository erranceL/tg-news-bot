"""
配置文件 - Telegram 新闻转发 Bot
所有敏感配置均从环境变量读取，不允许硬编码默认值
"""

import os
import sys

# ==================== Telegram Bot 配置 ====================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN:
    print("错误：环境变量 TELEGRAM_BOT_TOKEN 未设置，Bot 无法启动。", file=sys.stderr)
    sys.exit(1)

# 目标频道/群组 Chat ID
# 环境变量格式: 逗号分隔，例如 "2130253506,-1003500969046"
_chat_ids_str = os.environ.get("TELEGRAM_CHAT_IDS", "")
if not _chat_ids_str:
    print("错误：环境变量 TELEGRAM_CHAT_IDS 未设置，Bot 无法启动。", file=sys.stderr)
    sys.exit(1)
TELEGRAM_CHAT_IDS = [int(x.strip()) for x in _chat_ids_str.split(",") if x.strip()]

# ==================== 币安公告公开HTTP轮询配置 ====================
BINANCE_ANNOUNCEMENT_API = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
BINANCE_ANNOUNCEMENT_DETAIL_BASE = "https://www.binance.com/en/support/announcement/detail"
BINANCE_LISTING_CATALOG_ID = 48
BINANCE_DELISTING_CATALOG_ID = 161
BINANCE_POLL_INTERVAL_SECONDS = int(os.environ.get("BINANCE_POLL_INTERVAL", "30"))

# ==================== 币安行情 WebSocket 配置 ====================
BINANCE_STREAM_URL = "wss://stream.binance.com:9443"
BINANCE_REST_URL = "https://api.binance.com"

# 价格波动提醒配置
PRICE_ALERT_WINDOW_MINUTES = int(os.environ.get("PRICE_ALERT_WINDOW_MINUTES", "5"))
PRICE_ALERT_THRESHOLD_PERCENT = int(os.environ.get("PRICE_ALERT_THRESHOLD_PERCENT", "15"))

# ==================== OKX 公告 API 配置 ====================
OKX_API_BASE_URL = "https://www.okx.com"
OKX_ANNOUNCEMENT_PATH = "/api/v5/support/announcements"
OKX_ANNOUNCEMENT_TYPES_PATH = "/api/v5/support/announcement-types"
OKX_POLL_INTERVAL_SECONDS = int(os.environ.get("OKX_POLL_INTERVAL", "30"))

# ==================== Bybit 公告 API 配置 ====================
BYBIT_POLL_INTERVAL_SECONDS = int(os.environ.get("BYBIT_POLL_INTERVAL", "30"))

# ==================== Bitget 公告 API 配置 ====================
BITGET_POLL_INTERVAL_SECONDS = int(os.environ.get("BITGET_POLL_INTERVAL", "30"))

# ==================== Coinbase 博客 RSS 配置 ====================
COINBASE_POLL_INTERVAL_SECONDS = int(os.environ.get("COINBASE_POLL_INTERVAL", "60"))

# ==================== Polymarket 预测市场 API 配置 ====================
POLYMARKET_POLL_INTERVAL_SECONDS = int(os.environ.get("POLYMARKET_POLL_INTERVAL", "60"))

# ==================== 方程式新闻 WebSocket 配置 ====================
BWE_WS_URL = "wss://bwenews-api.bwe-ws.com/ws"

# ==================== 去重配置 ====================
DEDUP_CACHE_MAX_SIZE = 10000
DEDUP_CACHE_TTL_HOURS = 24

# ==================== 日志配置 ====================
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

# ==================== 重连配置 ====================
WS_RECONNECT_DELAY_SECONDS = 5
WS_RECONNECT_MAX_DELAY_SECONDS = 60
