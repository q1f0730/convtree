# -*- coding: utf-8 -*-
"""对照样本 · 反向：含被禁的**词**，但全是散文/说明，不是 SQL。

扫描器若命中本文件，说明它把文档当代码（旧脚本踩过的坑），
此时「全仓零命中」这类结论一律不可信。

本目录（tools/fixtures/clean/）在真实全仓扫描时同样被跳过。
"""

# 反向对照 1：有 UPDATE 与 message，但后面接的是点号与中文，不构成带 SET 的语句
DOC_1 = "只 INSERT。不存在任何 UPDATE message.content 的路径。"

# 反向对照 2：DELETE 后面接的是中文，不是 WHERE / 分号 / 引号 —— 不构成语句
DOC_2 = "本系统没有 DELETE FROM overlay 的必要：撤回以追加事件表达，行永不消失。"

# 反向对照 3：有 UPDATE overlay，但没有 SET 与 message_id 赋值
DOC_3 = "锚点归属一经创建即固定，因此不存在 UPDATE overlay 改写归属的代码路径。"


def read_only_queries(conn, mid):
    """正常只读路径：这些不该被任何规则命中。"""
    conn.execute("SELECT content FROM message WHERE id=?", (mid,))
    conn.execute("SELECT id, message_id, start, end FROM overlay WHERE message_id=?", (mid,))
    conn.execute("INSERT INTO message(id, node_id, role, content, created_at) VALUES(?,?,?,?,?)",
                 ("msg_x", "nd_x", "user", "原文", 0))
    # usage 允许 REPLACE：它不是原文，不在 D-101 / D-113 的射程内
    conn.execute("INSERT OR REPLACE INTO usage(message_id, input_tokens) VALUES(?,?)", ("msg_x", 1))
