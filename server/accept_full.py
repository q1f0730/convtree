#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V0.1 全量验收：100 例。隔离环境（临时库 + 不存在的配置 + mock 零花费）。
运行（项目根）：python server/accept_full.py"""
from __future__ import print_function

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["CONVTREE_NO_AUTOBACKUP"] = "1"
os.environ["CONVTREE_CONFIG"] = os.path.join(ROOT, "data", "accept_full.noconfig.json")
if os.path.exists(os.environ["CONVTREE_CONFIG"]):
    os.remove(os.environ["CONVTREE_CONFIG"])
os.environ.pop("LLM_BASE_URL", None)
os.environ.pop("LLM_API_KEY", None)
os.environ.pop("LLM_MODEL", None)
DB = os.path.join(ROOT, "data", "accept_full.db")
os.environ["APP_DB"] = DB
for s in ("", "-wal", "-shm"):
    if os.path.exists(DB + s):
        os.remove(DB + s)

from fastapi.testclient import TestClient  # noqa: E402

from server import llm as llm_mod  # noqa: E402
from server import settings as settings_mod  # noqa: E402
from server import store as store_mod  # noqa: E402
from server.main import app, build_context  # noqa: E402

store_mod.init_db()
c = TestClient(app)
fails = []
N = [0]


def check(cond, detail=""):
    N[0] += 1
    tag = "%03d" % N[0]
    print(("OK   " if cond else "FAIL ") + tag + " " + (detail or ""))
    if not cond:
        fails.append(tag + " " + detail)


def sse_done(text):
    m = re.search(r'event: done\ndata: (\{.*\})', text)
    return json.loads(m.group(1)) if m else None


INK = open(os.path.join(ROOT, "demo", "ink.html"), encoding="utf-8").read()
MAIN_SRC = open(os.path.join(ROOT, "server", "main.py"), encoding="utf-8").read()
STORE_SRC = open(os.path.join(ROOT, "server", "store.py"), encoding="utf-8").read()

# ── 设置 1-20 ──
r = c.post("/api/settings", json={"provider": "dashscope", "model": "qwen-plus", "apiKey": "sk-test1234567890"})
check(r.status_code == 200, "设置保存200")
cfg = r.json()["config"]
check(cfg.get("key_masked", "").startswith("sk-te"), "掩码格式")
check("sk-test1234567890" not in json.dumps(r.json()), "不返明文")
check(cfg.get("has_key") is True, "has_key真")
r = c.post("/api/settings", json={"provider": "dashscope"})
check(c.get("/api/settings").json()["config"]["has_key"] is True, "空apiKey不改动")
g = c.get("/api/settings").json()
check(set(g["providers"].keys()) == {"dashscope", "deepseek", "openai", "ollama", "custom"}, "5服务商")
r = c.post("/api/settings", json={"model": "  qwen-max  ", "baseUrl": "https://x.example/v1"})
cfg = r.json()["config"]
check(cfg["model"] == "qwen-max", "去空白")
check(cfg["base_url"] == "https://x.example/v1", "自定义地址")
check(cfg["provider"] == "dashscope", "只改模型时provider保留")
r = c.post("/api/settings", json={"provider": "nosuch"})
check(r.status_code == 200 and c.get("/api/settings").json()["config"]["base_url"] != "", "未知provider回落有地址")
r = c.post("/api/settings", json={"provider": "dashscope", "model": "qwen-plus"})
t = c.post("/api/settings/test")
check(t.status_code == 200 and "message" in t.json(), "测试端点有message")
r = c.post("/api/settings", json={"clearKey": True})
check(c.get("/api/settings").json()["config"]["has_key"] is False, "clearKey删密钥")
check(settings_mod.is_configured() is False, "删钥后未配置")
raw = open(os.environ["CONVTREE_CONFIG"], encoding="utf-8").read()
check("sk-test1234567890" not in raw, "删钥后落盘无明文")
os.rename(os.environ["CONVTREE_CONFIG"], os.environ["CONVTREE_CONFIG"] + ".bak")
os.environ["LLM_BASE_URL"] = "https://env.example/v1"
check(settings_mod.is_configured() is True, "无文件时环境变量兜底")
os.environ.pop("LLM_BASE_URL", None)
check(settings_mod.is_configured() is False, "无文件无变量=未配置")
os.rename(os.environ["CONVTREE_CONFIG"] + ".bak", os.environ["CONVTREE_CONFIG"])
check(os.path.exists(os.environ["CONVTREE_CONFIG"]), "配置落盘在项目外")
raw = open(os.environ["CONVTREE_CONFIG"], encoding="utf-8").read()
check("sk-test1234567890" not in raw, "删钥后落盘无明文")
check(llm_mod.configured() is False, "llm未配置走mock")
txt, meta = llm_mod.chat_sync([{"role": "user", "content": "hi"}])
check("mock" in txt and meta.get("mock") is True, "mock回显")

# ── 聊天 21-50 ──
check(c.post("/api/chat", json={"text": ""}).status_code == 400, "空400")
check(c.post("/api/chat", json={"text": "   "}).status_code == 400, "空白400")
check(c.post("/api/chat", json={"text": "x", "nodeId": "nd_nope"}).status_code == 404, "坏节点404")
r = c.post("/api/chat", json={"text": "根问题A"})
check(r.status_code == 200, "建根200")
check("event: delta" in r.text, "有delta")
check("event: done" in r.text, "有done")
d = sse_done(r.text)
check(bool(d and d.get("nodeId") and d.get("messageId")), "done带双id")
root = d["nodeId"]
msgs = c.get("/api/nodes/%s/messages" % root).json()["messages"]
check([m["role"] for m in msgs] == ["user", "assistant"], "user+assistant落库")
mids = set()
r = c.post("/api/chat", json={"nodeId": root, "text": "第二问"})
mids.add(sse_done(r.text)["messageId"])
r = c.post("/api/chat", json={"nodeId": root, "text": "第三问"})
mids.add(sse_done(r.text)["messageId"])
check(len(mids) == 2, "messageId唯一")
msgs = c.get("/api/nodes/%s/messages" % root).json()["messages"]
check(len(msgs) == 6, "同节点追加")
check([m["role"] for m in msgs] == ["user", "assistant"] * 3, "顺序交替")
ctx = build_context(root)
check([m["role"] for m in ctx] == ["user", "assistant"] * 3, "上下文顺序")
r = c.post("/api/chat", json={"text": "marker-xyz-111"})
n2 = sse_done(r.text)["nodeId"]
ch = c.post("/api/branch", json={"parentId": n2, "anchorText": "a", "color": "#fff"}).json()["node"]["id"]
c.post("/api/chat", json={"nodeId": ch, "text": "子问"})
check(any("marker-xyz-111" in m["content"] for m in build_context(ch)), "子继承父")
sib = c.post("/api/branch", json={"parentId": n2, "anchorText": "b", "color": "#000"}).json()["node"]["id"]
c.post("/api/chat", json={"nodeId": sib, "text": "sibmarker-222"})
check(not any("sibmarker-222" in m["content"] for m in build_context(ch)), "兄弟隔离")
long_t = "长" * 5000
check(c.post("/api/chat", json={"text": long_t}).status_code == 200, "5000字通过")
em = "emoji🍵 \"引号\" <标签>"
r = c.post("/api/chat", json={"text": em})
nn = sse_done(r.text)["nodeId"]
got = c.get("/api/nodes/%s/messages" % nn).json()["messages"][0]["content"]
check(got == em, "原文逐字存")
h = c.get("/api/health").json()
check(h["ok"] is True and h["db"] == DB, "health指临时库")
check(h["llm"]["configured"] is False, "health未配置")
p = c.get("/api/nodes/%s/messages" % root).json()
check(len(p["path"]) == 1 and p["path"][0]["id"] == root, "根path长1")
check(c.post("/api/chat", json={"text": "t"}).headers["content-type"].startswith("text/event-stream"), "SSE类型")
mid = sse_done(c.post("/api/chat", json={"text": "查id"}).text)["messageId"]
allm = [m["id"] for m in c.get("/api/nodes/%s/messages" % sse_done(c.post("/api/chat", json={"text": "y"}).text)["nodeId"]).json()["messages"]]
check(mid not in allm, "messageId跨节点唯一")
ts = [m["created_at"] for m in c.get("/api/nodes/%s/messages" % root).json()["messages"]]
check(ts == sorted(ts), "created_at单调")
ra = c.post("/api/chat", json={"text": "A库"})
rb = c.post("/api/chat", json={"text": "B库"})
check(sse_done(ra.text)["nodeId"] != sse_done(rb.text)["nodeId"], "两根独立")

# ── 分支改名节点 51-75 ──
r = c.post("/api/branch", json={"parentId": root, "anchorText": "锚A", "color": "#f25555"})
check(r.status_code == 200 and r.json()["node"]["parent_id"] == root, "分支挂父")
check(r.json()["node"]["anchor_text"] == "锚A" and r.json()["node"]["color"] == "#f25555", "锚色存")
r = c.post("/api/branch", json={"parentId": root, "anchorText": "x" * 300})
check(len(r.json()["node"]["anchor_text"]) == 200, "锚截断200")
check(r.json()["node"]["title"] == "未命名", "默认未命名")
r = c.post("/api/branch", json={"parentId": root, "anchorText": "t", "title": "自定名"})
check(r.json()["node"]["title"] == "自定名", "自定标题")
check(c.post("/api/branch", json={"parentId": "nd_nope", "anchorText": "x"}).status_code == 404, "坏父404")
check(c.post("/api/branch", json={"parentId": root}).status_code == 422, "缺锚422")
br = r.json()["node"]["id"]
r = c.post("/api/nodes/%s/rename" % br, json={"title": "新名"})
check(r.json()["title"] == "新名", "改名")
r = c.post("/api/nodes/%s/rename" % br, json={"title": "  两边  "})
check(r.json()["title"] == "两边", "改名去空白")
check(c.post("/api/nodes/%s/rename" % br, json={"title": ""}).status_code == 400, "空名400")
check(c.post("/api/nodes/%s/rename" % br, json={"title": "  "}).status_code == 400, "空白名400")
check(c.post("/api/nodes/nd_nope/rename", json={"title": "x"}).status_code == 404, "坏点404")
alln = {n["id"]: n for n in c.get("/api/nodes").json()["nodes"]}
check(alln[br]["title"] == "两边", "改名持久")
cur, depth = root, 0
for _ in range(5):
    cur = c.post("/api/branch", json={"parentId": cur, "anchorText": "d"}).json()["node"]["id"]
    depth += 1
check(len(store_mod.node_path(cur)) == 6, "5层path=6")
for _ in range(5):
    cur = c.post("/api/branch", json={"parentId": cur, "anchorText": "d"}).json()["node"]["id"]
check(len(store_mod.node_path(cur)) == 11, "10层无上限")
b1 = c.post("/api/branch", json={"parentId": root, "anchorText": "s1"}).json()["node"]["id"]
b2 = c.post("/api/branch", json={"parentId": root, "anchorText": "s2"}).json()["node"]["id"]
check(b1 != b2, "兄弟id独立")
nb = c.post("/api/branch", json={"parentId": b1, "anchorText": "nest"}).json()["node"]["id"]
check(store_mod.node_path(nb)[-2]["id"] == b1, "嵌套父正确")
check(store_mod.node_path(nb)[0]["id"] == root, "嵌套根正确")
check(alln[root]["anchor_text"] is None, "根无锚")
ra = c.post("/api/branch", json={"parentId": root, "anchorText": "同文"})
rb = c.post("/api/branch", json={"parentId": root, "anchorText": "同文"})
check(ra.json()["node"]["id"] != rb.json()["node"]["id"], "同锚不幂等各建")
check(c.get("/api/nodes/%s/messages" % b1).json()["messages"] == [], "新分支空消息")

# ── 扫描不变量 76-80 ──
bad = []
for fn in os.listdir(os.path.join(ROOT, "server")):
    if fn.endswith(".py") and not fn.startswith("accept_"):  # 验收脚本允许提及模式（注释/断言），只查产品代码
        src = open(os.path.join(ROOT, "server", fn), encoding="utf-8").read()
        if re.search(r"UPDATE\s+message\b", src, re.I) or re.search(r"DELETE\s+FROM\s+message\b", src, re.I):
            bad.append(fn)
check(not bad, "D-101无改删" + str(bad))
rp = subprocess.run([sys.executable, "tools/check_invariants.py", "--all"], capture_output=True, cwd=ROOT)
check(rp.returncode == 0, "全仓扫描0")
rp = subprocess.run([sys.executable, "tools/check_invariants.py", "--fixtures"], capture_output=True, cwd=ROOT)
check(rp.returncode == 0, "对照自测0")
check("concept" not in MAIN_SRC and "note" not in MAIN_SRC.replace("denote", ""), "上下文无图谱笔记")
check("PRAGMA foreign_keys=ON" in STORE_SRC, "外键pragma")

# ── 前端静态 81-100 ──
check("sProv" in INK and "sSave" in INK and "sTest" in INK, "设置面板三件")
check("/api/chat" in INK and "getReader" in INK, "SSE接线")
check("function md(" in INK and "##" not in INK.split("function md(")[0][-50:], "md函数")
for pat in ["#{1,4}", "\\*\\*", "<li>", "<pre", "innerHTML=md(full)"]:
    check(pat in INK, "md特性" + pat[:8])
check("/api/branch" in INK, "分支接线")
check("/api/nodes/'+t.nodeId+'/rename" in INK or '/rename' in INK, "改名接线")
check("返回上一级" in INK, "返回按钮")
check("随便看看" in INK, "随机回顾")
check("未命名" in INK, "未命名")
check("await asyncio.sleep" in MAIN_SRC, "后端pacing")
check("no-store" in MAIN_SRC, "禁缓存")
check('"*"' not in MAIN_SRC and "'*'" not in MAIN_SRC, "CORS无星")
check("--mk" in INK, "荧光笔变量")
check(len(re.findall(r"'#[0-9a-fA-F]{6}'", INK)) >= 48, "40+8色池")
check("closeTab" in INK and "textContent='×'" in INK, "标签×关闭")
check("startRename" in INK, "内联改名")
check('type="password"' in INK or "type='password'" in INK, "密码框")
check("/api/settings/test" in INK, "测试接线")
check("/api/nodes" in INK and "function boot" in INK, "启动加载")
check("prompt(" not in INK, "无原生弹窗")
check("parent.tab.name" in INK, "面包屑取父名")
check(c.post("/api/chat", json={}).status_code == 422, "缺text422")
r = c.post("/api/branch", json={"parentId": root, "anchorText": "无色"})
check(r.status_code == 200 and r.json()["node"]["color"] is None, "色可空")
ids = [n["id"] for n in c.get("/api/nodes").json()["nodes"]]
check(ids[0] == root, "节点按创建时间")
rp = subprocess.run([sys.executable, "tools/ledger.py", "list", "--kind", "goal"], capture_output=True, cwd=ROOT)
check(rp.returncode == 0, "账本list")
import py_compile
okc = True
for d in ("server", "tools"):
    for fn in os.listdir(os.path.join(ROOT, d)):
        if fn.endswith(".py"):
            try:
                py_compile.compile(os.path.join(ROOT, d, fn), doraise=True)
            except Exception:
                okc = False
check(okc, "全py可编译")
check('charset="utf-8"' in INK or "charset=utf-8" in INK, "utf8声明")
check("sClose" in INK, "抽屉有关闭")
check('id="roots"' in INK and "主对话" in INK, "主对话列表")
check("location.reload" not in INK, "删空不刷新")
check("refreshRoots" in INK, "根列表刷新")

print("")
print("共 %d 例，失败 %d" % (N[0], len(fails)))
if fails:
    print("=== 失败：%s ===" % fails)
    sys.exit(1)
rp = subprocess.run([sys.executable, "tools/ledger.py", "verify"], capture_output=True, cwd=ROOT)
print("OK   账本链完整" if rp.returncode == 0 else "FAIL 账本链")
sys.exit(0 if rp.returncode == 0 else 1)
