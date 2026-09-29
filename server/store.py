#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite 存储：node / message。D-101/D-103/D-107/D-108 的承载层。"""
from __future__ import print_function

import json
import os
import re
import sqlite3
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("APP_DB") or os.path.join(ROOT, "data", "app.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS node (
  id          TEXT PRIMARY KEY,
  parent_id   TEXT REFERENCES node(id),
  title       TEXT NOT NULL,
  anchor_text TEXT,
  color       TEXT,
  created_at  INTEGER NOT NULL,
  deleted     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS message (
  id         TEXT PRIMARY KEY,
  node_id    TEXT NOT NULL REFERENCES node(id),
  role       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_node ON message(node_id, created_at);
CREATE TABLE IF NOT EXISTS overlay (
  id         TEXT PRIMARY KEY,
  node_id    TEXT NOT NULL REFERENCES node(id),
  message_id TEXT NOT NULL REFERENCES message(id),
  kind       TEXT NOT NULL,
  start      INTEGER NOT NULL,
  end_pos    INTEGER NOT NULL,
  color      TEXT,
  value      TEXT,
  ref_node   TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ov_node ON overlay(node_id);
CREATE TABLE IF NOT EXISTS note (
  id         TEXT PRIMARY KEY,
  text       TEXT NOT NULL,
  from_node  TEXT,
  from_name  TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
"""


def connect():
    d = os.path.dirname(DB_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(node)")]
        if "deleted" not in cols:  # 老库迁移：删整支用的软删除标
            conn.execute("ALTER TABLE node ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        ocols = [r[1] for r in conn.execute("PRAGMA table_info(overlay)")]
        if ocols and "value" not in ocols:  # 老库迁移：style 格式值
            conn.execute("ALTER TABLE overlay ADD COLUMN value TEXT")
        conn.commit()
    finally:
        conn.close()


def uid(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


def now_ms():
    return int(time.time() * 1000)


def create_node(parent_id, title, anchor_text=None, color=None):
    nid, ts = uid("nd"), now_ms()
    conn = connect()
    try:
        if parent_id and not conn.execute(
                "SELECT 1 FROM node WHERE id=? AND deleted=0", (parent_id,)).fetchone():
            raise LookupError("父节点不存在")
        conn.execute("INSERT INTO node(id,parent_id,title,anchor_text,color,created_at) VALUES(?,?,?,?,?,?)",
                     (nid, parent_id, title, anchor_text, color, ts))
        conn.commit()
    finally:
        conn.close()
    return {"id": nid, "parent_id": parent_id, "title": title,
            "anchor_text": anchor_text, "color": color}


def list_nodes():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM node WHERE deleted=0 ORDER BY created_at")]
    finally:
        conn.close()


def node_path(node_id):
    """根→自身 id 链（D-107 上下文的唯一来源）。已删节点视为不存在。"""
    out, seen, cur = [], set(), node_id
    conn = connect()
    try:
        while cur and cur not in seen:
            seen.add(cur)
            row = conn.execute("SELECT * FROM node WHERE id=?", (cur,)).fetchone()
            if not row:
                break
            out.append(dict(row))
            cur = row["parent_id"]
    finally:
        conn.close()
    if not out or any(r.get("deleted") for r in out):
        return []
    return list(reversed(out))


def add_message(node_id, role, content):
    mid, ts = uid("msg"), now_ms()
    conn = connect()
    try:
        conn.execute("INSERT INTO message(id,node_id,role,content,created_at) VALUES(?,?,?,?,?)",
                     (mid, node_id, role, content, ts))
        conn.commit()
    finally:
        conn.close()
    return {"id": mid, "node_id": node_id, "role": role, "content": content}


def list_messages(node_id):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM message WHERE node_id=? ORDER BY created_at", (node_id,))]
    finally:
        conn.close()


def rename_node(node_id, title):
    if not (title or "").strip():
        raise ValueError("标题为空")
    conn = connect()
    try:
        cur = conn.execute("UPDATE node SET title=? WHERE id=? AND deleted=0",
                           (title.strip(), node_id))
        conn.commit()
        if cur.rowcount == 0:
            raise LookupError("节点不存在")
    finally:
        conn.close()
    return {"id": node_id, "title": title.strip()}


_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_STYLE_KEYS = {"b", "i", "size", "font", "op"}


def _check_style(value):
    """格式值：JSON 子集，非法即拒。返回规范串。"""
    try:
        obj = json.loads(value) if isinstance(value, str) else None
    except Exception:
        raise ValueError("value 非法")
    if not isinstance(obj, dict) or not obj or set(obj) - _STYLE_KEYS:
        raise ValueError("value 非法")
    if "b" in obj and obj["b"] not in (True, False, 0, 1):
        raise ValueError("value 非法")
    if "i" in obj and obj["i"] not in (True, False, 0, 1):
        raise ValueError("value 非法")
    if "size" in obj and obj["size"] not in ("sm", "lg"):
        raise ValueError("value 非法")
    if "font" in obj and obj["font"] not in ("song", "kai", "mono"):
        raise ValueError("value 非法")
    if "op" in obj and (not isinstance(obj["op"], (int, float))
                        or isinstance(obj["op"], bool)
                        or not (30 <= obj["op"] <= 100)):
        raise ValueError("value 非法")
    return json.dumps(obj, sort_keys=True)


def add_overlay(node_id, message_id, kind, start, end, color, value=None, ref_node=None):
    """标色/格式外挂层（D-103）：start/end = 消息原文字符偏移；原文一字不动。"""
    if kind not in ("mark", "ul", "style"):
        raise ValueError("kind 非法")
    if kind == "style":
        if value is None:
            raise ValueError("value 非法")
        value = _check_style(value)
        color = None
    else:
        if value is not None:
            raise ValueError("value 非法")
        if not _COLOR.match(color or ""):
            raise ValueError("color 非法")
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM node WHERE id=? AND deleted=0", (node_id,)).fetchone():
            raise LookupError("节点不存在")
        mrow = conn.execute("SELECT content FROM message WHERE id=? AND node_id=?",
                            (message_id, node_id)).fetchone()
        if not mrow:
            raise LookupError("消息不存在")
        size = len(mrow["content"] or "")
        if (not isinstance(start, int) or not isinstance(end, int)
                or isinstance(start, bool) or isinstance(end, bool)
                or not (0 <= start < end <= size)):
            raise ValueError("偏移越界")
        oid, ts = uid("ov"), now_ms()
        conn.execute("INSERT INTO overlay(id,node_id,message_id,kind,start,end_pos,color,value,ref_node,created_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (oid, node_id, message_id, kind, start, end, color, value, ref_node, ts))
        conn.commit()
    finally:
        conn.close()
    return {"id": oid, "node_id": node_id, "message_id": message_id, "kind": kind,
            "start": start, "end": end, "color": color, "value": value, "ref_node": ref_node}


def clear_style(node_id, message_id, start, end):
    """清除选中区间的格式外挂（只动 overlay，不动原文）。返回清掉的行数。"""
    if (not isinstance(start, int) or not isinstance(end, int)
            or isinstance(start, bool) or isinstance(end, bool)
            or not (0 <= start < end)):
        raise ValueError("偏移越界")
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM node WHERE id=? AND deleted=0", (node_id,)).fetchone():
            raise LookupError("节点不存在")
        if not conn.execute("SELECT 1 FROM message WHERE id=? AND node_id=?",
                            (message_id, node_id)).fetchone():
            raise LookupError("消息不存在")
        cur = conn.execute("DELETE FROM overlay WHERE node_id=? AND message_id=? AND kind='style'"
                           " AND start<? AND end_pos>?", (node_id, message_id, end, start))
        conn.commit()
        return {"cleared": cur.rowcount}
    finally:
        conn.close()


def list_overlays(node_id):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id,node_id,message_id,kind,start,end_pos AS \"end\",color,value,ref_node,created_at"
            " FROM overlay WHERE node_id=? ORDER BY created_at", (node_id,))]
    finally:
        conn.close()


def add_note(text, from_node=None, from_name=""):
    if not (text or "").strip():
        raise ValueError("内容为空")
    nid, ts = uid("note"), now_ms()
    conn = connect()
    try:
        conn.execute("INSERT INTO note(id,text,from_node,from_name,created_at) VALUES(?,?,?,?,?)",
                     (nid, text.strip(), from_node, from_name or "", ts))
        conn.commit()
    finally:
        conn.close()
    return {"id": nid, "text": text.strip(), "from_node": from_node,
            "from_name": from_name or "", "created_at": ts}


def list_notes():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM note ORDER BY created_at DESC")]
    finally:
        conn.close()


def delete_subtree(node_id):
    """软删除整支：节点打 deleted 标，消息行一字不动（D-101 冻结原文）。
    返回被隐藏的 id 列表（含自身）。"""
    conn = connect()
    try:
        row = conn.execute("SELECT id FROM node WHERE id=? AND deleted=0", (node_id,)).fetchone()
        if not row:
            raise LookupError("节点不存在")
        ids, stack = [], [node_id]
        while stack:
            cur = stack.pop()
            ids.append(cur)
            stack.extend(r[0] for r in conn.execute(
                "SELECT id FROM node WHERE parent_id=? AND deleted=0", (cur,)))
        conn.execute("UPDATE node SET deleted=1 WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
        conn.commit()
    finally:
        conn.close()
    return ids
