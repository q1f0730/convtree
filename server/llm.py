#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 调用：OpenAI 兼容端点 + 未配置时内置 mock。"""
from __future__ import print_function

import asyncio
import json
import os
import urllib.error
import urllib.request

import httpx

TIMEOUT = float(os.environ.get("LLM_TIMEOUT") or 120)


def resolve():
    from server import settings as app_settings
    return app_settings.chat_endpoint()


class UpstreamError(RuntimeError):
    """上游返回 HTTP 错误（401/402/429/400…）。message 已含状态码与上游给的原因。"""


def _upstream_reason(status, raw):
    try:
        obj = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
        err = obj.get("error") if isinstance(obj, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else err) or obj.get("message") or obj.get("detail")
    except Exception:
        msg = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or ""))[:200]
    return "上游 HTTP %d：%s" % (status, (str(msg) if msg else "无详情")[:300])


def _headers(key):
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    return h


def configured():
    from server import settings as app_settings
    return app_settings.is_configured()


def mock_reply(messages):
    last = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last = m.get("content", "")
            break
    text = "（未配置模型，这是本地 mock 回复）你问的是：「%s」。去设置里填好服务商与密钥，即可接真模型。" % last[:120]
    if any(m.get("role") == "system" and "【参考资料】" in (m.get("content") or "") for m in messages):
        text += "（mock 引用示例：见资料 [1]）"
    return text


def chat_sync(messages):
    """同步拿完整回复。未配置走 mock；失败抛异常由上层收口。"""
    from server import settings as app_settings
    if not app_settings.is_configured():
        return mock_reply(messages), {"mock": True}
    base, key, model = resolve()
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=body, headers=_headers(key))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise UpstreamError(_upstream_reason(exc.code, exc.read() or b""))
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    usage = (data.get("usage") or {})
    return text, {"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)}


async def chat_stream(messages, cancel=None):
    """异步生成器：逐块 yield 上游 token 流（真流式）。未配置走 mock（分块）。
    cancel 为 asyncio.Event，置位即停止。失败抛异常由上层收口。"""
    from server import settings as app_settings
    if not app_settings.is_configured():
        text = mock_reply(messages)
        for i in range(0, len(text), 4):
            if cancel is not None and cancel.is_set():
                return
            yield text[i:i + 4]
            await asyncio.sleep(0)
        return
    base, key, model = resolve()
    body = json.dumps({"model": model, "messages": messages, "stream": True}).encode("utf-8")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream(
                "POST", base + "/chat/completions", content=body,
                headers=_headers(key)) as resp:
            if resp.status_code >= 400:
                raw = await resp.aread()
                raise UpstreamError(_upstream_reason(resp.status_code, raw))
            async for line in resp.aiter_lines():
                if cancel is not None and cancel.is_set():
                    return
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if isinstance(obj, dict) and obj.get("error"):
                    raise UpstreamError(_upstream_reason(resp.status_code, json.dumps(obj)))
                delta = (((obj.get("choices") or [{}])[0].get("delta") or {}).get("content"))
                if delta:
                    yield delta


def describe():
    base, _, model = resolve()
    return {"base_url": base, "model": model, "configured": configured()}
