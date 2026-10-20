# 04 分支上下文预算 + 祖先摘要
Status: resolved
Type: task
Blocked by: 01

- build_context 字符预算；超出的祖先节点换成缓存摘要；锚文本与当前节点原文
- 回归：深度 4、~61k 字 ⇒ 预算内且含锚文本（审计 P1-3）

## Answer
`server/context.py` assemble(path)：预算（CONTEXT_BUDGET_CHARS，默认 24000）内从近到远放原文，超出的远祖先合成一条【祖先摘要】；当前节点原文 +【本分支锚文本】。摘要缓存表 node_summary（按消息数失效）。
回归：accept_v2 test_deep_branch_context_under_budget（深度 4：约 61.9k → 约 23k 字）、test_context_keeps_current_anchor_verbatim，及摘要不改原文、浅分支不压缩等。
