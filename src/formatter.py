"""
消息格式化模块
将各新闻源的原始数据格式化为统一的 Telegram 消息格式
统一模板：标题 / 内容 / 信息来源 / 时间
"""

import html
import re
from datetime import datetime, timezone


BINANCE_BASE_URL = "https://www.binance.com"
OKX_BASE_URL = "https://www.okx.com"


def format_timestamp(ts_ms: int = None, ts_sec: int = None) -> str:
    """格式化时间戳为 yyyy.mm.dd"""
    if ts_ms:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    elif ts_sec:
        dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    else:
        dt = datetime.now(tz=timezone.utc)
    return dt.strftime("%Y.%m.%d")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _shorten(text: str, limit: int = 140) -> str:
    text = _strip_html(text)
    if not text:
        return "暂无详细内容"
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _build_source_line(source_name: str, url: str = "") -> str:
    if url:
        return f"信息来源：<a href=\"{html.escape(url)}\">{html.escape(source_name)}</a>"
    return f"信息来源：{html.escape(source_name)}"


def _normalize_url(url: str, base_url: str = "") -> str:
    if not url:
        return ""
    url = str(url).strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if base_url and url.startswith("/"):
        return f"{base_url}{url}"
    return url


def _detect_listing_direction(text: str, catalog_name: str = "") -> str:
    full_text = f"{catalog_name} {text}".lower()
    if any(k in full_text for k in ["delist", "delisting", "下架", "removal", "will remove"]):
        return "下架"
    if any(k in full_text for k in ["list", "listing", "上线", "上币", "will list", "new cryptocurrency listing"]):
        return "上币"
    return "公告"


def _extract_symbol_candidates(text: str) -> list[str]:
    if not text:
        return []
    candidates = re.findall(r"\b[A-Z0-9]{2,10}\b", text.upper())
    stopwords = {
        "BINANCE", "OKX", "USDT", "USD", "BTC", "ETH", "API", "WILL",
        "LIST", "LISTING", "DELIST", "SPOT", "MARGIN", "FUTURES", "PERP",
        "NEW", "AND", "THE", "FOR", "ON", "OF", "TO", "IN", "FROM"
    }
    result = []
    for item in candidates:
        if item in stopwords:
            continue
        if item not in result:
            result.append(item)
    return result[:4]


def _title_from_direction(exchange: str, direction: str, raw_title: str) -> str:
    symbols = _extract_symbol_candidates(raw_title)
    symbol_text = "、".join(symbols) if symbols else _shorten(raw_title, 36)
    if direction == "上币":
        return f"{exchange}{symbol_text}上币公告"
    if direction == "下架":
        return f"{exchange}{symbol_text}下架公告"
    return f"{exchange}公告"


def _compose_message(title: str, content: str, source_name: str, time_str: str, url: str = "") -> str:
    return (
        f"标题：<b>{html.escape(title)}</b>\n"
        f"内容：{html.escape(content)}\n"
        f"{_build_source_line(source_name, url)}\n"
        f"时间：{html.escape(time_str)}"
    )


def format_binance_announcement(data: dict) -> str:
    """格式化币安公告消息"""
    catalog_name = data.get("catalogName", "")
    raw_title = data.get("title", "无标题")
    body = data.get("body", "") or data.get("summary", "") or raw_title
    url = _normalize_url(data.get("url", "") or data.get("code", ""), BINANCE_BASE_URL)
    publish_date = data.get("publishDate", 0)

    direction = _detect_listing_direction(raw_title, catalog_name)
    title = _title_from_direction("币安", direction, raw_title)
    content = _shorten(body if body and body != raw_title else raw_title, 160)
    if direction in ("上币", "下架"):
        content = f"{direction}相关公告：{content}"
    time_str = format_timestamp(ts_ms=publish_date) if publish_date else format_timestamp()
    return _compose_message(title, content, "Binance 官方公告", time_str, url)


def format_okx_announcement(data: dict) -> str:
    """格式化OKX公告消息"""
    raw_title = data.get("title", "无标题")
    ann_type = data.get("annType", "")
    description = data.get("description", "") or data.get("content", "") or raw_title
    url = _normalize_url(data.get("url", ""), OKX_BASE_URL)
    p_time = data.get("pTime", "")

    direction = _detect_listing_direction(raw_title, ann_type)
    title = _title_from_direction("OKX", direction, raw_title)
    content = _shorten(description if description and description != raw_title else raw_title, 160)
    if direction in ("上币", "下架"):
        content = f"{direction}相关公告：{content}"

    time_str = format_timestamp()
    if p_time:
        try:
            time_str = format_timestamp(ts_ms=int(p_time))
        except (ValueError, TypeError):
            time_str = str(p_time)

    return _compose_message(title, content, "OKX 官方公告", time_str, url)


def format_bwe_news(data: dict) -> str:
    """格式化方程式快讯"""
    news_title = data.get("news_title", "无标题")
    news_content = data.get("content", "") or news_title
    url = _normalize_url(data.get("url", ""))
    timestamp = data.get("timestamp", 0)
    coins = data.get("coins_included", []) or []

    title = "方程式快讯"
    content = _shorten(news_content, 180)
    if coins:
        content = f"涉及币种：{', '.join(map(str, coins[:6]))}；{content}"
    time_str = format_timestamp(ts_sec=timestamp) if timestamp else format_timestamp()
    return _compose_message(title, content, "BWEnews", time_str, url)


def format_price_alert(symbol: str, current_price: float, base_price: float,
                       change_percent: float, window_minutes: int) -> str:
    """格式化价格波动提醒消息"""
    base_coin = symbol.replace("USDT", "").replace("BUSD", "").replace("USDC", "")
    direction = "上涨" if change_percent > 0 else "下跌"
    title = f"{base_coin}价格异动提醒"
    content = (
        f"{base_coin}在{window_minutes}分钟内{direction}{abs(change_percent):.2f}%，"
        f"价格从{base_price:,.8g}变动至{current_price:,.8g}。"
    )
    return _compose_message(title, content, "Binance 行情", format_timestamp())
