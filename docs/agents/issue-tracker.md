# Issue tracker：本地 Markdown（`.scratch/`）

本仓没有远端 issue 系统，规格与工单都放在仓库内 `.scratch/`（随仓提交，便于下一位 agent 接手）。

- 一个功能一个目录：`.scratch/<feature-slug>/`
- 规格：`.scratch/<feature-slug>/spec.md`
- 工单：每单一个文件 `.scratch/<feature-slug>/issues/NN-<slug>.md`，从 01 编号
- 顶部 `Status:` 行记状态：`open | claimed | resolved | blocked`；`Blocked by: NN` 记依赖
- 讨论追加在文件底部 `## Comments`；完成时追加 `## Answer`（做了什么 + 提交号 + 回归测试名）
- 流程与角色见 `.agents/skills/engineering/setup-matt-pocock-skills/issue-tracker-local.md`
