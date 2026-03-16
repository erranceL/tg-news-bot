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
    await asyncio.sleep(3)  # 等待 Bot 完全启动
    startup_msg = (
        "🟢 <b>新闻转发 Bot 已上线</b>\n\n"
        "📡 <b>监听中的新闻源:</b>\n"
        "  • 币安公告 (CMS WebSocket)\n"
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

    # 1. 启动 Telegram Bot
    try:
        await telegram_bot.setup()
        logger.info("✅ Telegram Bot 已启动")
    except Exception as e:
        logger.error(f"❌ Telegram Bot 启动失败: {e}", exc_info=True)
        return

    # 2. 创建所有监听任务
    tasks = []

    # 币安 CMS 公告监听
    task_binance_cms = asyncio.create_task(binance_cms_monitor.start())
    task_binance_cms.set_name("binance_cms")
    tasks.append(task_binance_cms)
    logger.info("✅ 币安 CMS 公告监听已启动")

    # OKX 公告轮询
    task_okx = asyncio.create_task(okx_monitor.start())
    task_okx.set_name("okx_announcements")
    tasks.append(task_okx)
    logger.info("✅ OKX 公告监听已启动")

    # 方程式新闻 WebSocket
    task_bwe = asyncio.create_task(bwe_monitor.start())
    task_bwe.set_name("bwe_news")
    tasks.append(task_bwe)
    logger.info("✅ 方程式新闻监听已启动")

    # 币安价格波动监控
    task_price = asyncio.create_task(price_monitor.start())
    task_price.set_name("price_monitor")
    tasks.append(task_price)
    logger.info("✅ 价格波动监控已启动")

    # 发送启动通知
    asyncio.create_task(startup_notification())

    logger.info("=" * 60)
    logger.info("  所有模块已启动，Bot 正在运行...")
    logger.info("=" * 60)

    # 3. 等待所有任务（任何一个异常退出都会被捕获）
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

        for task in done:
            if task.exception():
                logger.error(f"任务 {task.get_name()} 异常退出: {task.exception()}")

    except asyncio.CancelledError:
        logger.info("收到取消信号")
    finally:
        # 4. 优雅关闭
        logger.info("正在关闭所有模块...")

        # 先标记所有监听器停止
        binance_cms_monitor._running = False
        okx_monitor._running = False
        bwe_monitor._running = False
        price_monitor._running = False

        # 取消所有未完成的任务
        for task in tasks:
            if not task.done():
                task.cancel()

        # 等待任务完成
        await asyncio.gather(*tasks, return_exceptions=True)

        try:
            await telegram_bot.shutdown()
        except Exception as e:
            logger.warning(f"关闭 Telegram Bot 异常: {e}")

        logger.info("Bot 已完全关闭")


def handle_signal(sig, frame):
    """处理系统信号"""
    logger.info(f"收到信号 {sig}，准备关闭...")
    for task in asyncio.all_tasks():
        task.cancel()


if __name__ == "__main__":
    # 注册信号处理
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("用户中断，Bot 已停止")
    except Exception as e:
        logger.error(f"Bot 异常退出: {e}", exc_info=True)
        sys.exit(1)
