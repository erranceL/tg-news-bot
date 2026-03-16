"""
配置文件 - Telegram 新闻转发 Bot
"""

# ==================== Telegram Bot 配置 ====================
TELEGRAM_BOT_TOKEN = "8665632132:AAF0OXX0XCXxcpu-43zOhv__Qx2_Als6_zg"

# 目标频道/群组 Chat ID（Bot 启动后通过 /start 命令自动获取）
# 也可以手动设置，例如频道 "@your_channel" 或群组 ID "-100xxxxxxxxxx"
TELEGRAM_CHAT_IDS = []

# ==================== 币安 CMS WebSocket 配置 ====================
BINANCE_CMS_WS_URL = "wss://api.binance.com/sapi/wss"
BINANCE_CMS_TOPIC = "com_announcement_en"

# 币安 API Key 和 Secret Key（用于 CMS WebSocket 签名认证）
# 请在此处填入您的币安 API Key 和 Secret Key
BINANCE_API_KEY = ""
BINANCE_API_SECRET = ""

# ==================== 币安行情 WebSocket 配置 ====================
BINANCE_STREAM_URL = "wss://stream.binance.com:9443"
BINANCE_REST_URL = "https://api.binance.com"

# 价格波动提醒配置
PRICE_ALERT_WINDOW_MINUTES = 5       # 监控时间窗口（分钟）
PRICE_ALERT_THRESHOLD_PERCENT = 15   # 波动阈值百分比

# ==================== OKX 公告 API 配置 ====================
OKX_API_BASE_URL = "https://www.okx.com"
OKX_ANNOUNCEMENT_PATH = "/api/v5/support/announcements"
OKX_ANNOUNCEMENT_TYPES_PATH = "/api/v5/support/announcement-types"
OKX_POLL_INTERVAL_SECONDS = 10  # 轮询间隔（秒）

# ==================== 方程式新闻 WebSocket 配置 ====================
BWE_WS_URL = "wss://bwenews-api.bwe-ws.com/ws"

# ==================== 去重配置 ====================
DEDUP_CACHE_MAX_SIZE = 10000    # 去重缓存最大条目数
DEDUP_CACHE_TTL_HOURS = 24      # 去重缓存过期时间（小时）

# ==================== 日志配置 ====================
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

# ==================== 重连配置 ====================
WS_RECONNECT_DELAY_SECONDS = 5     # WebSocket 重连延迟（秒）
WS_RECONNECT_MAX_DELAY_SECONDS = 60  # 最大重连延迟（秒）
