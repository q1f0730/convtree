#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V0.1 验收：设置掩码 / mock 聊天落库 / 分支隔离 D-107 / D-101 无 UPDATE。
运行（项目根）：python server/accept_v01.py（自隔离：mock 零花费，临时库）"""
from __future__ import print_function

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["CONVTREE_NO_AUTOBACKUP"] = "1"
os.environ["CONVTREE_CONFIG"] = os.path.join(ROOT, "data", "accept_v01.noconfig.json")
if os.path.exists(os.environ["CONVTREE_CONFIG"]):
    os.remove(os.environ["CONVTREE_CONFIG"])
os.environ.pop("LLM_BASE_URL", None)
os.environ.pop("LLM_API_KEY", None)
DB = os.path.join(ROOT, "data", "accept_v01.db")
os.environ["APP_DB"] = DB
for s in ("", "-wal", "-shm"):
    if os.path.exists(DB + s):
        os.remove(DB + s)

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app, build_context  # noqa: E402
from server import store as _store  # noqa: E402

_store.init_db()

fails = []


def check(name, cond, detail=""):
    print(("OK   " if cond else "FAIL ") + name + (("  :: " + detail) if detail else ""))
    if not cond:
        fails.append(name)


c = TestClient(app)

# 1. 设置：存掩码，不返明文
r = c.post("/api/settings", json={"provider": "dashscope", "model": "qwen-plus", "apiKey": "sk-test1234567890"})
check("设置保存", r.status_code == 200, str(r.status_code))
cfg = r.json()["config"]
check("返掩码不返明文", cfg.get("key_masked", "").startswith("sk-te") and "sk-test1234567890" not in json.dumps(cfg))
r = c.post("/api/settings", json={"provider": "dashscope"})
check("空apiKey=不改动", c.get("/api/settings").json()["config"]["has_key"] is True)

# 2. mock 聊天：建根 + 落库（未配置走 mock，零花费）
r = c.post("/api/chat", json={"text": "怎么选咖啡豆？"})
check("chat 200", r.status_code == 200, str(r.status_code))
body = r.text
check("SSE 有 done", "event: done" in body)
m = re.search(r'event: done\ndata: (\{.*\})', body)
node_id = json.loads(m.group(1))["nodeId"] if m else None
check("拿到 nodeId", bool(node_id))
msgs = c.get("/api/nodes/%s/messages" % node_id).json()["messages"]
check("user+assistant 落库", [x["role"] for x in msgs] == ["user", "assistant"], str([x["role"] for x in msgs]))

# 3. 分支：继承父上下文（D-107），兄弟 excluded
r = c.post("/api/branch", json={"parentId": node_id, "anchorText": "A咖啡豆", "color": "#f25555"})
check("分支创建", r.status_code == 200, str(r.status_code))
child = r.json()["node"]["id"]
c.post("/api/chat", json={"nodeId": child, "text": "A适合手冲吗？"})
ctx = build_context(child)
check("子上下文含父user", any("咖啡豆" in x["content"] for x in ctx if x["role"] == "user"))
# 兄弟节点内容不得进上下文
r2 = c.post("/api/branch", json={"parentId": node_id, "anchorText": "B咖啡豆", "color": "#3fa7ff"})
sib = r2.json()["node"]["id"]
c.post("/api/chat", json={"nodeId": sib, "text": "B适合意式吗？"})
ctx2 = build_context(sib)
check("兄弟内容隔离", not any("手冲" in x["content"] for x in ctx2), str([x["content"][:20] for x in ctx2]))

# 4. D-101：server 全仓无 UPDATE message
bad = []
for fn in os.listdir(os.path.join(ROOT, "server")):
    if fn.endswith(".py") and fn != "accept_v01.py":
        src = open(os.path.join(ROOT, "server", fn), encoding="utf-8").read()
        if re.search(r"UPDATE\s+message\b", src, re.I):
            bad.append(fn)
check("D-101 无UPDATE", not bad, str(bad))

# 5. 改名空标题被拒
r = c.post("/api/nodes/%s/rename" % child, json={"title": "  "})
check("空标题400", r.status_code == 400, str(r.status_code))

print("")
print("=== 全部通过 ===" if not fails else "=== 失败 %d 项：%s ===" % (len(fails), fails))
sys.exit(0 if not fails else 1)
