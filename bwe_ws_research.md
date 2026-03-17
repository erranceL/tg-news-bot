# BWEnews WebSocket 排查笔记

## 官方帖子关键信息

来源： https://x.com/bwenews/status/1915350326905115039

帖子正文提到：

> wss://bwenews-api.bwe-ws.com/ws by subscribing to this Websocket, you can receive news from certain news source (currently its limited to our own reporting source, the exchange announcements are not relayed)

并给出示例消息：

```json
{"source_name":"BWENEWS","news_title":"This is a test message news","coins_included":["BTC","ETH","SOL"],"url":"bwenews123.com/asdads","timestamp":1745770800}
```

## 当前结论

1. 官方明确写了“by subscribing to this Websocket”，说明**很可能需要发送订阅消息**，而不是仅仅建立连接后被动接收。
2. 当前推送范围“currently its limited to our own reporting source, the exchange announcements are not relayed”，说明就算连接正常，也**不会推送交易所公告类内容**，仅限 BWE 自有报道源。
3. 官方帖子没有直接给出订阅消息格式，因此需要继续通过测试脚本和更多公开资料进一步确认。

## 官方文档补充结论

来源： https://telegra.ph/BWEnews-API-documentation-06-19

文档明确写到：

> By connecting and subscribing to this WebSocket endpoint, clients will receive live news updates directly from BWEnews.

并补充了心跳要求：

> Send: `ping` (plain text)
> Receive: `pong` (plain text)

当前页面仍未直接给出 JSON 订阅报文示例，因此“subscribing”可能表示：
1. 仅建立连接并保持心跳；或
2. 建立连接后还需发送某种订阅文本/消息。

至少可以确认：
- **需要处理 ping/pong 心跳**；
- WebSocket 只提供 BWE 自有快讯，不包含交易所公告；
- 若对端要求客户端先发 `ping` 或其他文本，当前 Bot 逻辑尚未显式实现，需通过独立测试脚本进一步验证。
