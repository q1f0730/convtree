#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 引擎：切块 / 向量化 / 检索。懒加载 sentence-transformers 与 chroma。

向量化用本地 sentence-transformers（中文模型，EMBED_MODEL 可配）；
向量存 chroma PersistentClient（CHROMA_DIR，默认 data/chroma）。
"""
from __future__ import print_function

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 可配常量
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL") or "BAAI/bge-small-zh-v1.5"
CHUNK_SIZE = int(os.environ.get("RAG_CHUNK_SIZE") or 300)
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP") or 50)
TOP_K = int(os.environ.get("RAG_TOP_K") or 5)
CHROMA_DIR = os.environ.get("RAG_CHROMA_DIR") or os.path.join(ROOT, "data", "chroma")

_model = None
_chroma = None
_collection = None


def chunk_text(text, size=None, overlap=None):
    """按字符切块，返回块列表。size/overlap 缺省用模块常量。
    校验 0 <= overlap < size，非法抛 ValueError（否则 overlap>=size 会死循环）。"""
    size = size or CHUNK_SIZE
    overlap = overlap if overlap is not None else CHUNK_OVERLAP
    if not text:
        return []
    if size <= 0:
        raise ValueError("size 必须为正")
    if not (0 <= overlap < size):
        raise ValueError("overlap 必须满足 0 <= overlap < size")
    if len(text) <= size:
        return [text]
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return out


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def _embed(texts):
    """把文本列表变成向量列表。可被测试 monkeypatch。"""
    return _get_model().encode(texts, normalize_embeddings=True).tolist()


def _get_collection():
    global _chroma, _collection
    if _collection is None:
        import chromadb
        _chroma = chromadb.PersistentClient(path=CHROMA_DIR)
        _collection = _chroma.get_or_create_collection(
            "sources", metadata={"hnsw:space": "cosine"})
    return _collection


def reset():
    """测试用：清掉缓存的 model/collection，下次调用重新加载。"""
    global _model, _chroma, _collection
    _model = _chroma = _collection = None


def index_source(source_id, project_id, name, text):
    """切块 + 向量化 + 存 chroma。返回块数。"""
    chunks = chunk_text(text)
    if not chunks:
        return 0
    coll = _get_collection()
    coll.delete(where={"source_id": source_id})  # 重索引时先清旧块，避免孤儿
    ids = ["%s_%d" % (source_id, i) for i in range(len(chunks))]
    coll.upsert(ids=ids, documents=chunks,
                metadatas=[{"source_id": source_id, "project_id": project_id,
                            "name": name, "chunk_index": i} for i in range(len(chunks))],
                embeddings=_embed(chunks))
    return len(chunks)


def retrieve(project_id, query, top_k=None):
    """按 project_id 检索 top-k 相关块，返回 [{text, source_name, score}]。"""
    top_k = top_k or TOP_K
    coll = _get_collection()
    if coll.count() == 0:
        return []
    res = coll.query(query_embeddings=_embed([query]), n_results=top_k,
                     where={"project_id": project_id})
    out = []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for d, m, dist in zip(docs, metas, dists):
        out.append({"text": d, "source_name": m.get("name", ""), "score": 1.0 - dist})
    return out


def delete_source_chunks(source_id):
    """删某资料的所有块。"""
    coll = _get_collection()
    coll.delete(where={"source_id": source_id})
