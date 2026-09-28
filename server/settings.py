#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型接入配置：存项目目录之外。只返掩码，不返明文密钥。"""
from __future__ import print_function

import io
import json
import os
import time

APP_DIR_NAME = "convtree"


def config_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_DIR_NAME)


CONFIG_PATH = os.environ.get("CONVTREE_CONFIG") or os.path.join(config_dir(), "config.json")

PROVIDERS = {
    "dashscope": {"name": "阿里云百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "models": ["qwen-plus", "qwen-max", "qwen-flash", "qwen-turbo"],
                  "default_model": "qwen-plus", "need_key": True},
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "models": ["deepseek-chat", "deepseek-reasoner"],
                 "default_model": "deepseek-chat", "need_key": True},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1",
               "models": ["gpt-4o-mini", "gpt-4o"], "default_model": "gpt-4o-mini", "need_key": True},
    "ollama": {"name": "本地 Ollama", "base_url": "http://127.0.0.1:11434/v1",
               "models": ["qwen2.5:7b", "llama3.1:8b"], "default_model": "qwen2.5:7b", "need_key": False},
    "custom": {"name": "自定义", "base_url": "", "models": [], "default_model": "", "need_key": False},
}

DEFAULTS = {"provider": "dashscope", "base_url": "", "model": "", "api_key": ""}


def load():
    cfg = dict(DEFAULTS)
    try:
        with io.open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for k in DEFAULTS:
                if saved.get(k) is not None:
                    cfg[k] = saved[k]
    except (IOError, OSError, ValueError):
        pass
    p = PROVIDERS.get(cfg.get("provider")) or PROVIDERS["dashscope"]
    if not cfg.get("base_url"):
        cfg["base_url"] = p["base_url"]
    if not cfg.get("model"):
        cfg["model"] = p["default_model"]
    return cfg


def save(patch):
    cfg = load()
    for k in DEFAULTS:
        if k in patch and patch[k] is not None:
            cfg[k] = str(patch[k]).strip()
    d = os.path.dirname(CONFIG_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    cfg["updated_at"] = int(time.time())
    with io.open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=2))
    return cfg


def masked(key):
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:5] + "****" + key[-4:]


def is_configured():
    if os.path.exists(CONFIG_PATH):
        cfg = load()
        prov = PROVIDERS.get(cfg.get("provider")) or {}
        if prov.get("need_key", True) and not cfg.get("api_key"):
            return False
        return bool(cfg.get("base_url"))
    return bool(os.environ.get("LLM_BASE_URL"))


def public_config():
    cfg = load()
    return {"provider": cfg.get("provider", ""), "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""), "has_key": bool(cfg.get("api_key")),
            "key_masked": masked(cfg.get("api_key", "")), "configured": is_configured()}
