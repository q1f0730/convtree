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

# ── 真流式与可中断 ──
import asyncio as _asyncio
async def _cs(cancel=None):
    out=[]
    async for ch in llm_mod.chat_stream([{"role":"user","content":"hi"}], cancel):
        out.append(ch)
    return out
_full=_asyncio.run(_cs())
check(len(_full)>1, "chat_stream多块")
check("".join(_full)==llm_mod.chat_sync([{"role":"user","content":"hi"}])[0], "chat_stream拼接等于原文")
_ev=_asyncio.Event()
async def _cs_gen():
    async for ch in llm_mod.chat_stream([{"role":"user","content":"hi"}], _ev):
        yield ch
async def _drive():
    g=_cs_gen().__aiter__()
    await g.__anext__()
    _ev.set()
    n=1
    try:
        async for _ in g: n+=1
    except StopAsyncIteration:
        pass
    return n
check(_asyncio.run(_drive())<len(_full), "cancel提前停")
r=c.post("/api/chat", json={"text":"流式多delta"})
check(r.text.count("event: delta")>1, "api多delta")
check("chat_stream" in MAIN_SRC, "真流式接线")
check("asyncio.sleep(0.02)" not in MAIN_SRC, "无假回放")
check("AbortController" in INK, "可中断接线")
check("停止" in INK, "停止钮")

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
check(c.post("/api/branch", json={"parentId": root, "anchorText": "   "}).status_code == 400, "空锚400")
check("isConnected" in INK, "选区防游离")
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
        if re.search(r"UPDATE\s+message\b", src, re.I) or re.search(r"DELETE\s+FROM\s+message\b", src, re.I) or re.search(r"DELETE\s+FROM\s+node\b", src, re.I):
            bad.append(fn)
check(not bad, "D-101无改删" + str(bad))
rp = subprocess.run([sys.executable, "tools/check_invariants.py", "--all"], capture_output=True, cwd=ROOT)
check(rp.returncode == 0, "全仓扫描0")
rp = subprocess.run([sys.executable, "tools/check_invariants.py", "--fixtures"], capture_output=True, cwd=ROOT)
check(rp.returncode == 0, "对照自测0")
_seg = MAIN_SRC.split("def build_context")[1]
_seg = _seg[:_seg.index("\n\ndef ")] if "\n\ndef " in _seg else _seg
check("concept" not in MAIN_SRC and "overlay" not in _seg and "note" not in _seg, "上下文无杂物")
check("PRAGMA foreign_keys=ON" in STORE_SRC, "外键pragma")

# ── 前端静态 81-100 ──
check("sProv" in INK and "sSave" in INK and "sTest" in INK, "设置面板三件")
check("/api/chat" in INK and "getReader" in INK, "SSE接线")
check("function md(" in INK and "##" not in INK.split("function md(")[0][-50:], "md函数")
for pat in ["#{1,4}", "\\*\\*", "<li>", "<pre", "mdOv(full,"]:
    check(pat in INK, "md特性" + pat[:8])
check("/api/branch" in INK, "分支接线")
check("/api/nodes/'+t.nodeId+'/rename" in INK or '/rename' in INK, "改名接线")
check("返回上一级" in INK, "返回按钮")
check("随便看看" in INK, "随机回顾")
check("未命名" in INK, "未命名")
check("await asyncio.sleep" not in MAIN_SRC, "无人工pacing")
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
r = c.post("/api/branch", json={"parentId": root, "anchorText": "总结 Sebastian"})
sn = r.json()["node"]["id"]
r = c.post("/api/nodes/%s/suggest-title" % sn)
check(r.status_code == 200 and r.json()["title"] == "总结 Sebastian", "空分支先吃锚")
c.post("/api/chat", json={"nodeId": sn, "text": "总结测试问题很长很长很长很长"})
r = c.post("/api/nodes/%s/suggest-title" % sn)
check(r.status_code == 200 and 0 < len(r.json()["title"]) <= 16, "AI总结≤16字")
check(c.post("/api/nodes/nd_nope/suggest-title").status_code in (400, 404), "坏点拒绝")
check('id="clist"' in INK and "新对话" in INK, "主对话列表")
check("syncPanels" in INK and "panels[" in INK, "多栏独立开关")
check("some(Boolean)" in INK, "防全关白屏")
r = c.post("/api/branch", json={"parentId": root, "anchorText": "锚定标题xyz"})
an = r.json()["node"]["id"]
r = c.post("/api/nodes/%s/suggest-title" % an)
check(r.status_code == 200 and r.json()["title"] == "锚定标题xyz", "空分支吃锚文本")
check("userNamed" in INK, "手动改名优先AI")
check("const vis=visibleNodes()" in INK and "walk('',0)" in INK, "树按可见集画")
check("function openNode" in INK, "树点击开节点")
check("nodesCache=n.nodes" in INK and "const root=nodesCache.find" in INK, "boot只开一个根")
check("location.reload" not in INK, "删空不刷新")
check("refreshRoots" in INK, "根列表刷新")
check("treeMode" in INK and 'data-v="graph"' in INK and 'data-v="list"' in INK, "导图大纲切换")
check("renderTreeGraph" in INK and "renderTreeList" in INK, "双视图函数")
check("treeMode='graph'" in INK or 'treeMode="graph"' in INK, "默认导图")
check("createElementNS" in INK and "treeGraph" in INK, "SVG图谱")
check("curRootId" in INK, "分支高亮溯根")
check("读取中断" in INK and "空回复" in INK, "流式失败显错")
check("await q.put((\"delta\", err))" in MAIN_SRC, "失败也发delta")
_real_stream = llm_mod.chat_stream
async def _boom_stream(messages, cancel=None):
    raise RuntimeError("boom-test")
    yield
