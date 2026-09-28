#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 调用：OpenAI 兼容端点 + 未配置时内置 mock。"""
from __future__ import print_function

import json
import os
import urllib.request

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
    return "（未配置模型，这是本地 mock 回复）你问的是：「%s」。去设置里填好服务商与密钥，即可接真模型。" % last[:120]


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


def describe():
    base, _, model = resolve()
    return {"base_url": base, "model": model, "configured": configured()}
