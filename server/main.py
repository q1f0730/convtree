#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 层：只做 HTTP/SSE，不写 SQL（走 store.py）。上下文拼装唯一入口 build_context()（D-107）。

启动（项目根）：python -m uvicorn server.main:app --port 8787
"""
from __future__ import print_function

import asyncio
import json
import os
import subprocess
import sys

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictInt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import llm, settings as app_settings, store  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _build_tag():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5, cwd=ROOT)
        return (out.stdout or "").strip() or "dev"
    except Exception:
        return "dev"


BUILD = _build_tag()

app = FastAPI(title="深挖", version="0.1.0")
app.add_middleware(CORSMiddleware,
                   allow_origins=["http://localhost:8787", "http://127.0.0.1:8787"],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


_DEFAULT_HOSTS = ("localhost", "127.0.0.1", "::1", "testserver")


def host_allowed(host_header):
    """Host 头白名单（防 DNS rebinding，ADR-0002）。默认本机；CONVTREE_ALLOWED_HOSTS 追加，
    逗号分隔，支持 *.example.com 通配，单独一个 * 表示关闭校验。"""
    h = (host_header or "").strip().lower()
    if h.startswith("["):
        h = h[1:h.find("]")] if "]" in h else h[1:]
    elif h.count(":") == 1:
        h = h.split(":", 1)[0]
    if not h:
        return False
    extra = [x.strip().lower() for x in app_settings.env("CONVTREE_ALLOWED_HOSTS").split(",") if x.strip()]
    for a in list(_DEFAULT_HOSTS) + extra:
        a = a.split("://", 1)[-1].rstrip("/")
        if a.count(":") == 1:
            a = a.split(":", 1)[0]
        if a == "*" or a == h or (a.startswith("*.") and h.endswith(a[1:]) and len(h) > len(a) - 1):
            return True
    return False


@app.middleware("http")
async def _no_store(request, call_next):
    if not host_allowed(request.headers.get("host")):
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("Host 不在允许列表（设置 CONVTREE_ALLOWED_HOSTS）", status_code=400)
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


GROUNDING_RULES = ("请优先依据上面的【参考资料】回答。凡用到某条资料，在句末用方括号标注编号，如 [1] 或 [1][3]；"
                   "只能使用上面出现过的编号，不要编造编号。资料里没有的内容要明说「资料未提及」，再视需要补充常识。")


def build_context(node_id, query=None, with_cites=False):
    """D-107：上下文 = path(root→self) 上消息；兄弟/子excluded（path 天然不含）。
    有 query 且根节点（path[0]）挂了笔记本时，检索结果前置为带 [n] 编号的 system 消息；
    分支子节点经 path[0] 继承根的笔记本。with_cites=True 时返回 (messages, cites)。"""
    from server import context as ctx_mod
    cites = []
    path = store.node_path(node_id)
    out = ctx_mod.assemble(path)  # ADR-0004：预算内原文，超出的远祖先换缓存摘要
    if query and path:
        try:
            from server import rag
            pid = path[0].get("project_id")
            if pid:
                anchor = path[-1].get("anchor_text") or ""
                q = query if not anchor else (query + "\n" + anchor)
                hits = rag.retrieve(pid, q)
                if hits:
                    lines = ["【参考资料】"]
                    for i, h in enumerate(hits, 1):
                        lines.append("[%d] 《%s》：%s" % (i, h.get("source_name", ""), h["text"]))
                        cites.append({"n": i, "source_id": h.get("source_id"),
                                      "source_name": h.get("source_name", ""),
                                      "chunk_id": h.get("chunk_id"), "text": h["text"],
                                      "start": h.get("start", 0), "end": h.get("end", 0)})
                    lines.append(GROUNDING_RULES)
                    out.insert(0, {"role": "system", "content": "\n".join(lines)})
        except Exception as exc:
            print("rag retrieve failed: %s" % exc)  # 检索失败不拦聊天，但留痕
    if path:
        space = store.get_project(path[0].get("project_id")) if path[0].get("project_id") else None
        goal = (space or {}).get("goal") or ""
        if goal:
            out.insert(0, {"role": "system", "content": "【钻研目标】用户在空间「%s」里钻研：%s。回答时围绕这个目标组织重点。"
                           % (space.get("name", ""), goal)})
    return (out, cites) if with_cites else out


def sse(event, data):
    return "event: %s\ndata: %s\n\n" % (event, json.dumps(data, ensure_ascii=False))


class ChatIn(BaseModel):
    text: str
    nodeId: str | None = None
    title: str | None = None
    projectId: str | None = None
    spaceId: str | None = None


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
    spaceId: str | None = None
    projectId: str | None = None


class SettingsIn(BaseModel):
    provider: str | None = None
    baseUrl: str | None = None
    model: str | None = None
    apiKey: str | None = None
    clearKey: bool = False
    embedBaseUrl: str | None = None
    embedModel: str | None = None
    embedKey: str | None = None
    clearEmbedKey: bool = False


@app.get("/api/health")
def health():
    from server import rag
    return {"ok": True, "db": store.DB_PATH, "llm": llm.describe(), "rag": rag.backend()}


@app.get("/api/version")
def version():
    return {"build": BUILD}


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
    if body.embedBaseUrl is not None:
        patch["embed_base_url"] = body.embedBaseUrl
    if body.embedModel is not None:
        patch["embed_model"] = body.embedModel
    if body.embedKey:
        patch["embed_key"] = body.embedKey
    if body.clearEmbedKey:
        patch["embed_key"] = ""
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
def nodes(space_id: str | None = None):
    return {"nodes": store.list_nodes(space_id or None)}


@app.get("/api/nodes/{node_id}/messages")
def node_messages(node_id: str):
    path = store.node_path(node_id)
    if not path:
        raise HTTPException(404, "节点不存在")
    cites = store.list_citations(node_id)
    msgs = store.list_messages(node_id)
    for m in msgs:
        m["cites"] = cites.get(m["id"], [])
    return {"path": path, "messages": msgs}


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
        return store.add_note(body.text, body.fromNode, body.fromName or "",
                              project_id=body.spaceId or body.projectId or None)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: str):
    try:
        return store.delete_note(note_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/notes")
def get_notes(space_id: str | None = None):
    return {"notes": store.list_notes(space_id or None)}


class SpaceIn(BaseModel):
    name: str
    goal: str | None = None


class SpacePatch(BaseModel):
    name: str | None = None
    goal: str | None = None


def _space_err(exc):
    return HTTPException(404 if isinstance(exc, LookupError) else 400, str(exc))


# 空间（ADR-0003）。表名仍是 project；/api/projects* 为兼容别名，行为相同。
@app.get("/api/spaces")
def list_spaces_ep():
    return {"spaces": store.list_projects(with_counts=True)}


@app.post("/api/spaces")
def create_space_ep(body: SpaceIn):
    try:
        return store.create_project(body.name, body.goal or "")
    except ValueError as exc:
        raise _space_err(exc)


@app.get("/api/spaces/{space_id}")
def get_space_ep(space_id: str):
    sp = next((x for x in store.list_projects(with_counts=True) if x["id"] == space_id), None)
    if not sp:
        raise HTTPException(404, "空间不存在")
    return sp


@app.put("/api/spaces/{space_id}")
def update_space_ep(space_id: str, body: SpacePatch):
    try:
        return store.update_project(space_id, name=body.name, goal=body.goal)
    except (LookupError, ValueError) as exc:
        raise _space_err(exc)


@app.delete("/api/spaces/{space_id}")
def delete_space_ep(space_id: str):
    try:
        return store.delete_project(space_id)
    except (LookupError, ValueError) as exc:
        raise _space_err(exc)


@app.post("/api/spaces/{space_id}/restore")
def restore_space_ep(space_id: str):
    try:
        return store.restore_project(space_id)
    except (LookupError, ValueError) as exc:
        raise _space_err(exc)


@app.post("/api/projects")
def create_project_ep(body: SpaceIn):
    return create_space_ep(body)


@app.get("/api/projects")
def list_projects_ep():
    return {"projects": store.list_projects(with_counts=True)}


@app.put("/api/projects/{project_id}")
def rename_project_ep(project_id: str, body: SpacePatch):
    return update_space_ep(project_id, body)


@app.delete("/api/projects/{project_id}")
def delete_project_ep(project_id: str):
    return delete_space_ep(project_id)


@app.post("/api/sources")
async def upload_source(project_id: str = Form(...), name: str = Form(""),
                        file: UploadFile | None = None, text: str = Form(""),
                        url: str = Form("")):
    """资料入库：file（txt/md/pdf/docx）| url（抓网页）| text（粘贴），三选一，优先级同序。
    入库即切块建索引（BM25，配了嵌入则顺带向量）；返回带 chunks 块数。"""
    from server import ingest, rag
    try:
        if not store.get_project(project_id):
            raise LookupError("空间不存在")
        if file is not None and (file.filename or ""):
            raw = await file.read()
            ftype, content = ingest.extract_file(file.filename, raw)
            name = (name or "").strip() or file.filename
            size = len(raw)
        elif (url or "").strip():
            ftype, title, content = ingest.extract_url(url)
            name = (name or "").strip() or title
            size = len(content.encode("utf-8"))
        else:
            content = ingest.normalize(text)
            ftype = "text"
            name = (name or "").strip() or "粘贴文本"
            size = len(content.encode("utf-8"))
        if not content.strip():
            raise ValueError("内容为空（没抽到文字）")
        src = store.add_source(project_id, name, content, type=ftype, size=size)
        try:
            src["chunks"] = rag.index_source(src["id"], project_id, name, content)
        except Exception as exc:
            print("rag index failed: %s" % exc)  # 索引失败不拦上传，但留痕
            src["chunks"] = 0
        src.pop("content", None)
        return src
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ingest.UnsupportedType as exc:
        raise HTTPException(415, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/sources")
def list_sources_ep(project_id: str):
    return {"sources": store.list_sources(project_id)}


@app.delete("/api/sources/{source_id}")
def delete_source_ep(source_id: str):
    try:
        src = store.delete_source(source_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    try:
        from server import rag
        rag.delete_source_chunks(source_id)
    except Exception as exc:
        print("rag delete chunks failed: %s" % exc)
    return src


@app.post("/api/branch")
def branch(body: BranchIn):
    if not (body.anchorText or "").strip():
        raise HTTPException(400, "锚文本为空")
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
        space = body.spaceId or body.projectId or store.DEFAULT_SPACE_ID
        if not store.get_project(space):
            raise HTTPException(404, "空间不存在")
        node_id = store.create_node(None, body.title or "未命名", project_id=space)["id"]
    store.add_message(node_id, "user", text)

    try:
        messages, cites = build_context(node_id, query=text, with_cites=True)
    except Exception:
        store.add_message(node_id, "assistant", "（生成失败：服务内部错误）")
        raise
    q: asyncio.Queue = asyncio.Queue()
    cancel = asyncio.Event()

    def save_cites(mid):
        if cites:
            try:
                store.add_citations(mid, cites)
            except Exception as exc:
                print("save citations failed: %s" % exc)

    async def run():
        full = ""
        if cites:
            await q.put(("cites", {"cites": cites}))
        try:
            async for chunk in llm.chat_stream(messages, cancel):
                full += chunk
                await q.put(("delta", chunk))
            if not full.strip():
                err = "（生成失败：模型返回了空内容）"
                mid = store.add_message(node_id, "assistant", err, status="error")
                await q.put(("delta", err))
                await q.put(("done", {"nodeId": node_id, "messageId": mid["id"], "error": True}))
                await q.put(None)
                return
            mid = store.add_message(node_id, "assistant", full)
            save_cites(mid["id"])
            await q.put(("done", {"nodeId": node_id, "messageId": mid["id"], "cites": len(cites)}))
        except asyncio.CancelledError:
            if full.strip():
                mid = store.add_message(node_id, "assistant", full + "（已停止）")
                save_cites(mid["id"])
                await q.put(("done", {"nodeId": node_id, "messageId": mid["id"], "cancelled": True}))
            else:
                await q.put(("done", {"nodeId": node_id, "cancelled": True}))
            raise
        except Exception as exc:
            err = "（生成失败：%s）" % exc
            mid = store.add_message(node_id, "assistant", err, status="error")
            await q.put(("delta", err))
            await q.put(("done", {"nodeId": node_id, "messageId": mid["id"], "error": True}))
        await q.put(None)

    task = asyncio.create_task(run())

    async def gen():
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                kind, payload = item
                yield sse(kind, {"text": payload} if kind == "delta" else payload)
        except asyncio.CancelledError:
            cancel.set()
            task.cancel()
            raise
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


DEMO_DIR = os.path.join(ROOT, "demo")
if os.path.isdir(DEMO_DIR):
    app.mount("/demo", StaticFiles(directory=DEMO_DIR), name="demo")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(DEMO_DIR, "ink.html"))