llm_mod.chat_stream = _boom_stream
r = c.post("/api/chat", json={"text": "触发失败路径"})
llm_mod.chat_stream = _real_stream
check(r.status_code == 200 and "生成失败" in r.text, "失败落库且可见")
d = sse_done(r.text)
check(bool(d and d.get("error") is True), "失败带error标")
# ── 删除整支 ──
r = c.post("/api/chat", json={"text": "待删根"})
del_root = sse_done(r.text)["nodeId"]
del_ch = c.post("/api/branch", json={"parentId": del_root, "anchorText": "待删子"}).json()["node"]["id"]
del_gr = c.post("/api/branch", json={"parentId": del_ch, "anchorText": "待删孙"}).json()["node"]["id"]
c.post("/api/chat", json={"nodeId": del_gr, "text": "孙辈发言"})
r = c.post("/api/nodes/%s/delete" % del_ch)
check(r.status_code == 200 and set(r.json()["deleted"]) == {del_ch, del_gr}, "删整支返回两代")
check(r.json()["count"] == 2, "计数2")
ids = [n["id"] for n in c.get("/api/nodes").json()["nodes"]]
check(del_ch not in ids and del_gr not in ids and del_root in ids, "列表只剩根")
check(c.get("/api/nodes/%s/messages" % del_ch).status_code == 404, "已删消息404")
check(c.post("/api/chat", json={"nodeId": del_ch, "text": "x"}).status_code == 404, "已删续聊404")
check(c.post("/api/branch", json={"parentId": del_ch, "anchorText": "x"}).status_code == 404, "已删下挂404")
check(c.post("/api/nodes/%s/rename" % del_gr, json={"title": "x"}).status_code == 404, "已删改名404")
check(c.post("/api/nodes/%s/delete" % del_ch).status_code == 404, "重复删404")
check(c.post("/api/nodes/nd_nope/delete").status_code == 404, "坏点删404")
r = c.post("/api/nodes/%s/delete" % del_root)
check(r.status_code == 200 and r.json()["deleted"] == [del_root], "删根对话")
check(del_root not in [n["id"] for n in c.get("/api/nodes").json()["nodes"]], "根从列表消失")
check('删除该对话' in INK, "侧栏右键删对话")
# ── 标色与笔记落库 ──
r = c.post("/api/chat", json={"text": "标色测试Hello世界"})
ov_root = sse_done(r.text)["nodeId"]
ov_msgs = c.get("/api/nodes/%s/messages" % ov_root).json()["messages"]
midA = [m for m in ov_msgs if m["role"] == "assistant"][0]
alen = len(midA["content"])
r = c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "ul", "start": 0, "end": min(4, alen), "color": "#f5d90a"})
check(r.status_code == 200 and r.json()["start"] == 0, "划线落库")
ov_id = r.json()["id"]
got = c.get("/api/nodes/%s/overlays" % ov_root).json()["overlays"]
check(any(o["id"] == ov_id for o in got), "标色可取回")
midU = [m for m in ov_msgs if m["role"] == "user"][0]
r = c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midU["id"], "kind": "ul", "start": 0, "end": min(2, len(midU["content"])), "color": "#f5d90a"})
check(r.status_code == 200, "用户消息可划线")
check("send.busy" in INK, "发送重入锁")
check("doDelete.busy" in INK, "删除重入锁")
check("first&&t0.nodeId" in INK, "命名守卫空节点")
check("ct.parent={tab:pt}" in INK, "祖链贯通")
check(c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "xx", "start": 0, "end": 1, "color": "#f5d90a"}).status_code == 400, "坏kind400")
check(c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "ul", "start": 0, "end": 1, "color": "red"}).status_code == 400, "坏色400")
check(c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "ul", "start": 5, "end": 2, "color": "#f5d90a"}).status_code == 400, "倒挂400")
check(c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "ul", "start": 0, "end": alen + 100, "color": "#f5d90a"}).status_code == 400, "越界400")
check(c.post("/api/overlays", json={"nodeId": del_root, "messageId": midA["id"], "kind": "ul", "start": 0, "end": 1, "color": "#f5d90a"}).status_code in (400, 404), "串节点拒绝")
check(c.post("/api/overlays", json={"nodeId": "nd_nope", "messageId": midA["id"], "kind": "ul", "start": 0, "end": 1, "color": "#f5d90a"}).status_code == 404, "坏点叠加404")
check(c.get("/api/nodes/nd_nope/overlays").status_code == 404, "坏点取色404")
ctx = build_context(ov_root)
check(ctx == [{"role": m["role"], "content": m["content"]} for m in store_mod.list_messages(ov_root)], "标色不进上下文")
r = c.post("/api/notes", json={"text": "  笔记一  ", "fromNode": ov_root, "fromName": "t"})
check(r.status_code == 200 and r.json()["text"] == "笔记一", "笔记落库去空白")
check(c.post("/api/notes", json={"text": "   "}).status_code == 400, "空笔记400")
c.post("/api/notes", json={"text": "笔记二"})
nl = c.get("/api/notes").json()["notes"]
check([n["text"] for n in nl[:2]] == ["笔记二", "笔记一"], "笔记倒序")
ctx2 = build_context(ov_root)
check(ctx2 == [{"role": m["role"], "content": m["content"]} for m in store_mod.list_messages(ov_root)], "笔记不进上下文")
nid = c.post("/api/notes", json={"text": "待删"}).json()["id"]
check(c.delete("/api/notes/%s" % nid).status_code == 200, "笔记删除200")
check(nid not in [n["id"] for n in c.get("/api/notes").json()["notes"]], "删后列表无")
check(c.delete("/api/notes/%s" % nid).status_code == 404, "重删404")
check(c.delete("/api/notes/nd_nope").status_code == 404, "删坏点404")
check("api/notes/" in INK and "确认删除" in INK, "笔记删除接线")
check("p.innerHTML=md(n.text)" in INK, "笔记渲染markdown")
check("isCollapsed" in INK, "拖选不跳转")
check("scrollTop=st" in INK, "列表保滚动条")
check("overflow-wrap:anywhere" in INK, "长串不断行")
check("if(!out.length)return nodesCache" in INK, "游离回落总览")
check("streamInto" not in INK, "死代码清理")
check("mdOv" in INK and "rangeToSrc" in INK and "attachOvs" in INK, "坐标引擎")
check("dataset.mid" in INK and "/api/overlays" in INK and "/api/notes" in INK, "落库接线")
check("readBack" not in INK and "wrapRange" not in INK, "DOM外挂退役")
check("('tabs').style.display=panels.chat" in INK, "标签跟聊天走")
r = c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "style", "start": 0, "end": min(4, alen), "value": '{"b":1,"size":"lg"}'})
check(r.status_code == 200 and json.loads(r.json()["value"]) == {"b": 1, "size": "lg"}, "排版落库")
check(c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "style", "start": 0, "end": 1, "value": '{"size":"XXL"}'}).status_code == 400, "坏尺寸400")
check(c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "style", "start": 0, "end": 1, "value": '{}'}).status_code == 400, "空格式400")
check(c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "style", "start": 0, "end": 1, "value": '{"nope":1}'}).status_code == 400, "坏键400")
check(c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "style", "start": 0, "end": 1, "value": '{"op":5}'}).status_code == 400, "坏透明400")
check(c.post("/api/overlays", json={"nodeId": ov_root, "messageId": midA["id"], "kind": "mark", "start": 0, "end": 1, "color": "#f25555", "value": '{"b":1}'}).status_code == 400, "mark带值400")
r = c.post("/api/overlays/clear", json={"nodeId": ov_root, "messageId": midA["id"], "start": 0, "end": min(4, alen)})
check(r.status_code == 200 and r.json()["cleared"] >= 1, "清除格式计数")
got = c.get("/api/nodes/%s/overlays" % ov_root).json()["overlays"]
check(not any(o["kind"] == "style" for o in got) and any(o["kind"] == "ul" for o in got), "只清格式不动划线")
r = c.post("/api/overlays/clear", json={"nodeId": ov_root, "messageId": midA["id"], "start": 0, "end": min(4, alen)})
check(r.json()["cleared"] == 0, "复清零")
check(c.post("/api/overlays/clear", json={"nodeId": "nd_nope", "messageId": midA["id"], "start": 0, "end": 1}).status_code == 404, "坏点清除404")
ctx3 = build_context(ov_root)
check(ctx3 == [{"role": m["role"], "content": m["content"]} for m in store_mod.list_messages(ov_root)], "排版不进上下文")
check("showFmtMenu" in INK and 'id="fmtmenu"' in INK, "格式面板")
check("closest('#menu')" in INK, "面板防冒泡误关")
check("contenteditable" not in INK, "正文不可编辑")
check("treeZoom" in INK and "radial-gradient" in INK, "导图缩放点阵")
check("scrollLeft" in INK and "dblclick" in INK, "拖动与复位")
check("panels[m]=true" in INK and "ctrlKey" in INK, "单击切换ctrl并开")
check("treeResize" in INK and "treeWUser" in INK, "树栏拖宽")
check("let treeZoom=1" in INK, "定死100")
check("layoutBlock" in INK, "父居中布局")
check("const shift=(blockW-span)/2;" in INK, "子树无漂移")
check("treeLines" in INK, "标题两行")
check("tabs.includes(pt)" in INK, "返回键防脱钩")
check("title).replace(/" in INK, "标题消毒换行")
check("dataset.nid" in INK, "卡片去重键")
check("tnode span:last-child{min-width:0" in INK, "大纲行收缩")
check("innerHeight-mhh-8" in INK, "选中菜单防溢")
check("innerHeight-mhh2-8" in INK, "树菜单防溢")
check("复制失败，请手动复制" in INK, "复制诚实")
check("z-index:100" in INK, "toast置顶")
check("setSendLock(true)" in INK and "el.disabled=on" in INK, "发送中锁输入")
check("className='resend'" in INK and "lastUserText" in INK and "isFailText" in INK, "失败重发按钮")
check("Date.now()-suppressClick<600" in INK, "拖拽抑制自愈")
check(INK.count("!e.isComposing") >= 3, "输入法回车守卫")
check("拖栏必回自适应" not in INK, "拖栏不碰缩放")
check("border-left:3px" in INK and "<hr" in INK, "引用与分割线")
check("convS" in INK and "convE" in INK, "边界左闭右开")
check("'r',2.6" in INK, "连线接点")
check("visibleNodes" in INK and 'id="treeScope"' in INK, "一对话一树")
check('id="treeFold"' in INK and 'id="treeUnfold"' in INK and '#tree.shut' in INK, "树头收起展开")
check('data-m="tree"' in INK, "侧栏树图标（E-0002标准12反转E-0055旧口径）")
check("data-ref" in INK and "openRef" in INK, "点标跳分支")
check("panels.tree=true" in INK, "树随聊天开")
check(INK.index('id="center"') < INK.index('id="tree"'), "树在聊天右")
check("syncPanels();refreshRoots" in INK, "启动同步面板")
check("busY" in INK, "总线式连线")
check('class="sheet"' in INK and "backdrop-filter" in INK, "设置独立页")
check("target.id==='drawer'" in INK, "点空白关设置")
check(len(store_mod.list_messages(del_gr)) == 2, "消息行冻结保留")
check(build_context(del_gr) == [], "已删无上下文")
import sqlite3 as _sq
old_db = os.path.join(ROOT, "data", "mig_old.db")
for sfx in ("", "-wal", "-shm", "-journal"):
    if os.path.exists(old_db + sfx):
        os.remove(old_db + sfx)
