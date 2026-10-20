# 03 空间（Space）
Status: resolved
Type: task
Blocked by: 02

- `/api/spaces` CRUD + goal；`/api/projects` 别名保留
- note.project_id 迁移；NULL 数据进"默认空间"（幂等）
- 钻研目标注入系统提示
- 空间总览（卡片：目标、树数、资料数，真计数）、新建/改名/改目标；删除=软删 + 二次确认

## Answer
- 后端：project 加 goal/deleted_at，note 加 project_id；固定 id `prj_000000000000` 的"默认空间"由幂等迁移创建并收容 NULL 根节点/笔记（笔记按来源树归属）。`/api/spaces*` 新接口，`/api/projects*` 别名；`/api/nodes?space_id=`、`/api/notes?space_id=`；钻研目标以【钻研目标】system 消息注入（分支经根继承）；删除=软删+restore，默认空间不可删。
- 前端：侧栏「空间」总览（卡片：目标、树/资料/笔记真计数、进入/编辑/删除二次确认/撤销）；顶栏空间切换+目标；对话列表与笔记按当前空间过滤。
- 回归：accept_v2 test_migration_creates_one_default_space、test_migration_scopes_notes、test_create_space_with_goal、test_space_overview_real_counts、test_nodes_filtered_by_space、test_notes_scoped_per_space、test_space_goal_in_system_prompt、test_delete_space_is_soft_and_restorable；ui_e2e 新增 7 例（空间总览）。
- accept_full 改动：#374「删笔记本清块」改为"删后检索为空"（软删语义）；#379 前端选择器断言改为"空间"；#270 panels 断言放宽允许 space 键。
