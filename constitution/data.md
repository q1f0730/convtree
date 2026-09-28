# data.md — D-1xx

> IDs inherited, primary keys unchanged.

| ID | 规范 | 判据 | 反例 |
|---|---|---|---|
| D-101 | `message.content` 逐字符不可变 | 全仓无更新该列语句 | `UPDATE message SET content=...` |
| D-103 | 单一坐标系 | `start`/`end` = 源串字符偏移 | 渲染后坐标/DOM 偏移 |
| D-107 | 父请求永不含子内容 | 上下文 = `path(root→self)` 消息；兄弟/子 excluded | 子摘要注入父对话 |
| D-108 | 重生成 = 新兄弟版本 | 新行 + `ordinal`；上下文每组取最新 | `UPDATE` 已有 assistant 行 |
| D-114 | 每日首次启动自动备份；自验通过才算数 | 超 24h 必执行；`integrity_check='ok'` 且行数一致，否则删份报错 | 只拷不验 |

机械检查：`tools/check_invariants.py --all`（现仅 D-101；新条目落地时同改追加规则 + 阳性样本）。