_con = _sq.connect(old_db)
_con.execute("CREATE TABLE node(id TEXT PRIMARY KEY,parent_id TEXT,title TEXT NOT NULL,anchor_text TEXT,color TEXT,created_at INTEGER NOT NULL)")
_con.execute("INSERT INTO node VALUES('nd_old',NULL,'老节点',NULL,NULL,1)")
_con.execute("CREATE TABLE message(id TEXT PRIMARY KEY,node_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at INTEGER NOT NULL)")
_con.execute("CREATE TABLE overlay(id TEXT PRIMARY KEY,node_id TEXT,message_id TEXT,kind TEXT,start INTEGER,end_pos INTEGER,color TEXT,ref_node TEXT,created_at INTEGER)")
_con.commit()
_con.close()
_prev = store_mod.DB_PATH
store_mod.DB_PATH = old_db
try:
    store_mod.init_db()
    _con = _sq.connect(old_db)
    cols = [rr[1] for rr in _con.execute("PRAGMA table_info(node)")]
    _con.close()
    check("deleted" in cols, "老库迁移出deleted列")
    _con2 = _sq.connect(old_db)
    ocols = [rr[1] for rr in _con2.execute("PRAGMA table_info(overlay)")]
    _con2.close()
    check("value" in ocols, "老库迁移出value列")
    check(any(n["id"] == "nd_old" for n in store_mod.list_nodes()), "老数据可读")
