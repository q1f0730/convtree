#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 层：只做 HTTP/SSE，不写 SQL（走 store.py）。上下文拼装唯一入口 build_context()（D-107）。

启动（项目根）：python -m uvicorn server.main:app --port 8787
"""
from __future__ import print_function

import asyncio
import json
import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictInt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import llm, settings as app_settings, store  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="深挖", version="0.1.0")
app.add_middleware(CORSMiddleware,
                   allow_origins=["http://localhost:8787", "http://127.0.0.1:8787"],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


@app.middleware("http")
async def _no_store(request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/demo") or p.startswith("/api"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.on_event("startup")
def _startup():
    store.init_db()
    if os.environ.get("CONVTREE_NO_AUTOBACKUP") != "1":
        try:
            from tools import backup as _backup  # D-114：每日首次启动自动备份
            _backup.auto_backup()
        except Exception as exc:  # 备份失败不拦启动，只打印（自验失败不留半成品，见 backup.py）
            print("auto_backup skip/fail: %s" % exc)


def build_context(node_id):
    """D-107：上下文 = path(root→self) 上消息；兄弟/子excluded（path 天然不含）。"""
    out = []
    for n in store.node_path(node_id):
        for m in store.list_messages(n["id"]):
            out.append({"role": m["role"], "content": m["content"]})
    return out


def sse(event, data):
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(data, ensure_ascii=False))


class ChatIn(BaseModel):
    text: str
    nodeId: str | None = None
    title: str | None = None


class BranchIn(BaseModel):
    parentId: str
    anchorText: str
    color: str | None = None
    title: str | None = None


class RenameIn(BaseModel):
    title: str


class OverlayIn(BaseModel):
    nodeId: str
    messageId: str
    kind: str
    start: StrictInt
    end: StrictInt
    color: str | None = None
    value: str | None = None
    refNode: str | None = None


class ClearIn(BaseModel):
    nodeId: str
    messageId: str
    start: StrictInt
    end: StrictInt


class NoteIn(BaseModel):
    text: str
    fromNode: str | None = None
    fromName: str | None = None


class SettingsIn(BaseModel):
    provider: str | None = None
    baseUrl: str | None = None
    model: str | None = None
    apiKey: str | None = None
    clearKey: bool = False


@app.get("/api/health")
def health():
    return {"ok": True, "db": store.DB_PATH, "llm": llm.describe()}


@app.get("/api/settings")
def get_settings():
    return {"providers": app_settings.PROVIDERS,
            "config": app_settings.public_config(), "llm": llm.describe()}


@app.post("/api/settings")
def put_settings(body: SettingsIn):
    patch = {}
    if body.provider is not None:
        patch["provider"] = body.provider
    if body.baseUrl is not None:
        patch["base_url"] = body.baseUrl
    if body.model is not None:
        patch["model"] = body.model
    if body.apiKey:  # 空串 = 不改动（前端只有掩码）
        patch["api_key"] = body.apiKey
    if body.clearKey:
        patch["api_key"] = ""
    app_settings.save(patch)
    return {"ok": True, "config": app_settings.public_config()}


@app.post("/api/settings/test")
def test_settings():
    try:
        text, _ = llm.chat_sync([{"role": "user", "content": "ping"}])
        return {"ok": True, "message": "连通（%d字）" % len(text)}
    except Exception as exc:
        return {"ok": False, "message": "%s: %s" % (type(exc).__name__, exc)}


@app.get("/api/nodes")
def nodes():
    return {"nodes": store.list_nodes()}


@app.get("/api/nodes/{node_id}/messages")
def node_messages(node_id: str):
    path = store.node_path(node_id)
    if not path:
        raise HTTPException(404, "节点不存在")
    return {"path": path, "messages": store.list_messages(node_id)}


@app.post("/api/nodes/{node_id}/suggest-title")
def suggest_title(node_id: str):
    """AI 总结分支名：取首个 user 问题浓缩≤12 字；未配模型/失败→截断回落。"""
    msgs = [m for m in store.list_messages(node_id) if m["role"] == "user"]
    if not msgs:
        path = store.node_path(node_id)
        if not path:
            raise HTTPException(404, "节点不存在")
        anchor = path[-1].get("anchor_text") or ""
        if not anchor:
            raise HTTPException(400, "尚无提问")
        title = anchor[:16]
        try:
            return store.rename_node(node_id, title)
        except (LookupError, ValueError) as exc:
            raise HTTPException(404 if isinstance(exc, LookupError) else 400, str(exc))
    q = msgs[0]["content"]
    try:
        text, _ = llm.chat_sync([
            {"role": "user", "content": "用不超过10个字概括下面的问题，只给标题不给解释：\n" + q}])
        title = text.strip().strip("\"'“”‘’：:。.!?！？").replace("\n", " ")
        title = title[:16] or q[:12]
    except Exception:
        title = q[:12] + ("…" if len(q) > 12 else "")
    try:
        return store.rename_node(node_id, title)
    except (LookupError, ValueError) as exc:
        raise HTTPException(404 if isinstance(exc, LookupError) else 400, str(exc))


@app.post("/api/nodes/{node_id}/rename")
def rename(node_id: str, body: RenameIn):
    try:
        return store.rename_node(node_id, body.title)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/nodes/{node_id}/delete")
def delete_node(node_id: str):
    """软删除整支（含全部子孙；D-101：消息行不动，只藏节点）。"""
    try:
        ids = store.delete_subtree(node_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"deleted": ids, "count": len(ids)}


@app.post("/api/overlays")
def post_overlay(body: OverlayIn):
    """标色外挂层落库（D-103 偏移；D-101：只 INSERT，不过原文）。"""
    try:
        return store.add_overlay(body.nodeId, body.messageId, body.kind,
                                 body.start, body.end, body.color, body.value, body.refNode)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/overlays/clear")
def clear_overlay(body: ClearIn):
    """清除选中区间的格式外挂（kind='style'；原文不动）。"""
    try:
        return store.clear_style(body.nodeId, body.messageId, body.start, body.end)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/nodes/{node_id}/overlays")
def node_overlays(node_id: str):
    if not store.node_path(node_id):
        raise HTTPException(404, "节点不存在")
    return {"overlays": store.list_overlays(node_id)}


@app.post("/api/notes")
def post_note(body: NoteIn):
    try:
        return store.add_note(body.text, body.fromNode, body.fromName or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/notes")
def get_notes():
    return {"notes": store.list_notes()}


@app.post("/api/branch")
def branch(body: BranchIn):
    try:
        node = store.create_node(body.parentId, body.title or "未命名",
                                 (body.anchorText or "")[:200], body.color)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"node": node}


@app.post("/api/chat")
async def chat(body: ChatIn):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "内容为空")
    if body.nodeId:
        node = next((n for n in store.list_nodes() if n["id"] == body.nodeId), None)
        if not node:
            raise HTTPException(404, "节点不存在")
        node_id = body.nodeId
    else:
        node_id = store.create_node(None, body.title or "未命名")["id"]
    store.add_message(node_id, "user", text)

    try:
        messages = build_context(node_id)
    except Exception:
        store.add_message(node_id, "assistant", "（生成失败：服务内部错误）")
        raise
    q: asyncio.Queue = asyncio.Queue()

    async def run():
        try:
            text_out, _ = await asyncio.to_thread(llm.chat_sync, messages)
            if not (text_out or "").strip():
                text_out = "（生成失败：模型返回了空内容）"
            mid = store.add_message(node_id, "assistant", text_out)
            step = 3
            for i in range(0, len(text_out), step):
                await q.put(("delta", text_out[i:i + step]))
                await asyncio.sleep(0.02)
            await q.put(("done", {"nodeId": node_id, "messageId": mid["id"]}))
        except Exception as exc:
            err = "（生成失败：%s）" % exc
            mid = store.add_message(node_id, "assistant", err)
            await q.put(("delta", err))
            await q.put(("done", {"nodeId": node_id, "messageId": mid["id"], "error": True}))
        await q.put(None)

    task = asyncio.create_task(run())

    async def gen():
        while True:
            item = await q.get()
            if item is None:
                break
            kind, payload = item
            yield sse(kind, {"text": payload} if kind == "delta" else payload)
    try:
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    finally:
        pass


DEMO_DIR = os.path.join(ROOT, "demo")
if os.path.isdir(DEMO_DIR):
    app.mount("/demo", StaticFiles(directory=DEMO_DIR), name="demo")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(DEMO_DIR, "ink.html"))
