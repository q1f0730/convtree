# CONSTITUTION (lower bound; DREAMS.md is upper bound)

> Meta-rules only. Entity rules: `data.md`. IDs are primary keys. Old history: `E:\_convtree_archive_20260928\`.
---

## C-001 Structured transfer across steps

**规范**：跨步骤/会话/角色传递的信息必须落成结构（文件或账本），不得只存于 prompt 或对话上下文。
**判据**：可寻址文件实体；有版本；变更可追溯来源。
**反例**：规矩只贴对话不落盘。
---

## C-003 Producer ≠ verifier

**规范**：产出的验证不得由产出者执行。
**判据**：验证记录中产出者与验证者身份不同。
**反例**：同一 agent 写代码+写测试+跑测试。
---

## C-005 Invariants as negative assertions

**规范**：「X 不会发生」类规范的验证必须主动尝试违反（全仓扫描违反路径 / 非法输入期望被拒 / 对被消费数据断言），不得只断言正常路径。
**判据**：验证代码含 (a) 危险操作全仓扫描，或 (b) 非法输入期望被拒，或 (c) 被消费数据断言。
**反例**：`assert read_back() == original`。
---

## 修改记录

`CHANGELOG.md`. 同一规范修改超两次 → 降级为设计说明或重写判据。