finally:
    store_mod.DB_PATH = _prev
for sfx in ("", "-wal", "-shm", "-journal"):
    if os.path.exists(old_db + sfx):
        os.remove(old_db + sfx)
check('id="treemenu"' in INK and 'id="batchbar"' in INK, "删除菜单与批量条")
check("oncontextmenu" in INK and "showTreeMenu" in INK, "树右键菜单")
check("/delete" in INK and "doDelete" in INK and "selNodes" in INK, "删除接线与多选")
check("确认删除" in INK, "二次确认")
check("NODE_W" in INK and "NODE_H" in INK, "固定框尺寸")
check('id="treeScroll"' in INK, "图谱滚动区")
check("is-thinking" in INK and "@keyframes think" in INK and "@keyframes breathe" in INK, "思考动画")
check("caret" in INK and "@keyframes blink" in INK, "流式光标")
check(INK.index('id="center"') < INK.index('id="tabs"') < INK.index('id="chat"'), "标签列内序")
check("tabs.length=0;cur=null" in INK, "新对话全新页")
check("const open=tabs.find" not in INK, "侧栏无过期标签引用")
check("<svg" in INK and "viewBox" in INK, "简约线性图标")
check(not any(e in INK for e in ["💬", "📝", "🌳", "⚙"]), "无emoji图标")
check(INK.count("border-right:1px solid var(--line)") >= 2 and "border-left:1px solid var(--line)" in INK, "栏间分界")
check("#tabs::after" in INK, "标签栏filler线")
check("overflow-y:hidden" in INK, "禁标签竖滚动条")
check("::-webkit-scrollbar-thumb" in INK and "scrollbar-button" in INK, "自定义滚动条")
check("treeMaxW" in INK and "innerWidth-60-236-480" in INK, "树宽钳制保聊天")
check("padding:24px 20px" in INK and "padding:14px 18px" in INK, "消息区收边")
v = c.get("/api/version").json()
check(v.get("build"), "版本端点")
check('id="buildTag"' in INK, "构建号水印")
check("已开新对话" in INK, "新对话反馈")
check("addEventListener('resize'" in INK, "窗口缩放重钳")
check('title="收起"' in INK and 'title="展开对话树"' in INK, "收起提示")

# ── E-0002 十三项补齐：分支命名/右键菜单/中键新开/自选色/随机色/树时机/侧栏图标 ──
bseg = INK[INK.index("act==='branch'"):]
check("name:'未命名'" in bseg, "分支初名未命名")
check("suggest-title" not in bseg, "分支时刻不请求AI命名")
check('id="tabmenu"' in INK and "function showTabMenu" in INK, "标签右键菜单存在")
check("add('重命名'" in INK and "add('关闭'" in INK and "add('关闭其他标签页'" in INK, "右键三项齐")
check("function closeOthers" in INK, "关闭其他标签页实现")
check(INK.count("auxclick") >= 3, "树/列表/标签中键接线")
check("openNode(n,true)" in INK and "function openNode(n,bg)" in INK, "中键新开不抢焦点")
check("function showUlPal" in INK and "function hidePal" in INK and "applyUl" in INK, "划线自选色板")
check("getElementById('pal')" in INK and "color:color" in INK, "色板DOM接线且选色落库")
nc = INK[INK.index("function nextColor"):INK.index("function seedUsed")]
check("Math.random" in nc and "filter" in nc, "荧光笔真随机取色")
check("used.add(n.color)" in INK and "nodesCache=d.nodes||[];seedUsed()" in INK, "重载播种已用色")
check("FAKE" not in INK, "死示例文本已删")
check("treeAutoShown" in INK and "hasBranch" in INK, "树分叉后才首次露出")
check("const panels={chat:true,note:false,tree:false}" in INK, "初始树相暂不露")
check("#tree.nov" in INK, "无分叉时树面板整体隐藏")
check('title="树"' in INK, "侧栏树图标title")

