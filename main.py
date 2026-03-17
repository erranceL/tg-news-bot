"""
Telegram 新闻转发 Bot - 主入口
集成币安/OKX公告监听、方程式新闻、价格波动提醒
"""

import asyncio
import signal
import logging
import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import LOG_LEVEL, LOG_FORMAT
from src.telegram_bot import telegram_bot
from src.binance_cms import binance_cms_monitor
from src.okx_announcements import okx_monitor
from src.bwe_news import bwe_monitor
from src.price_monitor import price_monitor

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
        "  • 币安公告 (公开 HTTP 轮询)\n"
        "  • OKX 公告 (REST API 轮询)\n"
        "  • 方程式新闻 (BWE WebSocket)\n"
        "  • 币安价格波动监控 (5分钟/15%)\n\n"
        "✅ 所有模块已启动，新闻去重过滤已开启。\n"
        "发送 /help 查看可用命令。"
    )
    await telegram_bot.send_news(startup_msg, source="system")


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

    tasks = []

    task_binance_cms = asyncio.create_task(binance_cms_monitor.start(), name="binance_cms")
    tasks.append(task_binance_cms)
    logger.info("✅ 币安 CMS 公告监听已启动")

    task_okx = asyncio.create_task(okx_monitor.start(), name="okx_announcements")
    tasks.append(task_okx)
    logger.info("✅ OKX 公告监听已启动")

    task_bwe = asyncio.create_task(bwe_monitor.start(), name="bwe_news")
    tasks.append(task_bwe)
    logger.info("✅ 方程式新闻监听已启动")

    task_price = asyncio.create_task(price_monitor.start(), name="price_monitor")
    tasks.append(task_price)
    logger.info("✅ 价格波动监控已启动")

    startup_task = asyncio.create_task(startup_notification(), name="startup_notification")

    logger.info("=" * 60)
    logger.info("  所有模块已启动，Bot 正在运行...")
    logger.info("=" * 60)

    stop_wait_task = asyncio.create_task(stop_event.wait(), name="stop_wait")

    try:
        while not stop_event.is_set():
            done, pending = await asyncio.wait(tasks + [stop_wait_task], return_when=asyncio.FIRST_COMPLETED)

            if stop_wait_task in done:
                break

            for task in list(tasks):
                if task in done:
                    exc = task.exception()
                    if exc:
                        logger.error(f"任务 {task.get_name()} 异常退出: {exc}", exc_info=True)
                    else:
                        logger.warning(f"任务 {task.get_name()} 意外结束，准备关闭主程序")
                    stop_event.set()
                    break

    except asyncio.CancelledError:
        logger.info("收到取消信号")
    finally:
        logger.info("正在关闭所有模块...")

        binance_cms_monitor._running = False
        okx_monitor._running = False
        bwe_monitor._running = False
        price_monitor._running = False

        for task in tasks + [startup_task, stop_wait_task]:
            if not task.done():
                task.cancel()

        await asyncio.gather(*tasks, startup_task, stop_wait_task, return_exceptions=True)

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
