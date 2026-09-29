# -*- coding: utf-8 -*-
"""对照样本 · 阳性：**故意**含违规写法，只用于 tools/check_invariants.py 自测。

删整支必须软删节点（deleted 标记），消息行一字不许动；
硬删消息行等于销毁原文，判 D-101 违规。

禁止把这些写法抄进生产代码。
"""


def bad_delete_message(conn, nid):
    """D-101 违规：硬删消息行。"""
    conn.execute("DELETE FROM message WHERE node_id=?", (nid,))  # expect: D-101