# ── 里程碑1 资料底座（E-0071）：笔记本 / 资料 / BM25 / 引用 ──
import io as _io
import sqlite3 as _sq3
from server import rag as rag_mod
from server import ingest as ingest_mod
INGEST_SRC = open(os.path.join(ROOT, "server", "ingest.py"), encoding="utf-8").read()
RAG_SRC = open(os.path.join(ROOT, "server", "rag.py"), encoding="utf-8").read()
REQ_SRC = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read()


def sse_events(text):
    out = []
    for m in re.finditer(r'event: (\w+)\ndata: (.*)\n\n', text):
        out.append((m.group(1), json.loads(m.group(2))))
    return out


# 零重依赖
check("chromadb" not in REQ_SRC and "sentence-transformers" not in REQ_SRC and "torch" not in REQ_SRC, "requirements无重依赖")
check("import chromadb" not in RAG_SRC and "sentence_transformers" not in RAG_SRC, "rag不引chroma/st")
check("python-multipart" in REQ_SRC, "requirements含python-multipart")
check(rag_mod.backend() == "bm25", "默认后端BM25")
check(c.get("/api/health").json().get("rag") == "bm25", "health报告rag后端")
# 分词
tk = rag_mod.tokenize("咖啡豆 Coffee beans, the BM25!")
check("咖啡" in tk and "啡豆" in tk, "中文字二元组")
check("coffee" in tk and "beans" in tk and "bm25" in tk, "英文词小写")
check("the" not in tk, "英文虚词去除")
check(rag_mod.tokenize("我") == ["我"], "单字保留")
check(rag_mod.tokenize("") == [], "空串无词")
# 切块偏移
check(rag_mod.chunk_text("abcdefghij", size=4, overlap=1) == ['abcd', 'defg', 'ghij'], "chunk_text切块")
try:
    rag_mod.chunk_spans("abc", size=4, overlap=4)
    check(False, "overlap>=size应拒")
except ValueError:
    check(True, "overlap>=size拒绝")
long_txt = ("第一段讲咖啡的烘焙。" * 30 + "\n\n" + "Second part is about tea brewing. " * 20).strip()
sp = rag_mod.chunk_spans(long_txt, size=120, overlap=20)
check(len(sp) > 3, "长文多块")
check(all(0 <= a < b <= len(long_txt) for a, b in sp), "偏移在界内")
check(all(long_txt[a:b] == long_txt[a:b].strip() for a, b in sp), "块首尾无空白")
check(sp[-1][1] == len(long_txt), "末块到文末")
check(all(sp[k + 1][0] > sp[k][0] for k in range(len(sp) - 1)), "块起点单调")

# 笔记本 + 资料
r = c.post("/api/projects", json={"name": "测试笔记本"})
check(r.status_code == 200 and r.json()["name"] == "测试笔记本", "建笔记本")
pid = r.json()["id"]
check(c.post("/api/projects", json={"name": "  "}).status_code == 400, "空名笔记本400")
check(any(p["id"] == pid for p in c.get("/api/projects").json()["projects"]), "列笔记本")
check(c.put("/api/projects/" + pid, json={"name": "改名本"}).json()["name"] == "改名本", "笔记本改名")
TXT_ZH = "咖啡产地决定风味。耶加雪菲来自埃塞俄比亚，有明显的茉莉花香和柑橘果酸。\n\n深烘焙的曼特宁口感厚重，带草本和巧克力味。"
TXT_EN = "Photosynthesis converts light energy into chemical energy. Chlorophyll absorbs red and blue light.\n\nThe Calvin cycle fixes carbon dioxide into sugar."
r = c.post("/api/sources", data={"project_id": pid, "text": TXT_ZH, "name": "咖啡笔记"})
check(r.status_code == 200 and r.json()["name"] == "咖啡笔记", "粘贴文本入库")
check(r.json().get("chunks", 0) >= 1, "入库返回块数")
check("content" not in r.json(), "入库回包不回全文")
s_zh = r.json()["id"]
r = c.post("/api/sources", data={"project_id": pid},
           files={"file": ("bio.md", TXT_EN.encode("utf-8"), "text/markdown")})
check(r.status_code == 200 and r.json()["type"] == "md" and r.json()["name"] == "bio.md", "md文件入库")
s_en = r.json()["id"]
r = c.post("/api/sources", data={"project_id": pid},
           files={"file": ("gbk.txt", "国标编码的中文资料：普洱茶需要醒茶。".encode("gb18030"), "text/plain")})
check(r.status_code == 200, "GB18030文本入库")
s_gbk = r.json()["id"]
r = c.post("/api/sources", data={"project_id": pid},
           files={"file": ("crlf.txt", "第一行\r\n第二行\r\n".encode("utf-8"), "text/plain")})
s_crlf = r.json()["id"]
check("\r" not in store_mod.get_source(s_crlf)["content"], "换行归一为LF")
from docx import Document as _Doc
_d = _Doc()
_d.add_paragraph("量子纠缠是两个粒子状态相关联的现象。")
_d.add_paragraph("Entanglement survives distance.")
_bio = _io.BytesIO()
_d.save(_bio)
r = c.post("/api/sources", data={"project_id": pid},
           files={"file": ("q.docx", _bio.getvalue(), "application/octet-stream")})
