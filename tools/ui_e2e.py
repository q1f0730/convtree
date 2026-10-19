#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真浏览器端到端验收（前端行为 + 截图）。隔离库/隔离配置、mock 模型，不碰 data/app.db。

依赖：pip install playwright；浏览器用系统 chromium（或 CHROMIUM=路径），找不到就退出 3（=跳过，不算失败）。
用法（项目根目录）：
    python tools/ui_e2e.py                 # 只验收
    python tools/ui_e2e.py --shots DIR     # 同时把关键界面截图存到 DIR（1440x900）
退出码：0 全过；2 有失败；3 无浏览器（跳过）。
"""
from __future__ import print_function

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N = [0, 0]


def check(ok, name):
    N[0] += 1
    if not ok:
        N[1] += 1
    print(("OK   " if ok else "FAIL ") + str(N[0]) + " " + name)
    return ok


def find_chromium():
    for c in [os.environ.get("CHROMIUM"), shutil.which("chromium"), shutil.which("chromium-browser"),
              shutil.which("google-chrome"), shutil.which("chrome")]:
        if c and os.path.exists(c):
            return c
    return None


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_server(tmp, port):
    env = dict(os.environ, APP_DB=os.path.join(tmp, "app.db"), CONVTREE_CONFIG=os.path.join(tmp, "config.json"),
               CONVTREE_NO_AUTOBACKUP="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        env.pop(k, None)
    log = open(os.path.join(tmp, "server.log"), "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(80):
        try:
            if opener.open("http://127.0.0.1:%d/api/health" % port, timeout=1).status == 200:
                return proc
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise RuntimeError("server did not start; see " + log.name)


def rect_overlap(a, b):
    return a["x"] < b["right"] and a["x"] + a["width"] > b["left"] and a["y"] < b["bottom"] and a["y"] + a["height"] > b["top"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots")
    args = ap.parse_args()
    exe = find_chromium()
    if not exe:
        print("SKIP 无 chromium 可用")
        return 3
    from playwright.sync_api import sync_playwright

    tmp = tempfile.mkdtemp(prefix="ct_e2e_")
    port = free_port()
    proc = start_server(tmp, port)
    shots = args.shots
    if shots:
        os.makedirs(shots, exist_ok=True)
    md = os.path.join(tmp, "瀑布与敏捷.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# 瀑布模型与敏捷开发\n\n瀑布模型要求在前期冻结需求，阶段评审通过后才进入下一阶段，"
                "风险往往集中在后期测试阶段暴露。\n\n敏捷开发以 2 到 4 周为一个迭代持续交付可用增量，"
                "通过频繁反馈把风险分摊到每个迭代。\n")
    errors = []
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=exe, args=["--no-sandbox", "--no-proxy-server"])
            pg = br.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            pg.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
            pg.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text)) if m.type == "error" else None)
            pg.on("response", lambda r: errors.append("http %d %s" % (r.status, r.url)) if r.status >= 400 else None)
            pg.set_default_timeout(6000)
            base = "http://127.0.0.1:%d/" % port

            def shot(name):
                if shots:
                    pg.wait_for_timeout(450)
                    pg.screenshot(path=os.path.join(shots, name))

            pg.goto(base)
            pg.wait_for_timeout(900)
            check(pg.title().startswith("深挖"), "标题仍是 深挖")
            shot("01-empty.png")

            # Ctrl K：非 Mac 显示 Ctrl K，按下聚焦输入框
            check(pg.locator("#kbdHint").inner_text().strip() == "Ctrl K", "非 Mac 平台提示 Ctrl K")
            pg.locator("body").click(position={"x": 700, "y": 880})
            pg.keyboard.press("Control+k")
            check(pg.evaluate("document.activeElement&&document.activeElement.id") == "q", "Ctrl K 聚焦提问框")

            # 笔记本 + 上传 .md
            pg.click("#projNew")
            pg.fill("#projName", "软件工程期末")
            pg.press("#projName", "Enter")
            pg.wait_for_selector("#srcPanel", state="visible")
            pg.set_input_files("#srcFile", md)
            pg.click("#srcUpload")
            pg.wait_for_selector(".srcitem", timeout=8000)
            check(pg.locator(".srcitem").count() == 1, "上传 .md 后资料列表出现一条")
            check(pg.locator("#srcCount").inner_text().strip() == "1", "资料计数=1")
            shot("05-sources.png")
            pg.click("#srcClose")

            # 有据回答 + 引用气泡
            pg.fill("#q", "瀑布模型和敏捷开发最本质的区别是什么？")
            pg.click("#send")
            pg.wait_for_selector(".msg-a .cite", timeout=15000)
            pg.wait_for_function("!document.querySelector('.caret')", timeout=15000)
            pg.wait_for_timeout(600)
            pg.locator(".msg-a .cite").first.click()
            check(pg.locator("#citePop").is_visible(), "点 [1] 弹出引用气泡")
            check("瀑布" in pg.locator("#citePop .cp-tx").inner_text(), "气泡显示资料原文段")
            shot("02-chat-cite.png")
            pg.keyboard.press("Escape")

            # 划词菜单：不盖住选区
            def select_in_answer(start, length):
                return pg.evaluate("""([s,l])=>{const a=[...document.querySelectorAll('.msg-a')].pop();
                  const w=document.createTreeWalker(a,NodeFilter.SHOW_TEXT);let n,acc=0,sn=null,so=0,en=null,eo=0;
                  while((n=w.nextNode())){const L=n.data.length;
                    if(!sn&&acc+L>s){sn=n;so=s-acc}
                    if(sn&&acc+L>=s+l){en=n;eo=s+l-acc;break}acc+=L}
                  const r=document.createRange();r.setStart(sn,so);r.setEnd(en,eo);
                  const sel=getSelection();sel.removeAllRanges();sel.addRange(r);
                  const rs=[...r.getClientRects()].map(x=>({left:x.left,top:x.top,right:x.right,bottom:x.bottom}));
                  const b=rs[rs.length-1];
                  a.dispatchEvent(new MouseEvent('mouseup',{bubbles:true,clientX:b.right,clientY:b.bottom}));
                  return {rects:rs,text:sel.toString()}}""", [start, length])

            sel = select_in_answer(14, 10)
            pg.wait_for_timeout(200)
            m = pg.locator("#menu").bounding_box()
            check(pg.locator("#menu").is_visible(), "选中文字弹出划词菜单")
            check(m is not None and not any(rect_overlap(m, r) for r in sel["rects"]), "划词菜单不盖住选中文字")
            check(m is not None and m["y"] + m["height"] <= sel["rects"][0]["top"], "划词菜单默认在选区上方")
            shot("03-selection.png")

            # 分支 ×2 → 树 ≥3 节点
            pg.click('#menu button[data-a="branch"]')
            pg.wait_for_timeout(700)
            pg.fill("#q", "展开说说阶段评审")
            pg.click("#send")
            pg.wait_for_function("!document.querySelector('.caret')&&!document.querySelector('.is-thinking')", timeout=15000)
            pg.wait_for_timeout(500)
            pg.click("#backBtn")
            pg.wait_for_timeout(700)
            select_in_answer(30, 8)
            pg.wait_for_timeout(200)
            pg.click('#menu button[data-a="branch"]')
            pg.wait_for_timeout(700)
            pg.click("#backBtn")
            pg.wait_for_timeout(900)
            n_nodes = pg.locator("#treeGraph .tn").count()
            check(n_nodes >= 3, "导图里有 ≥3 个节点（实际 %d）" % n_nodes)
            gap = pg.evaluate("""()=>{const out=[];const svg=document.getElementById('treeGraph');
              const lines=[...svg.querySelectorAll('path.edge')].map(p=>p.getAttribute('d'));
              const pts=[];lines.forEach(d=>{const m=d.match(/M\\s*(-?[\\d.]+)\\s+(-?[\\d.]+)\\s*L\\s*(-?[\\d.]+)\\s+(-?[\\d.]+)/);if(m)pts.push(m.slice(1).map(Number))});
              const on=(x,y,s)=>(s[0]===s[2]&&Math.abs(x-s[0])<.6&&y>=Math.min(s[1],s[3])-.6&&y<=Math.max(s[1],s[3])+.6)||(s[1]===s[3]&&Math.abs(y-s[1])<.6&&x>=Math.min(s[0],s[2])-.6&&x<=Math.max(s[0],s[2])+.6);
              svg.querySelectorAll('.tn').forEach(g=>{const r=g.querySelector('rect.card');const x=+r.getAttribute('x')+(+r.getAttribute('width'))/2,yb=+r.getAttribute('y')+(+r.getAttribute('height'));
                if(g.dataset.child==='1'&&!pts.some(s=>on(x,yb,s)))out.push(g.dataset.nid)});
              return {segs:pts.length,dashed:[...svg.querySelectorAll('path.edge')].some(p=>p.getAttribute('stroke-dasharray')),unconnected:out}}""")
            check(gap["segs"] > 0 and not gap["unconnected"], "每个子卡底边都接上连线（%s）" % gap)
            check(not gap["dashed"], "连线为实线")
            shot("04-tree.png")
            pg.click('#treeTog button[data-v="list"]')
            check(pg.locator("#treeList .tnode").count() == n_nodes, "大纲视图与导图节点数一致")
            shot("07-outline.png")
            select_in_answer(4, 12)
            pg.wait_for_timeout(200)
            pg.click('#menu button[data-a="note"]')
            pg.wait_for_timeout(500)
            pg.click('#side button[data-m="note"]')
            pg.wait_for_selector("#notes .ncard", timeout=5000)
            check(pg.locator("#notes .ncard").count() >= 1, "划词记笔记后笔记页出现卡片")
            shot("08-notes.png")
            pg.click('#side button[data-m="tree"]')
            pg.wait_for_timeout(400)
            pg.click('#treeTog button[data-v="graph"]')

            # 右键树卡 → 删除菜单；划词 → 格式面板（只看能开，不真删）
            pg.locator("#treeGraph .tn").first.click(button="right")
            check(pg.locator("#treemenu").is_visible(), "树卡右键出删除菜单")
            shot("09-treemenu.png")
            pg.keyboard.press("Escape")
            pg.mouse.click(700, 600)
            fsel = select_in_answer(6, 8)
            pg.wait_for_timeout(200)
            pg.click('#menu button[data-a="fmt"]')
            check(pg.locator("#fmtmenu").is_visible(), "格式面板可打开")
            fm = pg.locator("#fmtmenu").bounding_box()
            check(fm is not None and not any(rect_overlap(fm, r) for r in fsel["rects"]), "格式面板不盖住选中文字")
            shot("10-format.png")
            pg.mouse.click(700, 600)

            # 设置：嵌入配置保存后重载仍在
            pg.click("#setBtn")
            check(pg.locator("#sEmbModel").is_visible(), "设置里有嵌入模型字段")
            pg.fill("#sEmbBase", "https://embed.example/v1")
            pg.fill("#sEmbModel", "text-embedding-v3")
            pg.fill("#sEmbKey", "sk-embedtest123456")
            pg.click("#sSave")
            pg.wait_for_timeout(600)
            pg.reload()
            pg.wait_for_timeout(900)
            pg.click("#setBtn")
            check(pg.locator("#sEmbModel").input_value() == "text-embedding-v3", "嵌入模型重载后回填")
            check(pg.locator("#sEmbBase").input_value() == "https://embed.example/v1", "嵌入地址重载后回填")
            check(pg.locator("#sEmbKey").input_value() == "", "嵌入密钥不回填明文")
            check("sk-em" in (pg.locator("#sEmbKey").get_attribute("placeholder") or ""), "嵌入密钥只显示掩码")
            shot("06-settings.png")
            pg.keyboard.press("Escape")

            # 横向溢出 / JS 报错
            check(pg.evaluate("document.documentElement.scrollWidth<=innerWidth"), "1440 下无横向溢出")
            pg.set_viewport_size({"width": 1280, "height": 800})
            pg.wait_for_timeout(400)
            check(pg.evaluate("document.documentElement.scrollWidth<=innerWidth"), "1280 下无横向溢出")
            onb = pg.locator("#treeGraph .tn.on rect.card").bounding_box()
            scb = pg.locator("#treeScroll").bounding_box()
            check(onb is not None and onb["y"] + onb["height"] <= scb["y"] + scb["height"] + 1, "窗口缩小后导图重排，当前卡仍完整可见")
            shot("11-1280.png")
            br.close()
    finally:
        proc.terminate()
    bad = [e for e in errors if not e.startswith("console.error: Failed to load resource")]
    check(not bad, "控制台无 JS 报错" + ("" if not bad else "：" + " | ".join(bad[:5])))
    print("\n共 %d 例，失败 %d" % (N[0], N[1]))
    return 2 if N[1] else 0


if __name__ == "__main__":
    sys.exit(main())
