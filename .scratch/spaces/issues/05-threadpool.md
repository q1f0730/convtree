# 05 重活离开事件循环（可选）
Status: resolved
Type: task
Blocked by: 01

- /api/sources 抽取+入库、/api/chat 的 build_context 进线程池
- 回归：大资料入库期间并发 /api/health 仍快（审计 P1-2）

## Answer
/api/sources 的抽取+入库+索引整体进 run_in_threadpool（_ingest_sync）；/api/chat 的 build_context（含检索）进线程池。
回归：accept_v2 test_ingest_does_not_block_health（真 uvicorn，14.8MB TXT 入库 ~2s 期间 /api/health 最大延迟 2.23s → 0.11s）。
未做：嵌入失败熔断、后台入库队列（见审计 P1-2 后半）。
