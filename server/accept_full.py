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
_real_chat = llm_mod.chat_sync
def _boom(messages):
    raise RuntimeError("boom-test")
llm_mod.chat_sync = _boom
r = c.post("/api/chat", json={"text": "触发失败路径"})
llm_mod.chat_sync = _real_chat
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
check("拖栏必回自适应" not in INK, "拖栏不碰缩放")
check("border-left:3px" in INK and "<hr" in INK, "引用与分割线")
check("convS" in INK and "convE" in INK, "边界左闭右开")
check("'r',2.6" in INK, "连线接点")
check("visibleNodes" in INK and 'id="treeScope"' in INK, "一对话一树")
check('id="treeFold"' in INK and 'id="treeUnfold"' in INK and '#tree.shut' in INK, "树头收起展开")
check('data-m="tree"' not in INK, "侧栏无树开关")
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

print("")
print("共 %d 例，失败 %d" % (N[0], len(fails)))
if fails:
    print("=== 失败：%s ===" % fails)
    sys.exit(1)
rp = subprocess.run([sys.executable, "tools/ledger.py", "verify"], capture_output=True, cwd=ROOT)
print("OK   账本链完整" if rp.returncode == 0 else "FAIL 账本链")
sys.exit(0 if rp.returncode == 0 else 1)
