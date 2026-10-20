# 空间（Space）拥有对话树、资料与笔记；表名不改

借鉴 AnythingLLM 的 workspace：用户面的"笔记本/项目"统一改叫**空间**，一个空间 = 一个钻研课题，有名称和可选钻研目标（注入系统提示）。笔记原本全局（`note` 无 project_id），现加 `project_id` 并按空间过滤。旧数据（根节点/笔记 project_id 为 NULL）由幂等迁移收进"默认空间"。**数据库表与字段仍叫 `project`**，新增 `/api/spaces*` 接口，旧 `/api/projects*` 保留为别名——改表名要迁移外键与历史库，收益只有命名一致。删除空间改为软删（`project.deleted_at`），其下数据原样保留、可恢复，不再硬删资料、不再把会话置 NULL（修审计 P2-2）。

**Consequences**：代码里 `project` 与界面"空间"是同一个东西，GLOSSARY 写明；"不使用笔记本"的游离对话不再存在，新对话总在当前空间。
