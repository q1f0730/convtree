#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型接入配置（深模块，ADR-0002）：唯一决定 chat / embed 端点的 base、key、model 从哪来。

接口不变量：
- 优先级：配置文件里**显式保存**的字段 > 环境变量（含 .env）> 服务商预设默认。
- key 绑定主机：保存时 base_url 的主机变了且本次没带新 key ⇒ 旧 key 清空（chat 与 embed 各自独立）。
- 环境变量里的 key 只发往环境变量里的 base（LLM_API_KEY ↔ LLM_BASE_URL，EMBED_API_KEY ↔ EMBED_BASE_URL）。
- 嵌入只在 embed_base_url 为空或与聊天同主机时回落聊天 key。
- 只返回掩码，永不返回明文 key。
"""
from __future__ import print_function

import io
import json
import os
import time
from urllib.parse import urlsplit

APP_DIR_NAME = "convtree"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_DIR_NAME)


CONFIG_PATH = os.environ.get("CONVTREE_CONFIG") or os.path.join(config_dir(), "config.json")

PROVIDERS = {
    "inception": {"name": "Inception Mercury", "base_url": "https://api.inceptionlabs.ai/v1",
                  "models": ["mercury-3", "mercury-2"], "default_model": "mercury-3", "need_key": True},
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
DEFAULT_PROVIDER = "inception"

FIELDS = ("provider", "base_url", "model", "api_key", "embed_base_url", "embed_model", "embed_key")
DEFAULTS = {k: "" for k in FIELDS}
DEFAULTS["provider"] = DEFAULT_PROVIDER

# ── .env：只补不覆盖真实环境变量；按 mtime 缓存 ──
_DOTENV_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "EMBED_BASE_URL", "EMBED_API_KEY", "EMBED_MODEL",
                "CONVTREE_ALLOWED_HOSTS", "CONVTREE_URL_ALLOWLIST", "CONTEXT_BUDGET_CHARS")
_dotenv_cache = {"path": None, "mtime": None, "vals": {}}


def _dotenv_path():
    return os.environ.get("CONVTREE_DOTENV") or os.path.join(ROOT, ".env")


def _dotenv():
    p = _dotenv_path()
    try:
        mt = os.path.getmtime(p)
    except OSError:
        _dotenv_cache.update(path=p, mtime=None, vals={})
        return {}
    if _dotenv_cache["path"] == p and _dotenv_cache["mtime"] == mt:
        return _dotenv_cache["vals"]
    vals = {}
    try:
        with io.open(p, "r", encoding="utf-8-sig") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                k = k.strip()
                if k.startswith("export "):
                    k = k[7:].strip()
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                if k in _DOTENV_KEYS:
                    vals[k] = v
    except (IOError, OSError):
        vals = {}
    _dotenv_cache.update(path=p, mtime=mt, vals=vals)
    return vals


def env(name):
    """真实环境变量优先，其次 .env。"""
    v = os.environ.get(name)
    if v is not None and v != "":
        return v
    return _dotenv().get(name, "")


def host_of(url):
    try:
        u = urlsplit((url or "").strip())
        return ((u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80))
    except ValueError:
        return ("", 0)


def _read_saved():
    try:
        with io.open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            return {k: str(saved[k]) for k in FIELDS if saved.get(k) is not None}
    except (IOError, OSError, ValueError):
        pass
    return {}


def _resolve(saved):
    """saved（显式字段）→ 生效配置。返回 dict（含 *_src 标注来源，便于测试与排查）。"""
    cfg = dict(DEFAULTS)
    env_base = env("LLM_BASE_URL").rstrip("/")
    prov_id = saved.get("provider") or ("custom" if (env_base and not saved.get("base_url")) else DEFAULT_PROVIDER)
    if prov_id not in PROVIDERS:
        prov_id = DEFAULT_PROVIDER
    p = PROVIDERS[prov_id]
    cfg["provider"] = prov_id
    # chat
    if saved.get("base_url"):
        cfg["base_url"], src = saved["base_url"], "file"
    elif env_base and not saved.get("provider"):
        cfg["base_url"], src = env_base, "env"
    else:
        cfg["base_url"], src = p["base_url"], "preset"
    cfg["base_src"] = src
    cfg["model"] = saved.get("model") or (env("LLM_MODEL") if src == "env" else "") or p["default_model"]
    if saved.get("api_key"):
        cfg["api_key"] = saved["api_key"]
    elif env("LLM_API_KEY") and env_base and host_of(env_base) == host_of(cfg["base_url"]):
        cfg["api_key"] = env("LLM_API_KEY")
    # embed
    env_ebase = env("EMBED_BASE_URL").rstrip("/")
    cfg["embed_base_url"] = saved.get("embed_base_url") or (env_ebase if "embed_base_url" not in saved else "")
    cfg["embed_model"] = saved.get("embed_model") or (env("EMBED_MODEL") if "embed_model" not in saved else "")
    if saved.get("embed_key"):
        cfg["embed_key"] = saved["embed_key"]
    elif env("EMBED_API_KEY") and env_ebase and host_of(env_ebase) == host_of(cfg["embed_base_url"]):
        cfg["embed_key"] = env("EMBED_API_KEY")
    return cfg


def load():
    return _resolve(_read_saved())


def save(patch):
    """patch 用内部字段名。执行"key 绑定主机"规则后写盘，只写显式字段。"""
    saved = _read_saved()
    before = _resolve(saved)
    patch = {k: ("" if patch[k] is None else str(patch[k]).strip()) for k in FIELDS if k in patch}
    for k, v in patch.items():
        saved[k] = v
    if "provider" in patch and "base_url" not in patch:
        saved.pop("base_url", None)   # 换预设 = 用新预设的地址
        if "model" not in patch:
            saved.pop("model", None)
    after = _resolve(saved)
    if host_of(after["base_url"]) != host_of(before["base_url"]) and not patch.get("api_key"):
        saved["api_key"] = ""
    eb_before = host_of(before["embed_base_url"] or before["base_url"])
    eb_after = host_of(after["embed_base_url"] or after["base_url"])
    if eb_before != eb_after and not patch.get("embed_key"):
        saved["embed_key"] = ""
    d = os.path.dirname(CONFIG_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    out = dict(saved)
    out["updated_at"] = int(time.time())
    tmp = CONFIG_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, CONFIG_PATH)
    return load()


def masked(key):
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:5] + "****" + key[-4:]


def chat_endpoint():
    """(base, key, model)。"""
    cfg = load()
    return cfg["base_url"].rstrip("/"), cfg.get("api_key", ""), cfg["model"]


def embed_endpoint():
    """(base, key, model) 或 None（未启用嵌入）。跨主机不回落聊天 key。"""
    cfg = load()
    model = (cfg.get("embed_model") or "").strip()
    if not model:
        return None
    chat_base = cfg["base_url"].rstrip("/")
    base = (cfg.get("embed_base_url") or "").strip().rstrip("/") or chat_base
    key = cfg.get("embed_key") or ""
    if not key and host_of(base) == host_of(chat_base):
        key = cfg.get("api_key") or ""
    if not base:
        return None
    return base, key, model


def is_configured():
    cfg = load()
    prov = PROVIDERS.get(cfg.get("provider")) or {}
    if not cfg.get("base_url"):
        return False
    if prov.get("need_key", True) and not cfg.get("api_key"):
        return False
    return True


def public_config():
    cfg = load()
    return {"provider": cfg.get("provider", ""), "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""), "has_key": bool(cfg.get("api_key")),
            "key_masked": masked(cfg.get("api_key", "")), "configured": is_configured(),
            "embed_base_url": cfg.get("embed_base_url", ""), "embed_model": cfg.get("embed_model", ""),
            "has_embed_key": bool(cfg.get("embed_key")),
            "embed_key_masked": masked(cfg.get("embed_key", ""))}
