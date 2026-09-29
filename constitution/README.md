# constitution/README

Files: `CONSTITUTION.md` (C-001/C-003/C-005, 不动), `data.md` (不变量 D-101/D-114 + 设计说明 D-103/D-107/D-108), `CHANGELOG.md`.

三道真闸（只认执行结果，不认口头）：

```bash
python tools/check_invariants.py --all   # D-101：阳性对照全命中 + 全仓零命中 + 无豁免
python tools/ledger.py verify            # 账本链：链完整 + 字段合法 + 无悬空（C-003 门禁只对新 verification 记录生效）
python tools/backup.py                   # D-114：自验 integrity_check='ok' + 行数一致，失败删半成品
```

New entries must pass before merge: falsifiable statement, no degree words; concrete violation exhibitable; binary violation; inputs directly readable. Fail ⇒ design note, not invariant.
