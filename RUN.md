# RUN — 可运行程序总览（2026-09-29 修：产品已落地，旧“尚未开写”作废）

> 产品：`server/`（main/store/llm/settings + accept_full）+ `demo/ink.html`（61KB 单文件前端）+ `data/app.db`。验收库 `accept_*.db` 系测试产物，备份自动跳过。

```bash
python -m uvicorn server.main:app --port 8787   # 启动：开 / 即前端；首次启动超 24h 自动备份（D-114，server/main.py:41）
python server/accept_full.py                    # 全量验收（200+ 例，隔离库 + CONVTREE_NO_AUTOBACKUP=1）
python tools/check_invariants.py --all          # D-101 静态门禁 + 对照自测
python tools/ledger.py verify                   # 账本链完整
python tools/ledger.py list [--kind goal] [--tail N]
python tools/backup.py                          # 手动备份（自验 integrity_check + 行数一致，失败删半成品）
```
