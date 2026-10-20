# Agent 工作台：本地 skills + `.scratch/` 工单 + accept_v2 行为测试

用户说"不用问我设计问题"，所以 grill 环节的结论直接写成 ADR，而不是对话。工单放仓库内 `.scratch/`（无远端 issue 系统，随仓提交便于下一位 agent 接手）。新测试单独放 `server/accept_v2.py`，只走 HTTP 公共接口、外部依赖用本地 HTTP 桩；不迁 pytest（少一个依赖，沿用现有 `check()` 风格与退出码约定），旧 `accept_full.py` 不删。

**Considered Options**：迁 pytest（更标准，但要改 392 例的全局顺序状态，本轮收益不抵成本）。
