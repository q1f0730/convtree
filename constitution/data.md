# data.md — D-1xx

> IDs inherited, primary keys unchanged. 2026-09-29 精简：只留两条不变量
> （D-101 静态门禁 + D-114 自验门禁）；D-103/D-107/D-108 降级为设计说明
> （运行时保证，非静态门禁；原因见各条）。C-001/C-003/C-005 不动（门禁的轨道）。

## 不变量（`--all` 变红即拦）

| ID | 规范 | 判据 | 反例 |
|---|---|---|---|
| D-101 | `message.content` 逐字符不可变 | 全仓无更新该列语句 | `UPDATE message SET content=...` |
| D-114 | 每日首次启动自动备份；自验通过才算数 | 超 24h 必执行；`integrity_check='ok'` 且行数一致，否则删份报错 | 只拷不验 |

## 设计说明（运行时保证，不进静态扫描；实现变了只改这里，不拦 `--all`）

| ID | 现状 | 运行时保证位置 |
|---|---|---|
| D-103 | 降级：`start`/`end` = 源串字符偏移是运行时属性，正则扫不出真假 | `server/store.py:200` 越界即拒 + `server/main.py:76` StrictInt 堵 bool；`server/accept_full.py` 划线/排版越界 400 例 |
| D-107 | 降级：上下文 = `path(root→self)` 是构造性保证，扫字符串只能自欺 | `server/main.py:52` 唯一入口经 `node_path`；`accept_full.py` 兄弟隔离 + 标色/笔记不进上下文 |
| D-108 | 降级 + 未实现：`message` 表无 `ordinal` 列，无重生成入口 | 实现后再立判据；在那之前任何静态规则都是假门禁 |

机械检查：`tools/check_invariants.py --all`（仅 D-101 静态规则 + 阳性/反向对照；
D-114 验 `backups/*/manifest.json` 自验字段 + `server/main.py:41` 启动接线；
D-103/D-107 跑 `server/accept_full.py` 运行时例，不进扫描器）。
