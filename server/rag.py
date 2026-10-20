#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 引擎：切块（带偏移）/ 分词 / 检索。后端可插拔，零重依赖。

- 默认后端：纯 Python BM25。分词 = 中文字二元组（单字片段保留单字）+ 英文/数字词（小写）。
  块与词表存 SQLite（store.chunk），查询时按笔记本现算 df/idf —— 本地规模足够。
- 可选后端：OpenAI 兼容 /embeddings（settings: embed_base_url / embed_model / embed_key，
  缺 base/key 时回落聊天端点的 base_url / api_key）。配了 embed_model 即启用，
  与 BM25 做 RRF 混合；嵌入任何一步失败都回落纯 BM25，不拦聊天。
- 不依赖 chromadb / sentence-transformers / torch。
"""
from __future__ import print_function

import json
import math
import os
import re

from server import store

CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE") or 400)
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP") or 60)
TOP_K = int(os.environ.get("RAG_TOP_K") or 5)
EMBED_TIMEOUT = float(os.environ.get("RAG_EMBED_TIMEOUT") or 30)
EMBED_BATCH = 16
BM25_K1, BM25_B = 1.5, 0.75
RRF_K = 60

_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af"
_TOKEN_RE = re.compile(r"[%s]+|[A-Za-z0-9]+(?:['_][A-Za-z0-9]+)*" % _CJK)
_CJK_RE = re.compile(r"[%s]" % _CJK)
_EN_STOP = {"the", "a", "an", "of", "to", "in", "and", "or", "is", "are", "was", "were", "be",
            "for", "on", "with", "as", "at", "by", "it", "this", "that", "from"}
# 句读/换行处断块，偏移仍是原文字符偏移
_BREAK_CHARS = "\n。！？；!?;．.，, "


def tokenize(text):
    """中文：连续 CJK 片段切字二元组（长度 1 保留单字）；英文/数字：小写词，去常见虚词。"""
    out = []
    for m in _TOKEN_RE.finditer(text or ""):
        w = m.group(0)
        if _CJK_RE.match(w):
            if len(w) == 1:
                out.append(w)
            else:
                out.extend(w[i:i + 2] for i in range(len(w) - 1))
        else:
            w = w.lower()
            if w not in _EN_STOP:
                out.append(w)
    return out


def chunk_spans(text, size=None, overlap=None):
    """返回 [(start, end)]，text[start:end] 即块原文。尽量在句读处断开。
    校验 0 <= overlap < size（否则会死循环）。"""
    size = size or CHUNK_SIZE
    overlap = overlap if overlap is not None else CHUNK_OVERLAP
    if size <= 0:
        raise ValueError("size 必须为正")
    if not (0 <= overlap < size):
        raise ValueError("overlap 必须满足 0 <= overlap < size")
    n = len(text or "")
    if n == 0:
        return []
    spans, i = [], 0
    while i < n:
        end = min(i + size, n)
        if end < n:
            # 在窗口后 30% 内找最后一个断点
            lo = i + int(size * 0.7)
            cut = -1
            for j in range(end - 1, max(lo, i + 1) - 1, -1):
                if text[j] in _BREAK_CHARS:
                    cut = j + 1
                    break
            if cut > i:
                end = cut
        # 去掉首尾纯空白（偏移随之收紧，保证 text[start:end] == 块文）
        s, e = i, end
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            spans.append((s, e))
        if end >= n:
            break
        nxt = end - overlap
        i = nxt if nxt > i else end
    return spans


def chunk_text(text, size=None, overlap=None):
    """兼容旧接口：只返回块文本。"""
    return [text[s:e] for s, e in chunk_spans(text, size, overlap)]


# ── 嵌入后端（可选）──

def embed_config():
    """返回 (base, key, model) 或 None（未启用）。规则在 settings.embed_endpoint（ADR-0002）。"""
    from server import settings as app_settings
    return app_settings.embed_endpoint()


def _embed(texts):
    """调 OpenAI 兼容 /embeddings。可被测试 monkeypatch。失败抛异常。"""
    import httpx
    conf = embed_config()
    if not conf:
        raise RuntimeError("嵌入未配置")
    base, key, model = conf
    out = []
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    with httpx.Client(timeout=EMBED_TIMEOUT) as cli:
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i:i + EMBED_BATCH]
            r = cli.post(base + "/embeddings", headers=headers,
                         content=json.dumps({"model": model, "input": batch}).encode("utf-8"))
            r.raise_for_status()
            data = sorted(r.json().get("data") or [], key=lambda d: d.get("index", 0))
            if len(data) != len(batch):
                raise RuntimeError("嵌入返回条数不符")
            out.extend(d["embedding"] for d in data)
    return out


def _cos(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def backend():
    return "hybrid" if embed_config() else "bm25"


# ── 入库 / 检索 ──

def index_source(source_id, project_id, name, text):
    """切块（带偏移）+ 分词 + （可选）嵌入，写 chunk 表。返回块数。"""
    spans = chunk_spans(text)
    rows = []
    for i, (s, e) in enumerate(spans):
        rows.append({"idx": i, "start": s, "end": e, "text": text[s:e],
                     "tokens": tokenize(text[s:e]), "embedding": None})
    if rows and embed_config():
        try:
            vecs = _embed([r["text"] for r in rows])
            for r, v in zip(rows, vecs):
                r["embedding"] = v
        except Exception as exc:
            print("rag embed failed (fallback bm25): %s" % exc)
    return store.replace_chunks(source_id, project_id, rows)


def _bm25(chunks, q_tokens):
    if not chunks or not q_tokens:
        return {}
    N = len(chunks)
    avgdl = (sum(c["tok_len"] for c in chunks) / float(N)) or 1.0
    toks = [c["tokens"].split() for c in chunks]
    qset = set(q_tokens)
    df = dict((t, 0) for t in qset)
    for ts in toks:
        for t in qset.intersection(ts):
            df[t] += 1
    scores = {}
    for c, ts in zip(chunks, toks):
        tf = {}
        for t in ts:
            if t in qset:
                tf[t] = tf.get(t, 0) + 1
        if not tf:
            continue
        dl = len(ts) or 1
        sc = 0.0
        for t, f in tf.items():
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            sc += idf * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl))
        scores[c["id"]] = sc
    return scores


def _vector_scores(chunks, query):
    """嵌入相似度；缺向量的块就地补嵌（失败即放弃，返回 None 让上层回落）。"""
    try:
        missing = [c for c in chunks if not c.get("embedding")]
        if missing:
            vecs = _embed([c["text"] for c in missing])
            store.set_chunk_embeddings([(c["id"], v) for c, v in zip(missing, vecs)])
            for c, v in zip(missing, vecs):
                c["embedding"] = json.dumps(v)
        qv = _embed([query])[0]
        return dict((c["id"], _cos(qv, json.loads(c["embedding"]))) for c in chunks)
    except Exception as exc:
        print("rag vector failed (fallback bm25): %s" % exc)
        return None


def retrieve(project_id, query, top_k=None):
    """按空间检索 top-k 块：[{chunk_id, source_id, source_name, text, start, end, score}]。已软删空间检索为空。"""
    top_k = top_k or TOP_K
    if not store.get_project(project_id):
        return []
    chunks = store.list_chunks(project_id)
    if not chunks or not (query or "").strip():
        return []
    bm = _bm25(chunks, tokenize(query))
    vec = _vector_scores(chunks, query) if embed_config() else None
    if vec is None:
        ranked = sorted(bm.items(), key=lambda kv: -kv[1])
    else:
        fused = {}
        for scores in (bm, vec):
            order = sorted(((k, v) for k, v in scores.items() if v > 0), key=lambda kv: -kv[1])
            for rank, (cid, _) in enumerate(order):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        ranked = sorted(fused.items(), key=lambda kv: -kv[1])
    byid = dict((c["id"], c) for c in chunks)
    out = []
    for cid, sc in ranked[:top_k]:
        if sc <= 0:
            continue
        c = byid[cid]
        out.append({"chunk_id": cid, "source_id": c["source_id"], "source_name": c["source_name"],
                    "text": c["text"], "start": c["start"], "end": c["end"], "score": round(sc, 6)})
    return out


def delete_source_chunks(source_id):
    return store.delete_chunks(source_id)


def reset():
    """兼容旧接口（chroma 版有缓存，BM25 版无状态）。"""
    return None
