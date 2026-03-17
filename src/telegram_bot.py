"""
Telegram Bot 模块
负责消息发送、用户交互，以及频道自动绑定
"""

import asyncio
import logging
import html
import aiohttp
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from src.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS,
    BINANCE_ANNOUNCEMENT_API, BINANCE_LISTING_CATALOG_ID,
    OKX_API_BASE_URL, OKX_ANNOUNCEMENT_PATH,
)
from src.formatter import format_binance_announcement, format_okx_announcement, format_bwe_news

logger = logging.getLogger("telegram_bot")

CHANNEL_CHAT_IDS = {chat_id for chat_id in TELEGRAM_CHAT_IDS if isinstance(chat_id, int) and chat_id < 0}


class TelegramNewsBot:
    """Telegram 新闻转发 Bot"""

    def __init__(self):
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.application = None
        self.chat_ids: set = set(TELEGRAM_CHAT_IDS)
        self._send_lock = asyncio.Lock()
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
            logger.info("未找到 chat_ids 文件，将在收到 /start 或频道消息后自动创建")

    def _save_chat_ids(self):
        """保存 chat_ids 到文件"""
        with open(self._chat_ids_file, 'w') as f:
            for chat_id in self.chat_ids:
                f.write(f"{chat_id}\n")

    def _is_channel_chat(self, chat_id) -> bool:
        return isinstance(chat_id, int) and chat_id < 0

    def _register_chat_id(self, chat_id, chat_title: str, chat_type: str) -> bool:
        """注册 chat_id，返回是否为新增"""
        existed = chat_id in self.chat_ids
        self.chat_ids.add(chat_id)
        self._save_chat_ids()
        if not existed:
            logger.info(f"新增推送目标: {chat_title} ({chat_id}) type={chat_type}")
        return not existed

    async def _send_to_channel_copies(self, text: str, exclude_chat_id=None, disable_web_page_preview: bool = True):
        """将一条消息同步发送到所有已绑定频道"""
        channel_ids = [chat_id for chat_id in self.chat_ids if self._is_channel_chat(chat_id)]
        for channel_id in channel_ids:
            if exclude_chat_id is not None and channel_id == exclude_chat_id:
                continue
            try:
                await self.bot.send_message(
                    chat_id=channel_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=disable_web_page_preview,
                )
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"同步消息到频道 {channel_id} 失败: {e}")

    async def _reply_and_mirror(self, update: Update, text: str, disable_web_page_preview: bool = True):
        """回复当前聊天；若当前为私聊，则额外同步一份到所有频道"""
        chat = update.effective_chat
        if not chat:
            return

        if update.message:
            await update.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=disable_web_page_preview,
            )
        elif update.channel_post:
            await self.bot.send_message(
                chat_id=chat.id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=disable_web_page_preview,
            )
        else:
            await self.bot.send_message(
                chat_id=chat.id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=disable_web_page_preview,
            )

        if chat.type == "private":
            mirror_text = (
                "🔁 <b>私聊命令回执同步</b>\n\n"
                f"用户：<code>{html.escape(chat.full_name or str(chat.id))}</code>\n"
                f"用户ID：<code>{chat.id}</code>\n\n"
                f"{text}"
            )
            await self._send_to_channel_copies(mirror_text, disable_web_page_preview=disable_web_page_preview)

    async def _bind_channel_from_update(self, update: Update):
        """当收到频道 channel_post 时，自动绑定该频道为推送目标"""
        if not update.effective_chat:
            return
        chat = update.effective_chat
        if chat.type != "channel":
            return

        chat_id = chat.id
        chat_title = chat.title or str(chat_id)
        is_new = self._register_chat_id(chat_id, chat_title, chat.type)
        logger.info(f"检测到频道消息，频道已绑定: {chat_title} ({chat_id})")

        if is_new:
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "✅ <b>频道绑定成功</b>\n\n"
                        f"频道：<code>{html.escape(chat_title)}</code>\n"
                        f"频道ID：<code>{chat_id}</code>\n\n"
                        "后续币安、OKX、方程式快讯和价格异动提醒都会自动推送到这里。"
                    ),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning(f"频道绑定后发送确认消息失败: {chat_id} error={e}")

    async def channel_post_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理频道消息，自动绑定频道"""
        await self._bind_channel_from_update(update)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令，注册当前 chat 接收新闻；若来自频道则自动绑定频道"""
        if update.effective_chat and update.effective_chat.type == "channel":
            await self._bind_channel_from_update(update)
            return

        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        chat_title = update.effective_chat.title or update.effective_chat.full_name or str(chat_id)

        self._register_chat_id(chat_id, chat_title, chat_type)

        welcome_msg = (
            "🤖 <b>新闻转发 Bot 已激活</b>\n\n"
            f"📍 当前聊天: <code>{html.escape(chat_title)}</code>\n"
            f"🆔 Chat ID: <code>{chat_id}</code>\n"
            f"📝 类型: {chat_type}\n\n"
            "📡 <b>监听的新闻源:</b>\n"
            "  • 币安上币/下架公告 (公开HTTP轮询)\n"
            "  • OKX 上币/下架公告 (REST API)\n"
            "  • 方程式新闻 (BWE WebSocket)\n"
            "  • 币安已上架币种 5分钟 15% 波动提醒\n\n"
            "📋 <b>可用命令:</b>\n"
            "  /start - 注册接收新闻\n"
            "  /stop - 停止接收新闻\n"
            "  /status - 查看 Bot 运行状态\n"
            "  /latest - 手动拉取最近一条币安、OKX 和方程式快讯\n"
            "  /help - 帮助信息"
        )
        await self._reply_and_mirror(update, welcome_msg)
        logger.info(f"新用户注册: {chat_title} ({chat_id})")

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /stop 命令，取消接收新闻"""
        if not update.effective_chat:
            return
        chat_id = update.effective_chat.id
        self.chat_ids.discard(chat_id)
        self._save_chat_ids()
        await self._reply_and_mirror(update, "✅ 已停止接收新闻推送。发送 /start 可重新订阅。")
        logger.info(f"用户取消订阅: {chat_id}")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /status 命令，显示运行状态"""
        if not update.effective_chat:
            return
        from src.dedup import deduplicator

        status_msg = (
            "📊 <b>Bot 运行状态</b>\n\n"
            f"👥 订阅目标数: {len(self.chat_ids)}\n"
            f"🗃 去重缓存条目: {deduplicator.get_cache_size()}\n"
            "✅ Bot 运行中"
        )
        await self._reply_and_mirror(update, status_msg)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /help 命令"""
        help_msg = (
            "📖 <b>帮助信息</b>\n\n"
            "本 Bot 自动监听以下新闻源并转发：\n\n"
            "1️⃣ <b>币安公告</b> - 上币、下架等官方公告\n"
            "2️⃣ <b>OKX 公告</b> - 上币、下架等官方公告\n"
            "3️⃣ <b>方程式新闻</b> - BWEnews 实时新闻\n"
            "4️⃣ <b>价格波动</b> - 币安已上架币种 5分钟内 15% 波动提醒\n\n"
            "⚡ 所有新闻经过去重过滤，相同内容仅推送一次。\n"
            "频道内发送 /start 后，Bot 也会自动将该频道绑定为推送目标。\n\n"
            "📋 <b>命令列表:</b>\n"
            "  /start - 注册接收新闻或绑定频道\n"
            "  /stop - 停止接收新闻\n"
            "  /status - 查看运行状态\n"
            "  /latest - 手动拉取最近一条币安、OKX 和方程式快讯\n"
            "  /help - 帮助信息"
        )
        await self._reply_and_mirror(update, help_msg)

    async def latest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /latest 命令，手动拉取最近一条币安、OKX 和方程式快讯"""
        await self._reply_and_mirror(update, "⏳ 正在拉取最近一条币安、OKX 和方程式快讯，请稍候...")
        try:
            async with aiohttp.ClientSession() as session:
                params = {"catalogId": BINANCE_LISTING_CATALOG_ID, "pageNo": 1, "pageSize": 1}
                async with session.get(BINANCE_ANNOUNCEMENT_API, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    bdata = await resp.json()
                    articles = bdata.get("data", {}).get("articles", [])
                    barticle = articles[0] if articles else {"title": "暂无公告", "body": "", "publishDate": 0}

                okx_url = f"{OKX_API_BASE_URL}{OKX_ANNOUNCEMENT_PATH}"
                headers = {"Accept-Language": "en-US", "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
                async with session.get(okx_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    odata = await resp.json()
                    items = []
                    for item in odata.get("data", []):
                        if isinstance(item, dict) and "details" in item:
                            items.extend(item["details"])
                        elif isinstance(item, dict):
                            items.append(item)
                    oann = items[0] if items else {}

            from src.bwe_news import bwe_monitor
            bwe_item = bwe_monitor.get_latest_cached_news()

            binance_msg = format_binance_announcement({
                "catalogName": "New Cryptocurrency Listing",
                "title": barticle.get("title", "无标题"),
                "body": "这是通过 /latest 命令手动拉取的最新币安公告。",
                "publishDate": 0,
            })
            okx_msg = format_okx_announcement(oann)

            await self._reply_and_mirror(update, "📥 <b>最近消息如下：</b>")
            await self._reply_and_mirror(update, binance_msg)
            await self._reply_and_mirror(update, okx_msg)
            if bwe_item:
                await self._reply_and_mirror(update, format_bwe_news(bwe_item))
            else:
                await self._reply_and_mirror(
                    update,
                    "📰 <b>[方程式新闻]</b>\n\n当前尚未缓存到任何已收到的方程式快讯。WebSocket 会继续保持连接并等待对端推送；一旦收到过真实快讯，后续 /latest 就会返回最近一条缓存内容。",
                )
        except Exception as e:
            logger.error(f"处理 /latest 命令失败: {e}", exc_info=True)
            await self._reply_and_mirror(update, f"❌ 拉取最新消息失败：{html.escape(str(e))}")

    async def send_news(self, message: str, source: str = ""):
        """向所有订阅用户和已绑定频道发送新闻（串行加锁，保证顺序）"""
        if not self.chat_ids:
            logger.warning("没有订阅目标，消息未发送")
            return

        async with self._send_lock:
            for chat_id in list(self.chat_ids):
                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"发送消息到 {chat_id} 失败: {e}")
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
        self.application.add_handler(CommandHandler("latest", self.latest_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(MessageHandler(filters.ChatType.CHANNEL, self.channel_post_handler))

        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling(drop_pending_updates=True, allowed_updates=["message", "channel_post"])
        logger.info("Telegram Bot 已启动，等待命令和频道消息...")

    async def shutdown(self):
        """关闭 Bot"""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            logger.info("Telegram Bot 已关闭")


telegram_bot = TelegramNewsBot()