check(r.status_code == 200 and r.json()["type"] == "docx", "docx入库")
s_docx = r.json()["id"]
check("量子纠缠" in store_mod.get_source(s_docx)["content"], "docx抽出正文")
# 手写最小 PDF（Helvetica 文本）
_stream = b"BT /F1 18 Tf 72 720 Td (Mitochondria is the powerhouse of the cell) Tj ET"
_objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
         b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
         b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
         b"<< /Length %d >>\nstream\n" % len(_stream) + _stream + b"\nendstream",
         b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
_pdf = b"%PDF-1.4\n"
_offs = []
for _k, _o in enumerate(_objs, 1):
    _offs.append(len(_pdf))
    _pdf += b"%d 0 obj\n" % _k + _o + b"\nendobj\n"
_xref = len(_pdf)
_pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(_objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in _offs)
_pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(_objs) + 1, _xref)
r = c.post("/api/sources", data={"project_id": pid},
           files={"file": ("cell.pdf", _pdf, "application/pdf")})
check(r.status_code == 200 and r.json()["type"] == "pdf", "pdf入库")
s_pdf = r.json()["id"]
check("Mitochondria" in store_mod.get_source(s_pdf)["content"], "pdf抽出正文")
# URL（mock 抓取）
_real_fetch = ingest_mod.fetch_url
_HTML = ("<html><head><title>黑洞简介</title><style>.x{color:red}</style><script>var secret='JSLEAK';</script></head>"
         "<body><nav>导航菜单</nav><h1>黑洞</h1><p>黑洞是引力强到光都无法逃逸的天体。</p><p>事件视界&amp;奇点。</p></body></html>")
ingest_mod.fetch_url = lambda u: ("text/html; charset=utf-8", _HTML.encode("utf-8"))
r = c.post("/api/sources", data={"project_id": pid, "url": "https://example.com/bh"})
check(r.status_code == 200 and r.json()["type"] == "url", "URL入库200")
check(r.json()["name"] == "黑洞简介", "URL用title命名")
s_url = r.json()["id"]
_uc = store_mod.get_source(s_url)["content"]
check("黑洞是引力强到光都无法逃逸的天体" in _uc, "HTML正文抽出")
check("JSLEAK" not in _uc and "color:red" not in _uc and "导航菜单" not in _uc, "script/style/nav剔除")
check("事件视界&奇点" in _uc, "HTML实体解码")
ingest_mod.fetch_url = lambda u: ("text/plain", "纯文本网页：海豚用回声定位。".encode("utf-8"))
r = c.post("/api/sources", data={"project_id": pid, "url": "http://example.com/a.txt"})
check(r.status_code == 200 and "回声定位" in store_mod.get_source(r.json()["id"])["content"], "URL纯文本入库")
s_url2 = r.json()["id"]
check(c.post("/api/sources", data={"project_id": pid, "url": "ftp://example.com/x"}).status_code == 400, "非http URL 400")
check(c.post("/api/sources", data={"project_id": pid, "url": "不是网址"}).status_code == 400, "乱写URL 400")


def _boom(u):
    raise ValueError("URL 抓取失败：ConnectError")


ingest_mod.fetch_url = _boom
r = c.post("/api/sources", data={"project_id": pid, "url": "https://down.example.com/"})
check(r.status_code == 400 and "抓取失败" in r.json()["detail"], "抓取失败400")
ingest_mod.fetch_url = lambda u: ("text/html", b"<html><body><script>x()</script></body></html>")
check(c.post("/api/sources", data={"project_id": pid, "url": "https://empty.example.com/"}).status_code == 400, "空网页400")
ingest_mod.fetch_url = _real_fetch
# 失败模式
check(c.post("/api/sources", data={"project_id": pid},
             files={"file": ("empty.txt", b"", "text/plain")}).status_code == 400, "空文件400")
check(c.post("/api/sources", data={"project_id": pid},
             files={"file": ("blank.txt", b"  \n\n ", "text/plain")}).status_code == 400, "纯空白文件400")
check(c.post("/api/sources", data={"project_id": pid},
             files={"file": ("a.exe", b"MZ\x90\x00", "application/octet-stream")}).status_code == 415, "不支持类型415")
check(c.post("/api/sources", data={"project_id": pid},
             files={"file": ("noext", b"hello", "application/octet-stream")}).status_code == 415, "无扩展名415")
check(c.post("/api/sources", data={"project_id": pid},
             files={"file": ("bad.pdf", b"not a pdf", "application/pdf")}).status_code == 400, "坏PDF 400")
check(c.post("/api/sources", data={"project_id": pid},
             files={"file": ("bad.docx", b"PK-not-docx", "application/octet-stream")}).status_code == 400, "坏docx 400")
