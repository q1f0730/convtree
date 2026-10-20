# 重活进线程池，不阻塞事件循环

审计 P1-2：`/api/sources` 与 `/api/chat` 是 `async def`，却在事件循环里同步做 PDF 解析、切块写库、BM25/嵌入检索，期间所有请求停摆。决定用 `starlette.concurrency.run_in_threadpool` 包住抽取+入库和 `build_context`（含检索），端点保持 async（需要 await 上传与 SSE）。不引入后台任务队列——单用户规模下线程池已足够，队列需要状态轮询 UI。
