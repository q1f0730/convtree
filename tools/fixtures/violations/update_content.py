# -*- coding: utf-8 -*-
"""对照样本 · 阳性：**故意**含违规写法，只用于 tools/check_invariants.py 自测。

本目录（tools/fixtures/violations/）在真实全仓扫描时被跳过；
它存在的唯一目的是让扫描器先命中一个已知的坏样本 ——
否则「全仓零命中」可能只是正则写错了（C-005 的阳性对照要求）。

**每行违规都带 `# expect: <规则ID>` 声明**。这不是装饰：扫描器会逐条核对
「声明的行是否真的被该规则命中」。早期版本只断言「每条规则至少命中一次」，
结果把某条正则**改坏一处**（例如删掉表别名那段，正是 E-0020 的漏检形态）时，
只要同规则的另一种写法还能命中，对照照样报 OK —— 形同虚设。
改坏验证方法见 logs/planning 里第 1 批的审查记录。

禁止把这些写法抄进生产代码。
"""


def bad_update_direct(conn, mid, text):
    """D-101 违规：直接更新 message.content。"""
    conn.execute("UPDATE message SET content=? WHERE id=?", (text, mid))  # expect: D-101


def bad_update_alias(conn, mid, text):
    """D-101 违规：表名后带别名（E-0020 记录的漏检形态）。"""
    conn.execute("UPDATE message m SET content=? WHERE m.id=?", (text, mid))  # expect: D-101


def bad_replace_message(conn, mid, node_id, text, ts):
    """D-101 违规：REPLACE 等于删除+插入，效果上改写了原文。"""
    conn.execute(
        "INSERT OR REPLACE INTO message(id, node_id, role, content, created_at) "  # expect: D-101
        "VALUES(?,?,?,?,?)",
        (mid, node_id, "assistant", text, ts),
    )
