"""
消息格式化模块
将各新闻源的原始数据格式化为统一的 Telegram 消息格式
"""

import html
import time
from datetime import datetime, timezone


def format_timestamp(ts_ms: int = None, ts_sec: int = None) -> str:
    """格式化时间戳为可读字符串"""
    if ts_ms:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    elif ts_sec:
        dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    else:
        dt = datetime.now(tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_binance_announcement(data: dict) -> str:
    """
    格式化币安公告消息
    
    Args:
        data: 币安公告数据，包含 catalogName, title, body 等字段
    """
    catalog_name = data.get("catalogName", "Unknown")
    title = data.get("title", "无标题")
    body = data.get("body", "")
    publish_date = data.get("publishDate", 0)

    # 根据分类选择 emoji
    emoji_map = {
        "New Listing": "🆕",
        "Delisting": "⚠️",
        "New Cryptocurrency Listing": "🆕",
        "Launchpad": "🚀",
        "Launchpool": "🌊",
    }
    emoji = emoji_map.get(catalog_name, "📢")

    # 截断过长的 body
    if len(body) > 500:
        body = body[:500] + "..."

    time_str = format_timestamp(ts_ms=publish_date) if publish_date else format_timestamp()

    msg = (
        f"{emoji} <b>[币安公告 - {html.escape(catalog_name)}]</b>\n\n"
        f"📌 <b>{html.escape(title)}</b>\n\n"
    )
    if body:
        msg += f"{html.escape(body)}\n\n"
    msg += f"🕐 {time_str}"

    return msg


def format_okx_announcement(data: dict) -> str:
    """
    格式化 OKX 公告消息
    
    Args:
        data: OKX 公告数据，包含 title, annType, url, pTime 等字段
    """
    title = data.get("title", "无标题")
    ann_type = data.get("annType", "")
    url = data.get("url", "")
    p_time = data.get("pTime", "")

    # 根据类型选择 emoji
    if "listing" in ann_type.lower() or "上币" in title or "new" in ann_type.lower():
        emoji = "🆕"
    elif "delist" in ann_type.lower() or "下架" in title:
        emoji = "⚠️"
    else:
        emoji = "📢"

    time_str = ""
    if p_time:
        try:
            time_str = format_timestamp(ts_ms=int(p_time))
        except (ValueError, TypeError):
            time_str = str(p_time)

    msg = (
        f"{emoji} <b>[OKX 公告]</b>\n\n"
        f"📌 <b>{html.escape(title)}</b>\n\n"
    )
    if url:
        msg += f"🔗 <a href=\"{html.escape(url)}\">查看详情</a>\n"
    if time_str:
        msg += f"🕐 {time_str}"

    return msg


def format_bwe_news(data: dict) -> str:
    """
    格式化方程式新闻消息
    
    Args:
        data: BWE 新闻数据，包含 source_name, news_title, coins_included, url, timestamp
    """
    source_name = data.get("source_name", "BWEnews")
    news_title = data.get("news_title", "无标题")
    coins = data.get("coins_included", [])
    url = data.get("url", "")
    timestamp = data.get("timestamp", 0)

    coins_str = ", ".join(coins) if coins else ""

    time_str = format_timestamp(ts_sec=timestamp) if timestamp else format_timestamp()

    msg = f"📰 <b>[方程式新闻 - {html.escape(source_name)}]</b>\n\n"
    msg += f"📌 <b>{html.escape(news_title)}</b>\n\n"
    if coins_str:
        msg += f"💰 相关币种: <code>{html.escape(coins_str)}</code>\n"
    if url:
        msg += f"🔗 <a href=\"{html.escape(url)}\">查看详情</a>\n"
    msg += f"🕐 {time_str}"

    return msg


def format_price_alert(symbol: str, current_price: float, base_price: float,
                       change_percent: float, window_minutes: int) -> str:
    """
    格式化价格波动提醒消息
    
    Args:
        symbol: 交易对符号
        current_price: 当前价格
        base_price: 基准价格（窗口内的起始价格）
        change_percent: 波动百分比
        window_minutes: 时间窗口（分钟）
    """
    direction = "📈" if change_percent > 0 else "📉"
    color_emoji = "🟢" if change_percent > 0 else "🔴"
    sign = "+" if change_percent > 0 else ""

    # 提取 base coin 名称
    base_coin = symbol.replace("USDT", "").replace("BUSD", "").replace("USDC", "")

    msg = (
        f"🚨 <b>价格剧烈波动提醒</b> {direction}\n\n"
        f"{color_emoji} <b>{html.escape(base_coin)}</b> ({html.escape(symbol)})\n\n"
        f"💵 当前价格: <code>${current_price:,.8g}</code>\n"
        f"📊 基准价格: <code>${base_price:,.8g}</code>\n"
        f"📈 {window_minutes}分钟波动: <b>{sign}{change_percent:.2f}%</b>\n\n"
        f"🕐 {format_timestamp()}"
    )

    return msg
