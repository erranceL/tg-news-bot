"""
Telegram Bot 模块
负责消息发送和用户交互
"""

import asyncio
import logging
import html
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS

logger = logging.getLogger("telegram_bot")


class TelegramNewsBot:
    """Telegram 新闻转发 Bot"""

    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.application = None
        self.chat_ids: set = set(TELEGRAM_CHAT_IDS)
        self._send_lock = asyncio.Lock()
        self._message_queue: asyncio.Queue = asyncio.Queue()
        # 持久化 chat_ids 文件路径
        self._chat_ids_file = "chat_ids.txt"
        self._load_chat_ids()

    def _load_chat_ids(self):
        """从文件加载已保存的 chat_ids"""
        try:
            with open(self._chat_ids_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self.chat_ids.add(int(line))
                        except ValueError:
                            self.chat_ids.add(line)
            logger.info(f"已加载 {len(self.chat_ids)} 个 chat_id")
        except FileNotFoundError:
            logger.info("未找到 chat_ids 文件，将在用户发送 /start 后自动创建")

    def _save_chat_ids(self):
        """保存 chat_ids 到文件"""
        with open(self._chat_ids_file, 'w') as f:
            for chat_id in self.chat_ids:
                f.write(f"{chat_id}\n")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令，注册当前 chat 接收新闻"""
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        chat_title = update.effective_chat.title or update.effective_chat.full_name or str(chat_id)

        self.chat_ids.add(chat_id)
        self._save_chat_ids()

        welcome_msg = (
            "🤖 <b>新闻转发 Bot 已激活</b>\n\n"
            f"📍 当前聊天: <code>{html.escape(chat_title)}</code>\n"
            f"🆔 Chat ID: <code>{chat_id}</code>\n"
            f"📝 类型: {chat_type}\n\n"
            "📡 <b>监听的新闻源:</b>\n"
            "  • 币安上币/下架公告 (CMS WebSocket)\n"
            "  • OKX 上币/下架公告 (REST API)\n"
            "  • 方程式新闻 (BWE WebSocket)\n"
            "  • 币安已上架币种 5分钟 15% 波动提醒\n\n"
            "📋 <b>可用命令:</b>\n"
            "  /start - 注册接收新闻\n"
            "  /stop - 停止接收新闻\n"
            "  /status - 查看 Bot 运行状态\n"
            "  /help - 帮助信息"
        )
        await update.message.reply_text(welcome_msg, parse_mode=ParseMode.HTML)
        logger.info(f"新用户注册: {chat_title} ({chat_id})")

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /stop 命令，取消接收新闻"""
        chat_id = update.effective_chat.id
        self.chat_ids.discard(chat_id)
        self._save_chat_ids()
        await update.message.reply_text("✅ 已停止接收新闻推送。发送 /start 可重新订阅。")
        logger.info(f"用户取消订阅: {chat_id}")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /status 命令，显示运行状态"""
        from src.dedup import deduplicator

        status_msg = (
            "📊 <b>Bot 运行状态</b>\n\n"
            f"👥 订阅用户数: {len(self.chat_ids)}\n"
            f"🗃 去重缓存条目: {deduplicator.get_cache_size()}\n"
            f"✅ Bot 运行中"
        )
        await update.message.reply_text(status_msg, parse_mode=ParseMode.HTML)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /help 命令"""
        help_msg = (
            "📖 <b>帮助信息</b>\n\n"
            "本 Bot 自动监听以下新闻源并转发：\n\n"
            "1️⃣ <b>币安公告</b> - 上币、下架等官方公告\n"
            "2️⃣ <b>OKX 公告</b> - 上币、下架等官方公告\n"
            "3️⃣ <b>方程式新闻</b> - BWEnews 实时新闻\n"
            "4️⃣ <b>价格波动</b> - 币安已上架币种 5分钟内 15% 波动提醒\n\n"
            "⚡ 所有新闻经过去重过滤，相同内容仅推送一次。\n\n"
            "📋 <b>命令列表:</b>\n"
            "  /start - 注册接收新闻\n"
            "  /stop - 停止接收新闻\n"
            "  /status - 查看运行状态\n"
            "  /help - 帮助信息"
        )
        await update.message.reply_text(help_msg, parse_mode=ParseMode.HTML)

    async def send_news(self, message: str, source: str = ""):
        """
        向所有订阅用户发送新闻
        
        Args:
            message: 格式化的新闻消息（HTML 格式）
            source: 新闻来源标识
        """
        if not self.chat_ids:
            logger.warning("没有订阅用户，消息未发送")
            return

        for chat_id in list(self.chat_ids):
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
                # 避免触发 Telegram 频率限制
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"发送消息到 {chat_id} 失败: {e}")
                # 如果是被用户 block 或 chat 不存在，移除
                error_str = str(e).lower()
                if "blocked" in error_str or "not found" in error_str or "deactivated" in error_str:
                    logger.info(f"移除无效 chat_id: {chat_id}")
                    self.chat_ids.discard(chat_id)
                    self._save_chat_ids()

    async def setup(self):
        """初始化 Bot Application 并注册命令处理器"""
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("stop", self.stop_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("help", self.help_command))

        # 初始化 application
        await self.application.initialize()
        await self.application.start()
        # 开始轮询（非阻塞）
        await self.application.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram Bot 已启动，等待命令...")

    async def shutdown(self):
        """关闭 Bot"""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            logger.info("Telegram Bot 已关闭")


# 全局 Bot 实例
telegram_bot = TelegramNewsBot()
