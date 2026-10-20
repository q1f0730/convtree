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
print("\n共 %d 例，失败 %d" % (N[0], len(FAILS)))
for f in FAILS:
    print("  FAIL", f)
sys.exit(1 if FAILS else 0)
