# 02 模型接入配置深模块 + P0 修复
Status: resolved
Type: task
Blocked by: 01

- 改 chat/embed baseUrl 主机不带新 key ⇒ 清 key；嵌入跨主机不回落聊天 key（审计 P0-1）
- Host 头白名单（`CONVTREE_ALLOWED_HOSTS`）（P0-1/P2-9）
- `.env`/环境变量优先级（P2-1）
- URL 入库 SSRF：逐跳解析校验、禁私网/回环/链路本地/元数据、数字形式 IP、流式限额（P0-2/P2-6）
- refNode 服务端校验 + 前端 escAttr（P1-1）
- 流式检查上游 HTTP 状态，失败消息不进上下文（P1-4）
- Inception Mercury 预设并设为默认

## Answer
- `server/settings.py` 成为唯一的端点决策模块（chat_endpoint / embed_endpoint），llm/rag 只调用它。
- 回归测试（server/accept_v2.py）：test_key_cleared_when_base_host_changes、test_key_not_sent_to_new_host、
  test_embed_no_chat_key_fallback_cross_host、test_host_guard_rejects_unknown_host、test_dotenv_loaded、
  test_ssrf_blocks_{loopback,localhost_name,hex_ip,decimal_ip,ipv6_loopback,metadata,private_10,zero}、
  test_ssrf_blocks_redirect_to_private、test_url_download_capped_streaming、test_refnode_rejects_markup、
  test_atomshtml_escapes_ref_attr、test_stream_surfaces_upstream_http_error、test_failed_reply_excluded_from_context、
  test_mercury_stream_chunks。
- accept_full 改动：#006 服务商集合加 inception（需求变更）；#095「CORS无星」由源码 grep 换成等价行为测试（陌生 Origin 拿不到 ACAO）。
- 真模型冒烟（2026-10-20，本地临时库，Mercury mercury-3）：根问题 1 条引用、2.24s 完成；分支"冻结需求"追问 1 条引用、含 [1]、1.63s 完成。key 只在 /tmp 临时配置里，未写入仓库。