check(c.post("/api/sources", data={"project_id": pid, "text": "   "}).status_code == 400, "空粘贴400")
check(c.post("/api/sources", data={"project_id": "prj_nope", "text": "x"}).status_code == 404, "笔记本不存在404")
check(c.post("/api/sources", data={"text": "x"}).status_code == 422, "缺project_id 422")
# 列表带块数
srcs = c.get("/api/sources?project_id=" + pid).json()["sources"]
check(len(srcs) == 8, "列资料8条")
check(all(sv.get("chunks", 0) >= 1 for sv in srcs), "每条资料有块数")
# 偏移 = 原文切片（被消费数据断言）
_con = _sq3.connect(DB)
_rows = _con.execute("SELECT c.start,c.end_pos,c.text,s.content FROM chunk c JOIN source s ON s.id=c.source_id WHERE c.project_id=?", (pid,)).fetchall()
_con.close()
check(len(_rows) >= 8, "块已落SQLite")
check(all(rw[3][rw[0]:rw[1]] == rw[2] for rw in _rows), "块文==原文[start:end]")
# BM25 检索
h = rag_mod.retrieve(pid, "耶加雪菲是什么香味")
check(bool(h) and h[0]["source_id"] == s_zh, "中文检索命中正确资料")
check(all(k in h[0] for k in ("chunk_id", "source_name", "text", "start", "end", "score")), "检索结果字段齐")
h = rag_mod.retrieve(pid, "How does chlorophyll absorb light?")
check(bool(h) and h[0]["source_id"] == s_en, "英文检索命中")
h = rag_mod.retrieve(pid, "普洱")
check(bool(h) and h[0]["source_id"] == s_gbk, "GB18030资料可检索")
h = rag_mod.retrieve(pid, "mitochondria powerhouse")
check(bool(h) and h[0]["source_id"] == s_pdf, "PDF资料可检索")
check(rag_mod.retrieve(pid, "zzzqqq 无关词汇鑫") == [], "无关查询返回空")
check(rag_mod.retrieve(pid, "") == [], "空查询返回空")
check(rag_mod.retrieve("prj_nope", "咖啡") == [], "空笔记本返回空")
check(len(rag_mod.retrieve(pid, "的", top_k=2)) <= 2, "top_k生效")
# 混合后端（嵌入 mock）+ 失败回落
_real_embed = rag_mod._embed
c.post("/api/settings", json={"embedModel": "text-embedding-v3", "embedBaseUrl": "https://emb.example/v1", "embedKey": "sk-emb-123456789"})
check(rag_mod.backend() == "hybrid", "配嵌入后为hybrid")
check(rag_mod.embed_config()[0] == "https://emb.example/v1", "嵌入base可单独配")
_pc = c.get("/api/settings").json()["config"]
check(_pc.get("embed_model") == "text-embedding-v3" and _pc.get("has_embed_key") is True, "嵌入配置可读")
check("sk-emb-123456789" not in json.dumps(_pc), "嵌入密钥不回明文")
_calls = []


def _fake_embed(texts):
    _calls.append(len(texts))
    return [[1.0 if "咖啡" in t or "耶加雪菲" in t else 0.0, 1.0 if "light" in t.lower() else 0.0, 0.1] for t in texts]


rag_mod._embed = _fake_embed
h = rag_mod.retrieve(pid, "耶加雪菲")
check(bool(h) and h[0]["source_id"] == s_zh, "hybrid检索命中")
check(sum(_calls) >= len(_rows), "缺向量的块就地补嵌")
_con = _sq3.connect(DB)
check(_con.execute("SELECT COUNT(*) FROM chunk WHERE project_id=? AND embedding IS NULL", (pid,)).fetchone()[0] == 0, "补嵌后向量落库")
_con.close()


def _bad_embed(texts):
    raise RuntimeError("embedding down")


rag_mod._embed = _bad_embed
h = rag_mod.retrieve(pid, "耶加雪菲")
check(bool(h) and h[0]["source_id"] == s_zh, "嵌入失败回落BM25")
r = c.post("/api/sources", data={"project_id": pid, "text": "嵌入挂了也能入库的资料：银杏是活化石。"})
check(r.status_code == 200 and r.json()["chunks"] >= 1, "嵌入失败入库不拦")
s_gingko = r.json()["id"]
rag_mod._embed = _real_embed
c.post("/api/settings", json={"embedModel": "", "embedBaseUrl": "", "clearEmbedKey": True})
check(rag_mod.backend() == "bm25", "清嵌入配置回BM25")
# 有据回答：cites SSE + 落库 + 不改 message.content
r = c.post("/api/chat", json={"text": "耶加雪菲有什么风味？", "projectId": pid})
ev = sse_events(r.text)
names = [e[0] for e in ev]
check("cites" in names, "SSE含cites事件")
check(names.index("cites") < names.index("delta"), "cites先于正文")
cites_ev = [e[1] for e in ev if e[0] == "cites"][0]["cites"]
check([ct["n"] for ct in cites_ev] == list(range(1, len(cites_ev) + 1)), "引用编号1..k")
check(cites_ev[0]["source_id"] == s_zh and cites_ev[0]["source_name"] == "咖啡笔记", "引用指向资料id/名")
_src_zh = store_mod.get_source(s_zh)["content"]
check(_src_zh[cites_ev[0]["start"]:cites_ev[0]["end"]] == cites_ev[0]["text"], "引用偏移指向原文段")
d = sse_done(r.text)
nb_root, nb_mid = d["nodeId"], d["messageId"]
check(d.get("cites") == len(cites_ev), "done带引用数")
streamed = "".join(e[1]["text"] for e in ev if e[0] == "delta")
msgs = c.get("/api/nodes/%s/messages" % nb_root).json()["messages"]
am = [m for m in msgs if m["id"] == nb_mid][0]
check(am["content"] == streamed, "message.content==流式原文（未注入引用）")
check(len(am["cites"]) == len(cites_ev) and am["cites"][0]["text"] == cites_ev[0]["text"], "引用随消息落库可取回")
check([m for m in msgs if m["role"] == "user"][0]["cites"] == [], "用户消息无引用")
check("[1]" in am["content"], "mock回答含[1]示例")
ctx, cc = build_context(nb_root, query="曼特宁口感", with_cites=True)
check(ctx[0]["role"] == "system" and "【参考资料】" in ctx[0]["content"] and "[1]" in ctx[0]["content"], "上下文前置参考资料")
check("资料未提及" in ctx[0]["content"], "提示不编造")
check(bool(cc) and cc[0]["source_id"] == s_zh, "build_context回传引用")
check(store_mod.node_path(nb_root)[0]["project_id"] == pid, "根节点挂笔记本")
# 分支继承 path[0] 的笔记本
br = c.post("/api/branch", json={"parentId": nb_root, "anchorText": "茉莉花香"}).json()["node"]
check(not br.get("project_id"), "分支节点自身不存笔记本")
r = c.post("/api/chat", json={"nodeId": br["id"], "text": "Calvin cycle 是什么"})
ev2 = sse_events(r.text)
check("cites" in [e[0] for e in ev2], "分支提问同样有引用")
c2 = [e[1] for e in ev2 if e[0] == "cites"][0]["cites"]
check(any(ct["source_id"] == s_en for ct in c2), "分支检索用根笔记本资料")
br2 = c.post("/api/branch", json={"parentId": br["id"], "anchorText": "孙节点"}).json()["node"]
_, cc3 = build_context(br2["id"], query="黑洞事件视界", with_cites=True)
check(any(ct["source_id"] == s_url for ct in cc3), "孙节点也继承")
# 无笔记本 → 无引用
r = c.post("/api/chat", json={"text": "耶加雪菲有什么风味？"})
check("event: cites" not in r.text, "无笔记本不发cites")
check(c.post("/api/chat", json={"text": "x", "projectId": "prj_nope"}).status_code == 404, "坏笔记本id 404")
# 检索失败不拦聊天
_real_retrieve = rag_mod.retrieve


