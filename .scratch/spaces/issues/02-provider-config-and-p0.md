# 02 模型接入配置深模块 + P0 修复
Status: open
Type: task
Blocked by: 01

- 改 chat/embed baseUrl 主机不带新 key ⇒ 清 key；嵌入跨主机不回落聊天 key（审计 P0-1）
- Host 头白名单（`CONVTREE_ALLOWED_HOSTS`）（P0-1/P2-9）
- `.env`/环境变量优先级（P2-1）
- URL 入库 SSRF：逐跳解析校验、禁私网/回环/链路本地/元数据、数字形式 IP、流式限额（P0-2/P2-6）
- refNode 服务端校验 + 前端 escAttr（P1-1）
- 流式检查上游 HTTP 状态，失败消息不进上下文（P1-4）
- Inception Mercury 预设并设为默认
