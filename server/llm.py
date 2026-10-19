#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 调用：OpenAI 兼容端点 + 未配置时内置 mock。"""
from __future__ import print_function

import asyncio
import json
import os
import urllib.request

import httpx

TIMEOUT = float(os.environ.get("LLM_TIMEOUT") or 120)


def resolve():
    from server import settings as app_settings
    cfg = app_settings.load()
    base = (cfg.get("base_url") or os.environ.get("LLM_BASE_URL") or "").rstrip("/")
    key = cfg.get("api_key") or os.environ.get("LLM_API_KEY") or ""
    model = cfg.get("model") or os.environ.get("LLM_MODEL") or ""
    return base, key, model


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
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
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
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key}) as resp:
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
                delta = (((obj.get("choices") or [{}])[0].get("delta") or {}).get("content"))
                if delta:
                    yield delta


def describe():
    base, _, model = resolve()
    return {"base_url": base, "model": model, "configured": configured()}
