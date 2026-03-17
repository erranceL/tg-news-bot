"""
消息格式化模块
将各新闻源的原始数据格式化为统一的 Telegram 消息格式
统一模板：[图标] 标题 / 内容 / 信息来源 / 时间
"""

import html
import re
from datetime import datetime, timezone


BINANCE_BASE_URL = "https://www.binance.com"
OKX_BASE_URL = "https://www.okx.com"

# 图标分类关键词
_URGENT_KEYWORDS = {
    "breaking", "urgently", "sec", "court", "hack", "freeze",
    "regulation", "sued", "suspended", "exploit", "attack", "breach",
    "emergency", "liquidat", "崩盘", "黑客", "监管", "起诉", "冻结"
}
_LISTING_KEYWORDS = {
    "listing", "launchpool", "launchpad", "new pair", "new token",
    "will list", "上币", "上线", "新币"
}
_PREDICTION_KEYWORDS = {
    "new market", "prediction", "bet", "outcome", "resolved",
    "volume alert", "polymarket", "kalshi", "预测"
}


def _choose_icon(text: str) -> str:
    lower = text.lower()
    if any(k in lower for k in _URGENT_KEYWORDS):
        return "🚨"
    if any(k in lower for k in _LISTING_KEYWORDS):
        return "💎"
    if any(k in lower for k in _PREDICTION_KEYWORDS):
        return "💹"
    return "📢"


def format_timestamp(ts_ms: int = None, ts_sec: int = None) -> str:
    """格式化时间戳为 yyyy.mm.dd HH:MM UTC"""
    if ts_ms:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    elif ts_sec:
        dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    else:
        dt = datetime.now(tz=timezone.utc)
    return dt.strftime("%Y.%m.%d %H:%M UTC")


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
        return f'信息来源：<a href="{html.escape(url)}">{html.escape(source_name)}</a>'
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


