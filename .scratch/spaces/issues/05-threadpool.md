# 05 重活离开事件循环（可选）
Status: open
Type: task
Blocked by: 01

- /api/sources 抽取+入库、/api/chat 的 build_context 进线程池
- 回归：大资料入库期间并发 /api/health 仍快（审计 P1-2）