def _rboom(*a, **k):
    raise RuntimeError("retrieve down")


rag_mod.retrieve = _rboom
r = c.post("/api/chat", json={"text": "仍能回答吗", "projectId": pid})
check(r.status_code == 200 and "event: done" in r.text and "event: cites" not in r.text, "检索异常聊天照常")
rag_mod.retrieve = _real_retrieve
# 删除：块随资料走；引用是快照仍可读
r = c.delete("/api/sources/" + s_zh)
check(r.status_code == 200, "删资料")
_con = _sq3.connect(DB)
check(_con.execute("SELECT COUNT(*) FROM chunk WHERE source_id=?", (s_zh,)).fetchone()[0] == 0, "删资料清块")
_con.close()
check(all(hh["source_id"] != s_zh for hh in rag_mod.retrieve(pid, "耶加雪菲")), "删后检索不到")
am2 = [m for m in c.get("/api/nodes/%s/messages" % nb_root).json()["messages"] if m["id"] == nb_mid][0]
check(am2["cites"] and am2["cites"][0]["source_name"] == "咖啡笔记", "删资料后引用快照仍在")
check(c.delete("/api/sources/src_nope").status_code == 404, "删不存在资料404")
r = c.delete("/api/projects/" + pid)
check(r.status_code == 200 and c.get("/api/sources?project_id=" + pid).json()["sources"] == [], "删笔记本级联资料")
_con = _sq3.connect(DB)
check(_con.execute("SELECT COUNT(*) FROM chunk WHERE project_id=?", (pid,)).fetchone()[0] == 0, "删笔记本清块")
_con.close()
check(c.get("/api/nodes/%s/messages" % nb_root).status_code == 200, "删笔记本后会话仍可读")
check(c.delete("/api/projects/prj_nope").status_code == 404, "删不存在笔记本404")
# D-101：新代码无改写原文
check(not re.search(r"UPDATE\s+message\s+SET", STORE_SRC + MAIN_SRC + RAG_SRC + INGEST_SRC, re.I), "新代码无UPDATE message")
check("citation" in STORE_SRC and "add_citations" in STORE_SRC, "引用独立表")
# 前端（静态接线断言）
check('id="projSel"' in INK and 'id="projNew"' in INK and "不使用笔记本" in INK, "前端笔记本选择器")
check('id="srcBtn"' in INK and 'id="srcCount"' in INK, "资料入口带计数")
check('id="srcUrl"' in INK and "fd.append('url'" in INK, "前端URL添加接线")
check('accept=".txt,.md,.markdown,.pdf,.docx"' in INK, "文件类型限定")
check("(s.chunks||0)+' 块'" in INK, "资料列表显示块数")
check("method:'DELETE'" in INK and "/api/sources/'+s.id" in INK, "前端删资料接线")
check("projectId:curProject" in INK, "新对话带笔记本")
check("m[1]==='cites'" in INK and "cites:m.cites||[]" in INK, "前端接cites事件与落库引用")
check('id="citePop"' in INK and "function showCitePop" in INK and "cp-tx" in INK, "引用气泡")
check("function mdcite(h,cites)" in INK and "ok.has(n)" in INK, "仅对有效编号渲染chip")
check("tx.textContent=c.text" in INK and "nm.textContent=c.source_name" in INK, "气泡用textContent防注入")
check("syncProjectFromCur" in INK, "切换会话同步笔记本")
check(INK.count("--acc-soft") >= 3 and ":root{--bg:" in INK, "沿用纸墨设计变量")
check('id="empty"' in INK and 'id="boxrow"' in INK, "首屏仍为单输入框")

print("")
print("共 %d 例，失败 %d" % (N[0], len(fails)))
if fails:
    print("=== 失败：%s ===" % fails)
    sys.exit(1)
rp = subprocess.run([sys.executable, "tools/ledger.py", "verify"], capture_output=True, cwd=ROOT)
print("OK   账本链完整" if rp.returncode == 0 else "FAIL 账本链")
sys.exit(0 if rp.returncode == 0 else 1)
