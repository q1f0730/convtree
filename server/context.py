#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分支上下文（ADR-0004）：path(root→当前) 上的消息，按字符预算组装。

接口：assemble(path) -> [{"role","content"}]
- 当前节点：消息全部原文；若有锚文本，先放一条【本分支锚文本】system 消息（原文）。
- 祖先：从近到远逐节点尝试原文放入；一旦某个祖先放不下，它和更远的祖先都换成摘要，
  合成一条【祖先摘要】system 消息放在最前（保持时间顺序，不留空洞）。
- 摘要按节点缓存（store.node_summary，以消息数为版本），外挂存储，不改 message（D-101）。
- 摘要器：默认确定性抽取（锚文本 + 每条消息开头），测试稳定、零成本；
  CONTEXT_SUMMARY=llm 时改调模型压缩，失败回落抽取。
- status='error' 的失败记录不进上下文（审计 P1-4）。
"""
from __future__ import print_function

from server import store

DEFAULT_BUDGET = 24000
SUMMARY_CHARS = 600
_PER_MSG = {"user": 80, "assistant": 120}


def budget():
    from server import settings as app_settings
    try:
        v = int(app_settings.env("CONTEXT_BUDGET_CHARS") or DEFAULT_BUDGET)
        return max(2000, v)
    except ValueError:
        return DEFAULT_BUDGET


def _msgs(node_id):
    return [{"role": m["role"], "content": m["content"]}
            for m in store.list_messages(node_id) if m.get("status") != "error"]


def extractive_summary(node, msgs, limit=SUMMARY_CHARS):
    """确定性摘要：锚文本 + 每条消息开头若干字，总长 ≤ limit。"""
    parts = []
    if node.get("anchor_text"):
        parts.append("锚：「%s」" % node["anchor_text"][:60])
    for m in msgs:
        n = _PER_MSG.get(m["role"], 80)
        t = " ".join((m["content"] or "").split())
        parts.append(("问：" if m["role"] == "user" else "答：") + t[:n] + ("…" if len(t) > n else ""))
    out = "；".join(parts)
    return out if len(out) <= limit else out[:limit - 1] + "…"


def _llm_summary(node, msgs, limit=SUMMARY_CHARS):
    from server import llm
    text = "\n".join(("用户：" if m["role"] == "user" else "助手：") + m["content"] for m in msgs)[:12000]
    prompt = ("把下面这段对话压缩成不超过%d字的中文要点摘要，保留关键概念、结论和用户关心的问题，不要寒暄：\n"
              % (limit // 2)) + text
    out, _ = llm.chat_sync([{"role": "user", "content": prompt}])
    out = (out or "").strip()
    if not out:
        raise ValueError("空摘要")
    if node.get("anchor_text"):
        out = "锚：「%s」；%s" % (node["anchor_text"][:60], out)
    return out[:limit]


def summary_for(node, msgs):
    cached = store.get_node_summary(node["id"], len(msgs))
    if cached is not None:
        return cached
    from server import settings as app_settings
    text = None
    if app_settings.env("CONTEXT_SUMMARY").lower() == "llm":
        try:
            text = _llm_summary(node, msgs)
        except Exception as exc:
            print("llm summary failed, fallback extractive: %s" % exc)
    if text is None:
        text = extractive_summary(node, msgs)
    store.put_node_summary(node["id"], len(msgs), text)
    return text


def assemble(path, limit=None):
    if not path:
        return []
    limit = limit or budget()
    cur = path[-1]
    cur_msgs = _msgs(cur["id"])
    tail = []
    if cur.get("parent_id") and cur.get("anchor_text"):
        tail.append({"role": "system", "content": "【本分支锚文本】用户从上一层回答里选中了这段话继续深挖：「%s」"
                     % cur["anchor_text"]})
    tail.extend(cur_msgs)
    used = sum(len(m["content"]) for m in tail)
    verbatim, summarized = [], []
    cut = False
    for depth in range(len(path) - 2, -1, -1):
        node = path[depth]
        msgs = _msgs(node["id"])
        size = sum(len(m["content"]) for m in msgs)
        if not cut and used + size <= limit:
            verbatim.insert(0, msgs)
            used += size
        else:
            cut = True
            summarized.insert(0, (depth, node, msgs))
    out = []
    if summarized:
        lines = ["【祖先摘要】较早的上层对话已压缩（原文未改动，仅为控制长度）："]
        for depth, node, msgs in summarized:
            if msgs or node.get("anchor_text"):
                lines.append("第%d层「%s」：%s" % (depth + 1, node.get("title") or "未命名", summary_for(node, msgs)))
        out.append({"role": "system", "content": "\n".join(lines)})
    for msgs in verbatim:
        out.extend(msgs)
    out.extend(tail)
    return out
