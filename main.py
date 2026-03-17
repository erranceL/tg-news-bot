"""
Telegram 新闻转发 Bot - 主入口
集成币安/OKX/Bybit/Bitget/Coinbase 公告监听、Polymarket、方程式新闻、价格波动提醒
"""

import asyncio
import signal
import logging
import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 自动加载 .env 文件（本地开发用；Railway 等云平台直接读系统环境变量，无需此步骤）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.config import LOG_LEVEL, LOG_FORMAT
from src.telegram_bot import telegram_bot
from src.binance_cms import binance_cms_monitor
from src.okx_announcements import okx_monitor
from src.bwe_news import bwe_monitor
from src.price_monitor import price_monitor
from src.bybit_announcements import bybit_monitor
from src.bitget_announcements import bitget_monitor
from src.coinbase_monitor import coinbase_monitor
from src.polymarket_monitor import polymarket_monitor

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("main")

# 降低第三方库日志级别
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)


async def startup_notification():
    """发送启动通知"""
    await asyncio.sleep(3)
    startup_msg = (
        "🟢 <b>新闻转发 Bot 已上线</b>\n\n"
        "📡 <b>监听中的新闻源:</b>\n"
        "  • 币安公告 (HTTP 轮询)\n"
        "  • OKX 公告 (REST API 轮询)\n"
        "  • Bybit 公告 (REST API 轮询)\n"
        "  • Bitget 公告 (REST API 轮询)\n"
        "  • Coinbase 博客 (RSS 轮询)\n"
        "  • Polymarket 预测市场 (REST API)\n"
        "  • 方程式新闻 (BWE WebSocket + RSS)\n"
        "  • 币安价格波动监控 (5分钟/15%)\n"
        "  • 多源共振检测器\n\n"
        "✅ 所有模块已启动，新闻去重过滤已开启。\n"
        "发送 /help 查看可用命令。"
    )
    await telegram_bot.send_news(startup_msg, source="system")


async def _run_with_restart(name: str, coro_factory, stop_event: asyncio.Event):
    """包装协程：崩溃后自动重启，直到 stop_event 被设置"""
    delay = 5
    max_delay = 60
    while not stop_event.is_set():
        try:
            logger.info(f"[{name}] 启动中...")
            await coro_factory()
            if not stop_event.is_set():
                logger.warning(f"[{name}] 意外正常退出，将在 {delay} 秒后重启")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if stop_event.is_set():
                break
            logger.error(f"[{name}] 异常退出: {e}，将在 {delay} 秒后重启", exc_info=True)

        if stop_event.is_set():
            break
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)


async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("  Telegram 新闻转发 Bot 启动中...")
    logger.info("=" * 60)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown(sig_name: str):
        logger.info(f"收到信号 {sig_name}，准备关闭...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: _request_shutdown(s.name))
        except NotImplementedError:
            pass

    try:
        await telegram_bot.setup()
        logger.info("✅ Telegram Bot 已启动")
    except Exception as e:
        logger.error(f"❌ Telegram Bot 启动失败: {e}", exc_info=True)
        return

    monitors = [
        ("binance_cms",    lambda: binance_cms_monitor.start()),
        ("okx",            lambda: okx_monitor.start()),
        ("bybit",          lambda: bybit_monitor.start()),
        ("bitget",         lambda: bitget_monitor.start()),
        ("coinbase",       lambda: coinbase_monitor.start()),
        ("polymarket",     lambda: polymarket_monitor.start()),
        ("bwe_news",       lambda: bwe_monitor.start()),
        ("price_monitor",  lambda: price_monitor.start()),
    ]

    tasks = [
        asyncio.create_task(
            _run_with_restart(name, factory, stop_event), name=name
        )
        for name, factory in monitors
    ]

    startup_task = asyncio.create_task(startup_notification(), name="startup_notification")

    logger.info("=" * 60)
    logger.info("  所有模块已启动，Bot 正在运行...")
    logger.info("=" * 60)

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.info("收到取消信号")
    finally:
        logger.info("正在关闭所有模块...")

        binance_cms_monitor._running = False
        okx_monitor._running = False
        bybit_monitor._running = False
        bitget_monitor._running = False
        coinbase_monitor._running = False
        polymarket_monitor._running = False
        bwe_monitor._running = False
        price_monitor._running = False

        for task in tasks + [startup_task]:
            if not task.done():
                task.cancel()

        await asyncio.gather(*tasks, startup_task, return_exceptions=True)

        try:
            await telegram_bot.shutdown()
        except Exception as e:
            logger.warning(f"关闭 Telegram Bot 异常: {e}")

        logger.info("Bot 已完全关闭")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("用户中断，Bot 已停止")
    except Exception as e:
        logger.error(f"Bot 异常退出: {e}", exc_info=True)
        sys.exit(1)
