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
CREATE TABLE IF NOT EXISTS project (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS source (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(id),
  name       TEXT NOT NULL,
  content    TEXT NOT NULL,
  type       TEXT NOT NULL DEFAULT 'text',
  size       INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_project ON source(project_id);
CREATE TABLE IF NOT EXISTS chunk (
  id         TEXT PRIMARY KEY,
  source_id  TEXT NOT NULL REFERENCES source(id),
  project_id TEXT NOT NULL,
  idx        INTEGER NOT NULL,
  start      INTEGER NOT NULL,
  end_pos    INTEGER NOT NULL,
  text       TEXT NOT NULL,
  tokens     TEXT NOT NULL,
  tok_len    INTEGER NOT NULL,
  embedding  TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunk_project ON chunk(project_id);
CREATE INDEX IF NOT EXISTS idx_chunk_source ON chunk(source_id);
CREATE TABLE IF NOT EXISTS citation (
  id          TEXT PRIMARY KEY,
  message_id  TEXT NOT NULL REFERENCES message(id),
  n           INTEGER NOT NULL,
  source_id   TEXT,
  source_name TEXT NOT NULL,
  chunk_id    TEXT,
  text        TEXT NOT NULL,
  start       INTEGER NOT NULL,
  end_pos     INTEGER NOT NULL,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cite_msg ON citation(message_id);
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
        if "project_id" not in cols:  # 老库迁移：会话归属项目
            conn.execute("ALTER TABLE node ADD COLUMN project_id TEXT")
        mcols = [r[1] for r in conn.execute("PRAGMA table_info(message)")]
        if "status" not in mcols:  # 迁移：失败回复标 status='error'，不进上下文（审计 P1-4）
            conn.execute("ALTER TABLE message ADD COLUMN status TEXT NOT NULL DEFAULT 'ok'")
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


def create_node(parent_id, title, anchor_text=None, color=None, project_id=None):
    nid, ts = uid("nd"), now_ms()
    conn = connect()
    try:
        if parent_id and not conn.execute(
                "SELECT 1 FROM node WHERE id=? AND deleted=0", (parent_id,)).fetchone():
            raise LookupError("父节点不存在")
        conn.execute("INSERT INTO node(id,parent_id,title,anchor_text,color,project_id,created_at) VALUES(?,?,?,?,?,?,?)",
                     (nid, parent_id, title, anchor_text, color, project_id, ts))
        conn.commit()
    finally:
        conn.close()
    return {"id": nid, "parent_id": parent_id, "title": title,
            "anchor_text": anchor_text, "color": color, "project_id": project_id}


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


def add_message(node_id, role, content, status="ok"):
    if status not in ("ok", "error"):
        raise ValueError("status 非法")
    mid, ts = uid("msg"), now_ms()
    conn = connect()
    try:
        conn.execute("INSERT INTO message(id,node_id,role,content,created_at,status) VALUES(?,?,?,?,?,?)",
                     (mid, node_id, role, content, ts, status))
        conn.commit()
    finally:
        conn.close()
    return {"id": mid, "node_id": node_id, "role": role, "content": content, "status": status}


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
_NODE_ID = re.compile(r"^nd_[0-9a-f]{12}$")
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
        if ref_node is not None:
            if not _NODE_ID.match(ref_node or "") or not conn.execute(
                    "SELECT 1 FROM node WHERE id=? AND deleted=0", (ref_node,)).fetchone():
                raise ValueError("refNode 非法")
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


def delete_note(note_id):
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM note WHERE id=?", (note_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise LookupError("笔记不存在")
    finally:
        conn.close()
    return {"id": note_id}


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


def create_project(name):
    if not (name or "").strip():
        raise ValueError("名称为空")
    pid, ts = uid("prj"), now_ms()
    conn = connect()
    try:
        conn.execute("INSERT INTO project(id,name,created_at) VALUES(?,?,?)",
                     (pid, name.strip(), ts))
        conn.commit()
    finally:
        conn.close()
    return {"id": pid, "name": name.strip(), "created_at": ts}


def list_projects():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM project ORDER BY created_at")]
    finally:
        conn.close()


def rename_project(project_id, name):
    if not (name or "").strip():
        raise ValueError("名称为空")
    conn = connect()
    try:
        cur = conn.execute("UPDATE project SET name=? WHERE id=?", (name.strip(), project_id))
        conn.commit()
        if cur.rowcount == 0:
            raise LookupError("项目不存在")
    finally:
        conn.close()
    return {"id": project_id, "name": name.strip()}


def delete_project(project_id):
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM project WHERE id=?", (project_id,)).fetchone():
            raise LookupError("项目不存在")
        conn.execute("DELETE FROM chunk WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM source WHERE project_id=?", (project_id,))
        conn.execute("UPDATE node SET project_id=NULL WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM project WHERE id=?", (project_id,))
        conn.commit()
    finally:
        conn.close()
    return {"id": project_id}


def add_source(project_id, name, content, type="text", size=0):
    if not (name or "").strip():
        raise ValueError("名称为空")
    if not (content or "").strip():
        raise ValueError("内容为空")
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM project WHERE id=?", (project_id,)).fetchone():
            raise LookupError("项目不存在")
        sid, ts = uid("src"), now_ms()
        conn.execute("INSERT INTO source(id,project_id,name,content,type,size,created_at) VALUES(?,?,?,?,?,?,?)",
                     (sid, project_id, name.strip(), content, type, size, ts))
        conn.commit()
    finally:
        conn.close()
    return {"id": sid, "project_id": project_id, "name": name.strip(),
            "content": content, "type": type, "size": size, "created_at": ts}


def list_sources(project_id):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT s.id,s.project_id,s.name,s.type,s.size,s.created_at,"
            " (SELECT COUNT(*) FROM chunk c WHERE c.source_id=s.id) AS chunks"
            " FROM source s WHERE s.project_id=? ORDER BY s.created_at",
            (project_id,))]
    finally:
        conn.close()


def delete_source(source_id):
    conn = connect()
    try:
        conn.execute("DELETE FROM chunk WHERE source_id=?", (source_id,))
        cur = conn.execute("DELETE FROM source WHERE id=?", (source_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise LookupError("资料不存在")
    finally:
        conn.close()
    return {"id": source_id}


def set_node_project(node_id, project_id):
    conn = connect()
    try:
        cur = conn.execute("UPDATE node SET project_id=? WHERE id=? AND deleted=0",
                           (project_id, node_id))
        conn.commit()
        if cur.rowcount == 0:
            raise LookupError("节点不存在")
    finally:
        conn.close()
    return {"id": node_id, "project_id": project_id}


def get_project(project_id):
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def get_source(source_id):
    conn = connect()
    try:
        r = conn.execute("SELECT * FROM source WHERE id=?", (source_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


# ── 检索块（rag.py 的持久层；偏移 = source.content 的字符偏移）──

def replace_chunks(source_id, project_id, rows):
    """rows: [{idx,start,end,text,tokens(list),embedding(list|None)}]。先删后插（重索引不留孤儿）。"""
    conn = connect()
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM chunk WHERE source_id=?", (source_id,))
        for r in rows:
            emb = r.get("embedding")
            conn.execute("INSERT INTO chunk(id,source_id,project_id,idx,start,end_pos,text,tokens,tok_len,embedding)"
                         " VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (uid("ck"), source_id, project_id, r["idx"], r["start"], r["end"], r["text"],
                          " ".join(r["tokens"]), len(r["tokens"]),
                          json.dumps(emb) if emb else None))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return len(rows)


def list_chunks(project_id):
    """某笔记本的全部块（含资料名），供 BM25/向量打分。"""
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT c.id,c.source_id,c.project_id,c.idx,c.start,c.end_pos AS \"end\",c.text,c.tokens,c.tok_len,"
            " c.embedding,s.name AS source_name FROM chunk c JOIN source s ON s.id=c.source_id"
            " WHERE c.project_id=? ORDER BY c.source_id,c.idx", (project_id,))]
    finally:
        conn.close()


def set_chunk_embeddings(pairs):
    """pairs: [(chunk_id, vector)]。只写 chunk.embedding，不碰消息。"""
    conn = connect()
    try:
        for cid, vec in pairs:
            conn.execute("UPDATE chunk SET embedding=? WHERE id=?", (json.dumps(vec), cid))
        conn.commit()
    finally:
        conn.close()


def delete_chunks(source_id):
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM chunk WHERE source_id=?", (source_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ── 引用（回答的 [n] → 资料片段；独立表，message.content 不动 D-101）──

def add_citations(message_id, cites):
    ts = now_ms()
    conn = connect()
    try:
        if not conn.execute("SELECT 1 FROM message WHERE id=?", (message_id,)).fetchone():
            raise LookupError("消息不存在")
        for c in cites:
            conn.execute("INSERT INTO citation(id,message_id,n,source_id,source_name,chunk_id,text,start,end_pos,created_at)"
                         " VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (uid("ct"), message_id, int(c["n"]), c.get("source_id"), c.get("source_name") or "",
                          c.get("chunk_id"), c.get("text") or "", int(c.get("start") or 0),
                          int(c.get("end") or 0), ts))
        conn.commit()
    finally:
        conn.close()
    return len(cites)


def list_citations(node_id):
    """某节点全部消息的引用，按 message_id 分组。"""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT c.message_id,c.n,c.source_id,c.source_name,c.chunk_id,c.text,c.start,c.end_pos AS \"end\""
            " FROM citation c JOIN message m ON m.id=c.message_id WHERE m.node_id=? ORDER BY c.message_id,c.n",
            (node_id,)).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        d = dict(r)
        out.setdefault(d.pop("message_id"), []).append(d)
    return out
