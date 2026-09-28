# RUN — 可运行程序总览

> 本项目所有能跑的东西都在这里。命令一律在项目根（`convtree/`）执行。

## 1. 产品（1 个）

| 程序 | 命令 | 说明 |
|---|---|---|
| 笔记应用 | `python -m uvicorn server.main:app --port 8787` | 打开 `http://127.0.0.1:8787/`，界面即 `demo/app.html` |

## 2. 验收（12 个：后端 9 + 前端 3）

```bash
python server/smoke_test.py          # 冒烟，先跑它
python server/accept_step1.py        # 建库/并发/外键/分支地基（已知 2 项 Windows 并发建库失败，非代码问题）
python server/accept_step2.py        # 聊天断线落库、幂等、上下文隔离 D-107
python server/accept_step3.py        # 备份、版本折叠、迁移
python server/accept_step4.py        # 原文不可变、坐标系
python server/accept_step5.py        # 导出
python server/accept_step6.py        # 粘贴导入
python server/accept_step7.py        # 笔记（26 项断言）
python server/accept_settings.py     # 设置接口（含系统提示词）
```

```bash
node demo/accept_render.js                              # 无头渲染比对（需 npm install）
node demo/accept_visual.js --url http://127.0.0.1:8788/ # 真浏览器视觉（需先起隔离服务）
node demo/accept_e2e.js    --url http://127.0.0.1:8788/ # 端到端（需先起隔离服务）
```

隔离服务（跑后两个前端验收前，先起这个，否则打到真实付费 API）：

```bash
APP_DB=<临时库> CONVTREE_CONFIG=<不存在的路径> CONVTREE_NO_AUTOBACKUP=1 \
  python -m uvicorn server.main:app --port 8788
```

## 3. 治理工具（10 个）

```bash
python tools/ledger.py verify                    # 账本链完整（每批收尾必跑）
python tools/ledger.py list [--kind goal] [--tail N]
python tools/rsi.py scan|diagnose|propose|cycle  # 改进循环
python tools/check_invariants.py --all           # 不变量扫描（每次写入可跑）
python tools/check_status_claims.py --self-test  # 先证判据有分辨力，再跑正式检查
python tools/check_status_claims.py
python tools/backup.py                           # 手动备份
python tools/migrate_1_1.py                      # schema 1.0 → 1.1 迁移
python tools/decide.py / tools/new_log.py        # 判定 / 日志骨架
python tools/judge_changes.py                    # 语义判定（需 git diff；本项目无 git 时用 --files + --allow-non-diff，且不作放行依据）
```

## 4. 不是程序（别直接跑）

- `server/store.py`、`exporters.py`、`imports.py`、`llm.py`、`settings.py`：被 `main.py` import 的库。
- `demo/app.html`：由后端托管在 `/` 的单文件前端。
