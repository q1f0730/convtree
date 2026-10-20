#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收 v2：只走公共接口（HTTP / SSE），外部依赖用本地 HTTP 桩，不 monkeypatch 模块全局。

与 accept_full.py 的分工：accept_full 是历史回归（含部分源码 grep）；本文件按
.agents/skills/engineering/tdd 的规范写——每例一个行为，断言可观察结果。
运行（项目根）：python server/accept_v2.py     退出码 0=全过，1=有失败
"""
from __future__ import print_function

import http.server
import json
import os
import re
import socketserver
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 本地回环直连（沙箱可能有出站代理）
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
TMP = tempfile.mkdtemp(prefix="ct_v2_")
os.environ["CONVTREE_NO_AUTOBACKUP"] = "1"
os.environ["APP_DB"] = os.path.join(TMP, "app.db")
os.environ["CONVTREE_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["CONVTREE_DOTENV"] = os.path.join(TMP, "no.env")
for k in list(os.environ):
    if k.startswith(("LLM_", "EMBED_", "CONVTREE_ALLOWED_HOSTS", "CONVTREE_ALLOW_PRIVATE")):
        os.environ.pop(k, None)

from fastapi.testclient import TestClient  # noqa: E402

from server import store  # noqa: E402
from server.main import app  # noqa: E402

store.init_db()
client = TestClient(app)
FAILS, N = [], [0]


def check(cond, name):
    N[0] += 1
    print(("OK   " if cond else "FAIL ") + "%03d %s" % (N[0], name))
    if not cond:
        FAILS.append(name)


def section(t):
    print("\n── %s ──" % t)


def serve(handler_cls):
    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True
    srv = S(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def sse_events(text):
    return [(m.group(1), json.loads(m.group(2)))
            for m in re.finditer(r"event: (\w+)\ndata: (.*)\n", text)]


def reset_settings():
    p = os.environ["CONVTREE_CONFIG"]
    if os.path.exists(p):
        os.remove(p)


# ════════════════════════════════════════════════════════════════════
section("冒烟")
r = client.get("/api/health")
check(r.status_code == 200 and r.json().get("ok") is True, "health 200")


# ════════════════════════════════════════════════════════════════════
print("\n共 %d 例，失败 %d" % (N[0], len(FAILS)))
for f in FAILS:
    print("  FAIL", f)
sys.exit(1 if FAILS else 0)
