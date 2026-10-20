# AGENTS.md — 给接手的 agent

深挖（convtree）：中文学习工具。在回答里**划词开分支**，形成对话树；每个**空间**有自己的资料库，回答带 [n] 引用。
单进程 FastAPI + SQLite + 单文件前端（`demo/ink.html`，C「空间画布」风格，设计令牌见 `../../../design/stitch/design-notes.md`）。

## 跑起来

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn server.main:app --port 8787      # 打开 http://127.0.0.1:8787/
```

没填 key 时自动走内置 **mock** 回复（零花费、可离线）。默认服务商预设 = Inception Mercury（`mercury-3`），key 在界面「设置」里填。

## 测试（改任何东西前后都跑）

```bash
tools/check_all.sh          # 全部：accept_full + accept_v2 + test_ui_logic + check_invariants + ledger verify + ui_e2e
tools/check_all.sh --fast   # 快检（不起浏览器）
tools/install_hooks.sh      # 装 pre-commit 钩子：每次提交前跑快检
```

- `server/accept_v2.py`：**新测试写这里**。只走 HTTP/SSE 公共接口；外部依赖（LLM、嵌入、被抓网页）用本地 HTTP 桩，不 monkeypatch 模块全局。
- `server/accept_full.py`：历史回归，不删不弱化；若把其中的源码 grep 换成行为测试，在提交说明里逐条写明。
- `tools/ui_e2e.py`：真浏览器（playwright + 系统 chromium，无则退出 3=跳过）。

## 铁律

1. **D-101 原文冻结**：`message.content` 只 INSERT，永不 UPDATE（`check_invariants.py` 门禁）。标色/划线/笔记/摘要都是外挂。
2. **key 只存用户配置目录**（`~/.config/convtree/config.json`），不进仓库、`.env`、账本、截图、服务器日志。改 baseUrl 不带新 key ⇒ 旧 key 被清空（ADR-0002）。
3. 公网部署无鉴权：任何新端点都假设"陌生人能调"。服务端校验输入，前端拼 HTML 一律转义。
4. 删除一律软删或二次确认，不静默孤立数据。
5. 中文提交信息，一个纵切片一个提交；不提交嵌套的未跟踪 `convtree/` 目录、`data/`、`backups/`。

## 工作流（skills 在 `.agents/skills/`，来源 mattpocock/skills@49dd158）

grill（`productivity/grill-me`，用户说"不用问"时把决策写 ADR）→ spec（`engineering/to-spec`）→ tickets（`engineering/to-tickets`，落 `.scratch/<feature>/issues/`，见 `docs/agents/issue-tracker.md`）→ implement（`engineering/tdd`：先写红测试，公共接缝，纵切片）→ `engineering/code-review`（规格轴 + 质量轴两轴评审）。

- 术语：`GLOSSARY.md`（说"空间"不说"笔记本/项目"）
- 决策：`docs/adr/`
- 旧治理：`constitution/`、`ledger/`（产物落账见 `python tools/ledger.py -h`）
