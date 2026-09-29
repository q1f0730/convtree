#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite 存储：node / message。D-101/D-103/D-107/D-108 的承载层。"""
from __future__ import print_function

import os
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
