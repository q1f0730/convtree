# RUN — 可运行程序总览（B 重立，2026-09-28）

> 产品尚未开写（`server/`、`demo/` 不存在，待 grill 分支 2 定 MVP）。目前能跑的只有治理工具。

```bash
python tools/check_invariants.py --all   # 不变量扫描（D-101 + 对照自测）
python tools/ledger.py verify            # 账本链完整
python tools/ledger.py list [--kind goal] [--tail N]
python tools/backup.py                   # 手动备份（D-114 执行器）
```

产品第一个可运行版本落地时，按 `versions/README.md` 旧例重建版本建档（已随旧项目归档）。