def _extract_symbol_candidates(text: str) -> list:
    if not text:
        return []
    candidates = re.findall(r"\b[A-Z0-9]{2,10}\b", text.upper())
    stopwords = {
        "BINANCE", "OKX", "BYBIT", "BITGET", "COINBASE", "USDT", "USD",
        "BTC", "ETH", "API", "WILL", "LIST", "LISTING", "DELIST", "SPOT",
        "MARGIN", "FUTURES", "PERP", "NEW", "AND", "THE", "FOR", "ON",
        "OF", "TO", "IN", "FROM", "WITH", "ARE", "ITS"
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


def _compose_message(icon: str, title: str, content: str, source_name: str,
                     time_str: str, url: str = "") -> str:
    return (
        f"{icon} 标题：<b>{html.escape(title)}</b>\n"
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
    icon = _choose_icon(raw_title + " " + catalog_name)
    return _compose_message(icon, title, content, "Binance 官方公告", time_str, url)


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

    icon = _choose_icon(raw_title + " " + ann_type)
    return _compose_message(icon, title, content, "OKX 官方公告", time_str, url)


def format_bybit_announcement(data: dict) -> str:
    """格式化 Bybit 公告消息"""
    raw_title = data.get("title", "无标题")
    description = data.get("description", "") or raw_title
    url = data.get("url", "")
    publish_ts = data.get("publishTime", 0)

    direction = _detect_listing_direction(raw_title, data.get("type", ""))
    title = _title_from_direction("Bybit", direction, raw_title)
    content = _shorten(description if description != raw_title else raw_title, 160)
    if direction in ("上币", "下架"):
        content = f"{direction}相关公告：{content}"

    time_str = format_timestamp(ts_ms=int(publish_ts)) if publish_ts else format_timestamp()
    icon = _choose_icon(raw_title)
    return _compose_message(icon, title, content, "Bybit 官方公告", time_str, url)


def format_bitget_announcement(data: dict) -> str:
    """格式化 Bitget 公告消息"""
    raw_title = data.get("title", "无标题")
    description = data.get("description", "") or raw_title
    url = data.get("url", "")
    publish_ts = data.get("ctime", 0) or data.get("publishTime", 0)

    direction = _detect_listing_direction(raw_title, data.get("annType", ""))
    title = _title_from_direction("Bitget", direction, raw_title)
    content = _shorten(description if description != raw_title else raw_title, 160)
    if direction in ("上币", "下架"):
        content = f"{direction}相关公告：{content}"

    time_str = format_timestamp(ts_ms=int(publish_ts)) if publish_ts else format_timestamp()
    icon = _choose_icon(raw_title)
    return _compose_message(icon, title, content, "Bitget 官方公告", time_str, url)


def format_coinbase_announcement(data: dict) -> str:
    """格式化 Coinbase 博客消息"""
    raw_title = data.get("title", "无标题")
    summary = data.get("summary", "") or raw_title
    url = data.get("url", "")
    pub_date_str = data.get("pubDate", "")

    title = f"Coinbase 公告：{_shorten(raw_title, 50)}"
    content = _shorten(summary, 160)
    time_str = format_timestamp()
    if pub_date_str:
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
            try:
                dt = datetime.strptime(pub_date_str.strip(), fmt)
                time_str = dt.strftime("%Y.%m.%d %H:%M UTC")
                break
            except ValueError:
                continue

    icon = _choose_icon(raw_title)
    return _compose_message(icon, title, content, "Coinbase 官方博客", time_str, url)


def format_polymarket_event(data: dict) -> str:
    """格式化 Polymarket 新市场或 Volume 突增消息"""
    question = data.get("question", "无标题")
    volume = data.get("volume", 0)
    end_date = data.get("end_date_iso", "")
    url = data.get("url", "")
    alert_type = data.get("alert_type", "new_market")

    if alert_type == "volume_surge":
        title = f"Polymarket 交易量异动：{_shorten(question, 60)}"
        content = f"交易量突增至 ${float(volume):,.0f}，话题：{_shorten(question, 120)}"
        icon = "💹"
    else:
        title = f"Polymarket 新预测市场上线"
        content = f"话题：{_shorten(question, 140)}"
        if end_date:
            content += f"；截止：{end_date[:10]}"
        icon = "💹"

    return _compose_message(icon, title, content, "Polymarket", format_timestamp(), url)


def format_resonance_alert(token: str, sources: list[str]) -> str:
    """格式化多源共振预警消息"""
    source_str = "、".join(sources)
    title = f"多重共振预警：{token}"
    content = f"{token} 在5分钟内被 {source_str} 同时提及，请关注异动。"
    return (
        f"⚡ 标题：<b>{html.escape(title)}</b>\n"
        f"内容：{html.escape(content)}\n"
        f"时间：{html.escape(format_timestamp())}"
    )


def _parse_bwe_pub_date(pub_date_str: str) -> str:
    """从 RSS pubDate 字符串解析出 yyyy.mm.dd HH:MM UTC 格式"""
    if not pub_date_str:
        return format_timestamp()
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(pub_date_str.strip(), fmt)
            return dt.strftime("%Y.%m.%d %H:%M UTC")
        except ValueError:
            continue
    return format_timestamp()


def format_bwe_news(data: dict) -> str:
    """格式化方程式快讯（兼容 WebSocket 和 RSS 数据格式）"""
    news_title = data.get("news_title", "无标题")
    news_content = data.get("content", "") or news_title
    url = _normalize_url(data.get("url", ""))
    timestamp = data.get("timestamp", 0)
    pub_date = data.get("pubDate", "")
    coins = data.get("coins_included", []) or []
    source_name = data.get("source_name", "BWEnews")

    title = "方程式快讯"
    content = _shorten(news_content, 180)
    if coins:
        coin_str = ", ".join(map(str, coins[:6]))
        content = f"涉及币种：{coin_str}；{content}"

    if timestamp:
        time_str = format_timestamp(ts_sec=timestamp)
    elif pub_date:
        time_str = _parse_bwe_pub_date(pub_date)
    else:
        time_str = format_timestamp()

    icon = _choose_icon(news_title)
    return _compose_message(icon, title, content, source_name, time_str, url)


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
    return _compose_message("🚨", title, content, "Binance 行情", format_timestamp())
