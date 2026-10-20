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
section("02 模型接入配置：默认预设 Inception Mercury")
reset_settings()
g = client.get("/api/settings").json()
check("inception" in g["providers"], "预设含 inception")
check(g["config"]["provider"] == "inception", "默认预设 = inception")
check(g["config"]["base_url"] == "https://api.inceptionlabs.ai/v1", "默认 base = api.inceptionlabs.ai/v1")
check(g["config"]["model"] == "mercury-3", "默认模型 mercury-3")
check(g["config"]["has_key"] is False and g["config"]["configured"] is False, "默认无 key、未配置")
r = client.post("/api/chat", json={"text": "无key时"})
check("mock" in r.text, "无 key 时走 mock 回复")


class Upstream(http.server.BaseHTTPRequestHandler):
    """假的 OpenAI 兼容上游：记录收到的 Authorization 与请求体；按 MODE 回应。"""
    GOT = []
    MODE = {"kind": "mercury"}

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        Upstream.GOT.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        kind = Upstream.MODE["kind"]
        if kind == "401":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"Invalid API key","code":"invalid_api_key"}}')
            return
        if self.path.endswith("/embeddings"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            n = len(json.loads(body).get("input") or [])
            self.wfile.write(json.dumps({"data": [{"index": i, "embedding": [1.0, 0.0]} for i in range(n)]}).encode())
            return
        if '"stream": true' in body:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            # Mercury 实测分块格式：首块 role+空 content，中间 delta.content，末块 finish_reason+usage，再 [DONE]
            chunks = [{"id": "c1", "object": "chat.completion.chunk", "model": "mercury-3",
                       "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}]
            for piece in ["瀑布模型", "要求前期", "冻结需求 [1]。"]:
                chunks.append({"id": "c1", "object": "chat.completion.chunk", "model": "mercury-3",
                               "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]})
            chunks.append({"id": "c1", "object": "chat.completion.chunk", "model": "mercury-3",
                           "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 10, "completion_tokens": 8}})
            for ch in chunks:
                self.wfile.write(("data: " + json.dumps(ch, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": {"content": "pong"}}]}).encode())

    def log_message(self, *a):
        pass


_up, UP = serve(Upstream)
UPBASE = "http://127.0.0.1:%d/v1" % UP
# 第二个"攻击者"主机：同一桩换个主机名（localhost vs 127.0.0.1 是不同 host）
EVIL = "http://localhost:%d/v1" % UP
SECRET = "sk-REAL-SECRET-0123456789"

section("02 P0-1 key 绑定主机")
reset_settings()
client.post("/api/settings", json={"provider": "custom", "baseUrl": UPBASE, "apiKey": SECRET})
Upstream.GOT.clear()
client.post("/api/settings/test")
check(any(g["auth"] == "Bearer " + SECRET for g in Upstream.GOT), "正常主机收到 key（阳性对照）")
r = client.post("/api/settings", json={"baseUrl": EVIL})
check(r.json()["config"]["has_key"] is False, "test_key_cleared_when_base_host_changes：改主机不带 key ⇒ key 清空")
Upstream.GOT.clear()
client.post("/api/settings/test")
check(not any(SECRET in (g["auth"] or "") for g in Upstream.GOT), "test_key_not_sent_to_new_host：新主机收不到旧 key")
client.post("/api/settings", json={"baseUrl": UPBASE, "apiKey": SECRET})
r = client.post("/api/settings", json={"model": "mercury-2"})
check(r.json()["config"]["has_key"] is True, "只改模型 key 保留")
r = client.post("/api/settings", json={"baseUrl": UPBASE + "/"})
check(r.json()["config"]["has_key"] is True, "同主机改路径 key 保留")
r = client.post("/api/settings", json={"baseUrl": EVIL, "apiKey": "sk-new-key-999999"})
check(r.json()["config"]["has_key"] is True, "改主机同时给新 key ⇒ 采用新 key")
r = client.post("/api/settings", json={"provider": "deepseek"})
check(r.json()["config"]["has_key"] is False, "换服务商预设（主机变）⇒ key 清空")
# 嵌入：跨主机不回落聊天 key
reset_settings()
client.post("/api/settings", json={"provider": "custom", "baseUrl": UPBASE, "apiKey": SECRET,
                                   "embedModel": "e1", "embedBaseUrl": EVIL})
Upstream.GOT.clear()
pid0 = client.post("/api/projects", json={"name": "emb"}).json()["id"]
client.post("/api/sources", data={"project_id": pid0, "text": "hello world 嵌入测试"})
emb_calls = [g for g in Upstream.GOT if g["path"].endswith("/embeddings")]
check(not any(SECRET in (g["auth"] or "") for g in emb_calls), "test_embed_no_chat_key_fallback_cross_host：嵌入跨主机不带聊天 key")
r = client.post("/api/settings", json={"embedBaseUrl": UPBASE, "embedKey": "sk-emb-1234567"})
r = client.post("/api/settings", json={"embedBaseUrl": EVIL})
check(r.json()["config"]["has_embed_key"] is False, "改嵌入主机不带 key ⇒ 嵌入 key 清空")
reset_settings()

section("02 Host 头白名单（DNS rebinding）")
check(client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400, "test_host_guard_rejects_unknown_host")
check(client.get("/api/health", headers={"Host": "localhost:8787"}).status_code == 200, "localhost 放行")
check(client.get("/api/health", headers={"Host": "127.0.0.1:3000"}).status_code == 200, "127.0.0.1 放行")
os.environ["CONVTREE_ALLOWED_HOSTS"] = "3000-abc.instabox.tech,*.trycloudflare.com"
check(client.get("/api/health", headers={"Host": "3000-abc.instabox.tech"}).status_code == 200, "配置的公网主机放行")
check(client.get("/api/health", headers={"Host": "x.trycloudflare.com"}).status_code == 200, "通配主机放行")
check(client.get("/api/health", headers={"Host": "3000-abc.instabox.tech.evil.com"}).status_code == 400, "后缀伪装被拒")
os.environ.pop("CONVTREE_ALLOWED_HOSTS")

section("02 P2-1 环境变量 / .env 优先级")
reset_settings()
with open(os.environ["CONVTREE_DOTENV"], "w") as f:
    f.write("LLM_BASE_URL=%s\nLLM_MODEL=env-model\nLLM_API_KEY=sk-from-dotenv-1\n" % UPBASE)
g = client.get("/api/settings").json()["config"]
check(g["base_url"] == UPBASE and g["model"] == "env-model", "test_dotenv_loaded：无配置文件时 .env 生效")
Upstream.GOT.clear()
client.post("/api/settings/test")
check(any(x["auth"] == "Bearer sk-from-dotenv-1" for x in Upstream.GOT), ".env 的 key 发往 .env 的 base")
client.post("/api/settings", json={"baseUrl": EVIL})
Upstream.GOT.clear()
client.post("/api/settings/test")
check(not any("sk-from-dotenv-1" in (x["auth"] or "") for x in Upstream.GOT), "配置文件改了 base ⇒ 环境 key 不跟过去")
os.remove(os.environ["CONVTREE_DOTENV"])
reset_settings()

section("02 P1-4 上游错误不被吞 + Mercury 流格式")
client.post("/api/settings", json={"provider": "custom", "baseUrl": UPBASE, "apiKey": "sk-x-123456789"})
Upstream.MODE["kind"] = "mercury"
r = client.post("/api/chat", json={"text": "瀑布模型是什么"})
evs = sse_events(r.text)
txt = "".join(d.get("text", "") for e, d in evs if e == "delta")
check(txt == "瀑布模型要求前期冻结需求 [1]。", "test_mercury_stream_chunks：Mercury 分块拼接正确")
nid_ok = [d for e, d in evs if e == "done"][0]["nodeId"]
Upstream.MODE["kind"] = "401"
r = client.post("/api/chat", json={"nodeId": nid_ok, "text": "再问一次"})
evs = sse_events(r.text)
done = [d for e, d in evs if e == "done"][-1]
alltext = r.text
check(done.get("error") is True and ("401" in alltext or "Invalid API key" in alltext),
      "test_stream_surfaces_upstream_http_error：SSE 报出 401 真因")
msgs = client.get("/api/nodes/%s/messages" % nid_ok).json()["messages"]
asst = [m for m in msgs if m["role"] == "assistant" and m.get("status", "ok") != "error"]
check(not any("生成失败" in m["content"] for m in asst), "失败文本不作为正常 assistant 回复")
check(any(m.get("status") == "error" for m in msgs), "失败记录仍可见（status=error）")
Upstream.MODE["kind"] = "mercury"
Upstream.GOT.clear()
client.post("/api/chat", json={"nodeId": nid_ok, "text": "第三问"})
sent = [g["body"] for g in Upstream.GOT if "/chat/completions" in g["path"]]
check(sent and "生成失败" not in sent[-1] and "Invalid API key" not in sent[-1],
      "test_failed_reply_excluded_from_context：失败文本不进下一问上下文")
reset_settings()

section("02 P1-1 refNode 校验 + 前端转义")
pid1 = client.post("/api/projects", json={"name": "xss"}).json()["id"]
r = client.post("/api/chat", json={"text": "hello world", "projectId": pid1})
nx = [d for e, d in sse_events(r.text) if e == "done"][0]
mid = nx["messageId"]
payload = '"><img src=x onerror=alert(document.cookie)><b x="'
r = client.post("/api/overlays", json={"nodeId": nx["nodeId"], "messageId": mid, "kind": "mark",
                                       "start": 0, "end": 5, "color": "#ff0000", "refNode": payload})
check(r.status_code == 400, "test_refnode_rejects_markup：恶意 refNode 400")
r = client.post("/api/overlays", json={"nodeId": nx["nodeId"], "messageId": mid, "kind": "mark",
                                       "start": 0, "end": 5, "color": "#ff0000", "refNode": "nd_000000000000"})
check(r.status_code in (400, 404), "不存在的 refNode 拒绝")
child = client.post("/api/branch", json={"parentId": nx["nodeId"], "anchorText": "hello"}).json()["node"]["id"]
r = client.post("/api/overlays", json={"nodeId": nx["nodeId"], "messageId": mid, "kind": "mark",
                                       "start": 0, "end": 5, "color": "#ff0000", "refNode": child})
check(r.status_code == 200, "真实分支 id 作 refNode 通过")
INK = open(os.path.join(ROOT, "demo", "ink.html"), encoding="utf-8").read()
js = INK[INK.index("function md(s)"):INK.index("function plainOffset")]
ps = re.search(r"function parseStyle\(.*?\n", INK)
js += "\n" + (ps.group(0) if ps else "function parseStyle(v){try{return JSON.parse(v)}catch(e){return null}}\n")
js += "console.log(mdOv('hello world',[{start:0,end:5,kind:'mark',color:'#ff0000',ref_node:%s}],[]));" % json.dumps(payload)
import subprocess  # noqa: E402
out = subprocess.run(["node", "-e", js], capture_output=True, text=True).stdout
check(bool(out) and "<img" not in out and "&quot;" in out,
      "test_atomshtml_escapes_ref_attr：前端渲染不产生可执行标签")

section("02 P0-2 URL 入库 SSRF")


class Internal(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            try:
                for _ in range(60):
                    self.wfile.write(b"x" * (1 << 20))
            except Exception:
                pass
            return
        if self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:%d/secret" % IPORT)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write("<html><body><p>INTERNAL_ADMIN_PANEL db_password=hunter2</p></body></html>".encode())

    def log_message(self, *a):
        pass


_in, IPORT = serve(Internal)
_pub, PPORT = serve(Internal)   # 充当"公网站点"：显式放进白名单
pid2 = client.post("/api/projects", json={"name": "ssrf"}).json()["id"]
blocked = {
    "test_ssrf_blocks_loopback": "http://127.0.0.1:%d/secret" % IPORT,
    "test_ssrf_blocks_localhost_name": "http://localhost:%d/secret" % IPORT,
    "test_ssrf_blocks_hex_ip": "http://0x7f000001:%d/secret" % IPORT,
    "test_ssrf_blocks_decimal_ip": "http://2130706433:%d/secret" % IPORT,
    "test_ssrf_blocks_ipv6_loopback": "http://[::1]:%d/secret" % IPORT,
    "test_ssrf_blocks_metadata": "http://169.254.169.254/latest/meta-data/",
    "test_ssrf_blocks_private_10": "http://10.0.0.1/",
    "test_ssrf_blocks_zero": "http://0.0.0.0:%d/secret" % IPORT,
}
for name, u in blocked.items():
    t0 = time.time()
    r = client.post("/api/sources", data={"project_id": pid2, "url": u})
    check(r.status_code == 400 and time.time() - t0 < 3, "%s（%s → %d）" % (name, u.split('/')[2], r.status_code))
os.environ["CONVTREE_URL_ALLOWLIST"] = "127.0.0.1:%d" % PPORT
r = client.post("/api/sources", data={"project_id": pid2, "url": "http://127.0.0.1:%d/page" % PPORT})
check(r.status_code == 200, "白名单主机可抓（阳性对照）")
r = client.post("/api/sources", data={"project_id": pid2, "url": "http://127.0.0.1:%d/redir" % PPORT})
check(r.status_code == 400, "test_ssrf_blocks_redirect_to_private：重定向到内网逐跳拦截")
import tracemalloc  # noqa: E402
tracemalloc.start()
r = client.post("/api/sources", data={"project_id": pid2, "url": "http://127.0.0.1:%d/big" % PPORT})
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
check(r.status_code == 400 and peak < 25e6, "test_url_download_capped_streaming：60MB 响应边读边断（峰值 %.0fMB）" % (peak / 1e6))
os.environ.pop("CONVTREE_URL_ALLOWLIST")



# ════════════════════════════════════════════════════════════════════
section("03 空间：迁移（旧库 NULL 数据 → 默认空间，幂等）")
import sqlite3  # noqa: E402
OLD = os.path.join(TMP, "old.db")
_c = sqlite3.connect(OLD)
_c.executescript("""
CREATE TABLE node (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT NOT NULL, anchor_text TEXT, color TEXT,
  created_at INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, project_id TEXT);
CREATE TABLE message (id TEXT PRIMARY KEY, node_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE note (id TEXT PRIMARY KEY, text TEXT NOT NULL, from_node TEXT, from_name TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL);
CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at INTEGER NOT NULL);
INSERT INTO project VALUES ('prj_aaaaaaaaaaaa','旧笔记本',1);
INSERT INTO node VALUES ('nd_000000000001',NULL,'游离树',NULL,NULL,1,0,NULL);
INSERT INTO node VALUES ('nd_000000000002','nd_000000000001','子',NULL,NULL,2,0,NULL);
INSERT INTO node VALUES ('nd_000000000003',NULL,'挂本树',NULL,NULL,3,0,'prj_aaaaaaaaaaaa');
INSERT INTO message VALUES ('msg_1','nd_000000000001','user','老问题',1);
INSERT INTO note VALUES ('note_1','游离笔记',NULL,'',1);
INSERT INTO note VALUES ('note_2','挂本笔记','nd_000000000003','挂本树',2);
INSERT INTO note VALUES ('note_3','子节点笔记','nd_000000000002','子',3);
""")
_c.commit()
_c.close()
_saved_db = store.DB_PATH
store.DB_PATH = OLD
store.init_db()
store.init_db()   # 幂等：二次迁移不重复建默认空间
_c = sqlite3.connect(OLD)
_defaults = _c.execute("SELECT id FROM project WHERE name='默认空间'").fetchall()
check(len(_defaults) == 1, "test_migration_creates_one_default_space（幂等）")
_d = _defaults[0][0]
check(_c.execute("SELECT project_id FROM node WHERE id='nd_000000000001'").fetchone()[0] == _d, "游离根节点进默认空间")
check(_c.execute("SELECT project_id FROM node WHERE id='nd_000000000003'").fetchone()[0] == "prj_aaaaaaaaaaaa", "已挂空间的根不动")
_notes = dict(_c.execute("SELECT id, project_id FROM note").fetchall())
check(_notes == {"note_1": _d, "note_2": "prj_aaaaaaaaaaaa", "note_3": _d}, "test_migration_scopes_notes：笔记按来源树归空间，无来源进默认")
check(_c.execute("SELECT content FROM message WHERE id='msg_1'").fetchone()[0] == "老问题", "迁移不动消息原文")
_c.close()
store.DB_PATH = _saved_db

section("03 空间：CRUD + 钻研目标 + 真计数")
r = client.post("/api/spaces", json={"name": "软件工程期末", "goal": "弄懂瀑布与敏捷的取舍"})
check(r.status_code == 200 and r.json().get("goal") == "弄懂瀑布与敏捷的取舍", "test_create_space_with_goal")
SP = r.json()["id"]
check(client.post("/api/spaces", json={"name": "  "}).status_code == 400, "空名 400")
r = client.put("/api/spaces/" + SP, json={"goal": "弄懂瀑布、敏捷与螺旋模型"})
check(r.status_code == 200 and r.json()["goal"] == "弄懂瀑布、敏捷与螺旋模型" and r.json()["name"] == "软件工程期末", "改目标不动名称")
r = client.put("/api/spaces/" + SP, json={"name": "软工"})
check(r.json()["name"] == "软工" and r.json()["goal"] == "弄懂瀑布、敏捷与螺旋模型", "改名不动目标")
check(client.put("/api/spaces/prj_nope", json={"name": "x"}).status_code == 404, "改不存在空间 404")
client.post("/api/sources", data={"project_id": SP, "text": "瀑布模型要求前期冻结需求。", "name": "a"})
client.post("/api/sources", data={"project_id": SP, "text": "敏捷开发以迭代交付。", "name": "b"})
r = client.post("/api/chat", json={"text": "树一", "spaceId": SP})
t1 = [d for e, d in sse_events(r.text) if e == "done"][0]["nodeId"]
client.post("/api/chat", json={"text": "树二", "projectId": SP})
client.post("/api/branch", json={"parentId": t1, "anchorText": "树一"})   # 分支不算新树
client.post("/api/notes", json={"text": "空间笔记", "fromNode": t1})
sp = [x for x in client.get("/api/spaces").json()["spaces"] if x["id"] == SP][0]
check(sp["trees"] == 2 and sp["sources"] == 2 and sp["notes"] == 1, "test_space_overview_real_counts（树2 资料2 笔记1）")
check(any(x["name"] == "默认空间" for x in client.get("/api/spaces").json()["spaces"]), "默认空间存在")
check(any(x["id"] == SP and x.get("goal") for x in client.get("/api/projects").json()["projects"]), "旧 /api/projects 别名可用且带 goal")

section("03 空间：对话树与笔记按空间隔离")
SP2 = client.post("/api/spaces", json={"name": "线代"}).json()["id"]
client.post("/api/chat", json={"text": "线代树", "spaceId": SP2})
client.post("/api/notes", json={"text": "线代笔记", "spaceId": SP2})
roots1 = [n for n in client.get("/api/nodes?space_id=" + SP).json()["nodes"] if not n["parent_id"]]
check(len(roots1) == 2 and all(n["project_id"] == SP for n in roots1), "test_nodes_filtered_by_space")
check(len(client.get("/api/nodes?space_id=" + SP).json()["nodes"]) == 3, "按空间取节点含分支")
n1 = [x["text"] for x in client.get("/api/notes?space_id=" + SP).json()["notes"]]
n2 = [x["text"] for x in client.get("/api/notes?space_id=" + SP2).json()["notes"]]
check(n1 == ["空间笔记"] and n2 == ["线代笔记"], "test_notes_scoped_per_space")
r = client.post("/api/chat", json={"text": "无空间新对话"})
nd = [d for e, d in sse_events(r.text) if e == "done"][0]["nodeId"]
dflt = [x for x in client.get("/api/spaces").json()["spaces"] if x["name"] == "默认空间"][0]["id"]
check(any(n["id"] == nd for n in client.get("/api/nodes?space_id=" + dflt).json()["nodes"]), "不指定空间 ⇒ 落默认空间")

section("03 空间：钻研目标进系统提示")
client.post("/api/settings", json={"provider": "custom", "baseUrl": UPBASE, "apiKey": "sk-x-123456789"})
Upstream.GOT.clear()
client.post("/api/chat", json={"nodeId": t1, "text": "瀑布模型的风险？"})
body = json.loads([g["body"] for g in Upstream.GOT if "/chat/completions" in g["path"]][-1])
sys_txt = "\n".join(m["content"] for m in body["messages"] if m["role"] == "system")
check("弄懂瀑布、敏捷与螺旋模型" in sys_txt, "test_space_goal_in_system_prompt")
br = client.post("/api/branch", json={"parentId": t1, "anchorText": "风险"}).json()["node"]["id"]
Upstream.GOT.clear()
client.post("/api/chat", json={"nodeId": br, "text": "展开"})
body = json.loads([g["body"] for g in Upstream.GOT if "/chat/completions" in g["path"]][-1])
check(any("弄懂瀑布、敏捷与螺旋模型" in m["content"] for m in body["messages"] if m["role"] == "system"), "分支也继承钻研目标")
Upstream.GOT.clear()
client.post("/api/chat", json={"text": "线代问题", "spaceId": SP2})
body = json.loads([g["body"] for g in Upstream.GOT if "/chat/completions" in g["path"]][-1])
check(not any("钻研目标" in m["content"] for m in body["messages"] if m["role"] == "system"), "无目标的空间不注入")
reset_settings()

section("03 空间：删除=软删，不孤立数据，可恢复")
r = client.delete("/api/spaces/" + SP)
check(r.status_code == 200, "删空间 200")
check(SP not in [x["id"] for x in client.get("/api/spaces").json()["spaces"]], "删后不在列表")
check(client.get("/api/nodes/%s/messages" % t1).status_code == 200, "删后树仍可读")
r = client.post("/api/spaces/%s/restore" % SP)
sp = [x for x in client.get("/api/spaces").json()["spaces"] if x["id"] == SP]
check(r.status_code == 200 and sp and sp[0]["trees"] == 2 and sp[0]["sources"] == 2 and sp[0]["notes"] == 1,
      "test_delete_space_is_soft_and_restorable（恢复后计数不变）")
check(client.delete("/api/spaces/" + dflt).status_code == 400, "默认空间不可删")
check(client.delete("/api/projects/prj_nope").status_code == 404, "旧别名删不存在 404")


# ════════════════════════════════════════════════════════════════════
section("04 分支上下文预算（审计 P1-3）")
os.environ["CONTEXT_BUDGET_CHARS"] = "24000"
SPB = client.post("/api/spaces", json={"name": "深挖预算"}).json()["id"]
r = client.post("/api/chat", json={"text": "根问题：什么是软件过程模型？", "spaceId": SPB})
root_b = [d for e, d in sse_events(r.text) if e == "done"][0]["nodeId"]
cur_b, anchors = root_b, []
# 夹具：直接写库造 3 层 × 10 轮 × ~2k 字（观察仍走 HTTP：看上游实际收到的请求体）
for depth in range(3):
    for i in range(10):
        store.add_message(cur_b, "user", ("第%d层第%d问" % (depth, i)) + "问" * 50)
        store.add_message(cur_b, "assistant", ("第%d层第%d答" % (depth, i)) + "答" * 2000)
    anchor = "锚文本ANCHOR_%d_深层" % depth
    anchors.append(anchor)
    cur_b = client.post("/api/branch", json={"parentId": cur_b, "anchorText": anchor}).json()["node"]["id"]
client.post("/api/settings", json={"provider": "custom", "baseUrl": UPBASE, "apiKey": "sk-x-123456789"})
Upstream.GOT.clear()
t0 = time.time()
client.post("/api/chat", json={"nodeId": cur_b, "text": "第4层的新问题：为什么要冻结需求？"})
sent = json.loads([g["body"] for g in Upstream.GOT if "/chat/completions" in g["path"]][-1])["messages"]
total = sum(len(m["content"]) for m in sent)
flat = "\n".join(m["content"] for m in sent)
check(total <= 24000 + 3000, "test_deep_branch_context_under_budget（深度4：%d 字，预算 24000，未压缩约 61800）" % total)
check(anchors[-1] in flat, "test_context_keeps_current_anchor_verbatim：当前分支锚文本原文在")
check("第4层的新问题：为什么要冻结需求？" in flat, "当前问题原文在")
check(("第2层第9答" + "答" * 2000) in flat, "最近祖先在预算内保持原文")
check("祖先摘要" in flat and "第0层第0问" in flat, "远祖先被摘要替换（摘要保留根问题开头）")
check(("第0层第5答" + "答" * 2000) not in flat, "远祖先原文不再整段进入")
check(time.time() - t0 < 5, "确定性摘要不额外调模型（%.2fs）" % (time.time() - t0))
calls_before = len([g for g in Upstream.GOT if "/chat/completions" in g["path"]])
check(calls_before == 1, "mock/截断摘要：一次提问只调一次上游")
root_msgs = client.get("/api/nodes/%s/messages" % root_b).json()["messages"]
check(any(m["content"] == "第0层第0答" + "答" * 2000 for m in root_msgs), "D-101：摘要不改原消息")
Upstream.GOT.clear()
client.post("/api/chat", json={"nodeId": cur_b, "text": "再追问一次"})
sent2 = json.loads([g["body"] for g in Upstream.GOT if "/chat/completions" in g["path"]][-1])["messages"]
check(sum(len(m["content"]) for m in sent2) <= 24000 + 3000 and anchors[-1] in "\n".join(m["content"] for m in sent2),
      "第二问仍在预算内（摘要缓存复用）")
# 浅分支不受影响：预算内一字不压
r = client.post("/api/chat", json={"text": "浅根", "spaceId": SPB})
sroot = [d for e, d in sse_events(r.text) if e == "done"][0]["nodeId"]
sb = client.post("/api/branch", json={"parentId": sroot, "anchorText": "浅根"}).json()["node"]["id"]
Upstream.GOT.clear()
client.post("/api/chat", json={"nodeId": sb, "text": "浅问"})
sent3 = json.loads([g["body"] for g in Upstream.GOT if "/chat/completions" in g["path"]][-1])["messages"]
check(not any("祖先摘要" in m["content"] for m in sent3) and any(m["content"] == "浅根" for m in sent3),
      "浅分支预算内保持全文、不出摘要")
os.environ.pop("CONTEXT_BUDGET_CHARS")
reset_settings()


# ════════════════════════════════════════════════════════════════════
section("05 重活不阻塞事件循环（审计 P1-2，真 uvicorn）")
import socket  # noqa: E402
import subprocess  # noqa: E402,F811
import httpx  # noqa: E402
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
_port = _s.getsockname()[1]
_s.close()
_env = dict(os.environ, APP_DB=os.path.join(TMP, "uv.db"), CONVTREE_CONFIG=os.path.join(TMP, "uv.json"))
_proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app", "--port", str(_port), "--log-level", "warning"],
                         cwd=ROOT, env=_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    _cli = httpx.Client(base_url="http://127.0.0.1:%d" % _port, timeout=60, trust_env=False)
    for _ in range(50):
        try:
            if _cli.get("/api/health").status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    _pid = _cli.post("/api/spaces", json={"name": "大文件"}).json()["id"]
    big = ("瀑布模型要求在前期冻结需求，阶段评审通过后才进入下一阶段。敏捷开发以迭代交付。\n" * 125000).encode("utf-8")
    lat, done = [], {}

    def _upload():
        t = time.time()
        r = _cli.post("/api/sources", data={"project_id": _pid}, files={"file": ("big.txt", big, "text/plain")})
        done["dt"], done["code"] = time.time() - t, r.status_code

    th = threading.Thread(target=_upload)
    th.start()
    time.sleep(0.3)
    while th.is_alive():
        t = time.time()
        _cli.get("/api/health")
        lat.append(time.time() - t)
        time.sleep(0.05)
    th.join()
    mx = max(lat) if lat else 0
    check(done.get("code") == 200 and done.get("dt", 0) > 0.8,
          "大文件入库成功且确实耗时（%.1fMB，%.2fs）" % (len(big) / 1e6, done.get("dt", 0)))
    check(len(lat) >= 3 and mx < 0.5, "test_ingest_does_not_block_health：入库期间 /api/health 最大延迟 %.2fs（%d 次）" % (mx, len(lat)))
finally:
    _proc.terminate()
    _proc.wait(timeout=10)


# ════════════════════════════════════════════════════════════════════
print("\n共 %d 例，失败 %d" % (N[0], len(FAILS)))
for f in FAILS:
    print("  FAIL", f)
sys.exit(1 if FAILS else 0)
