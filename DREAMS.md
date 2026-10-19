# DREAMS — target list (upper bound; constitution is lower bound)

> Scope: uncompleted targets only. Delete on completion; history lives in `ledger/events.jsonl`.

## Rules

1. Read this file before starting work. Every task must map to one listed dream. Unmappable task → stop, ask user: add dream or drop task.
2. No invented work. Empty dream list → no code work.
3. On completion: delete entry + ledger `verification` (or `decision`) referencing original goal. Criteria = entry's 完成标准.
4. New dream: append entry + ledger `append --kind goal` (record returned E-xxxx in entry).
5. Conflict with ledger → ledger wins; fix this file.

## 非目标

- 替代 Cherry Studio（old-chain E-0073 voided; still void）

## 梦

- [ ] 把 convtree 做成 NotebookLM Plus 式学习工具 — 里程碑1 资料底座（用户，2026-10-19）
  完成标准：
  1. 未装 chromadb / sentence-transformers / torch 时，`server/accept_full.py` 全绿；检索默认后端为纯 Python BM25（中文字二元组 + 英文词），块表存 SQLite。
  2. 配了 embed_base_url/embed_model（或回落聊天端点）时走嵌入+BM25 混合；嵌入调用失败时回落 BM25，聊天不报错。
  3. `POST /api/sources` 接受 .txt/.md/.pdf/.docx 上传、粘贴文本、URL（httpx 抓取 + html.parser 去标签）；空文件/坏 URL/不支持类型返回 4xx。
  4. 每个块记录 start/end 字符偏移，且 `source.content[start:end] == chunk.text`（验收脚本断言）。
  5. 根节点属于有资料的笔记本时，回答流含 `event: cites`，引用表（n→资料 id/名/块文/偏移）落 citation 表；`message.content` 不被改写（D-101 门禁绿）。
  6. 分支子节点经 path[0] 继承根的笔记本，子节点提问同样产出引用。
  7. 前端 demo/ink.html：笔记本/资料面板（建笔记本、列/加/删资料、显示块数）、在笔记本内开新对话、回答里 [n] 可点开气泡显示资料名+原文段。
  8. 三道闸 exit 0：`python server/accept_full.py`、`python tools/check_invariants.py --all`、`python tools/ledger.py verify`。
  账本：E-0071

```
- [ ] <one-line target>（proposer, date）
  完成标准：<decidable, no degree words>
  账本：E-xxxx
```
