# RUN — 可运行程序总览（2026-09-29 修：产品已落地，旧“尚未开写”作废）

> 产品：`server/`（main/store/llm/settings + accept_full）+ `demo/ink.html`（61KB 单文件前端）+ `data/app.db`。验收库 `accept_*.db` 系测试产物，备份自动跳过。

```bash
python -m uvicorn server.main:app --port 8787   # 启动：开 / 即前端；首次启动超 24h 自动备份（D-114，server/main.py:41）
python server/accept_full.py                    # 全量验收（200+ 例，隔离库 + CONVTREE_NO_AUTOBACKUP=1）
python tools/check_invariants.py --all          # D-101 静态门禁 + 对照自测
python tools/ledger.py verify                   # 账本链完整
node tools/test_ui_logic.js                     # 前端纯逻辑（划词菜单定位/树布局连线/快捷键文案），抽 ink.html 的 <script id="uilogic">
python tools/ui_e2e.py [--shots DIR]            # 真浏览器端到端（需 playwright + 系统 chromium；无浏览器退出 3=跳过）
python tools/ledger.py list [--kind goal] [--tail N]
python tools/backup.py                          # 手动备份（自验 integrity_check + 行数一致，失败删半成品）
```

## 里程碑1 资料底座（E-0071，2026-10-19）

- 笔记本 = `project`；资料 = `source`（txt/md/pdf/docx/网页 URL/粘贴），入库即切块进 `chunk` 表（`start/end` = `source.content` 字符偏移）。
- 检索默认纯 Python BM25（中文字二元组 + 英文词），零重依赖；可选嵌入混合：配置文件里填 `embed_model`（`embed_base_url` / `embed_key` 留空则回落聊天端点），或 `POST /api/settings {"embedModel": ...}`。嵌入失败自动回落 BM25。
- 根节点挂了笔记本的会话（含全部分支子孙，经 path[0] 继承）回答前先检索，SSE 先发 `event: cites`，引用表落 `citation`（`message.content` 不动，D-101）。
- 端点：`POST /api/sources`（form：project_id + file | url | text）、`GET /api/sources?project_id=`（带 chunks 块数）、`DELETE /api/sources/{id}`、`GET /api/nodes/{id}/messages`（每条带 cites）、`GET /api/health`（含 rag 后端）。
