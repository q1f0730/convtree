#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态不变量扫描器：把宪法里**能机械判定**的条目变成可执行检查。

依据 C-005：不变量必须写成否定式断言，且必须被实际触发过才算数。
因此本脚本自带两类对照，缺一不可：

- **阳性对照**（tools/fixtures/violations/）：扫描器必须先命中已知的坏样本，
  否则「全仓零命中」可能只是正则写错了。
- **反向对照**（tools/fixtures/clean/）：含有该词但属散文/字符串说明的样本
  不得被误命中，否则扫描器会把文档当代码。

历史教训（E-0020）：旧扫描器只走 `server/*.py`，正则要求表名后**紧跟** SET
（带表别名的 UPDATE 因此漏检），且不覆盖 REPLACE 形式的 message 写入。
本脚本按「表名可选别名 + 任意空白」匹配，并把 REPLACE 形式一并覆盖。

覆盖范围：`.py` / `.js` / `.html`（`.html` 是后加的 —— 唯一前端的全部 JS 都在 app.html 里，
只扫 .py/.js 等于把前端整块留成盲区，隔离审查者实测抓出）。

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\check_invariants.py --all
    .venv\\Scripts\\python.exe tools\\check_invariants.py --fixtures
    .venv\\Scripts\\python.exe tools\\check_invariants.py --all --json tools\\_out_check.json

退出码：0 = 全部通过；2 = 有违规或对照失败。
"""
from __future__ import print_function

import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = os.path.abspath(__file__)
FIXTURE_VIOLATIONS = os.path.join(ROOT, "tools", "fixtures", "violations")
FIXTURE_CLEAN = os.path.join(ROOT, "tools", "fixtures", "clean")

# 扫描时跳过的目录：虚拟环境、备份、数据库目录、缓存、以及对照样本本身
SKIP_DIRS = (".venv", "backups", "data", "__pycache__", "node_modules", ".git", ".workbuddy")
SCAN_EXTS = (".py", ".js", ".html", ".htm")

# 表名在源码里拆开拼接，使**规则的正则本身**不含被禁的表名字面量。
# 注意别把这条理解成「本文件不含这些词」—— 本文件的说明文字里当然会写到它们，
# 所以真实扫描时另按路径排除自身（见 iter_source_files 里的 SELF 判断）。
# 两道保险各管一段：拆拼接让规则不误伤自己，路径排除兜住文档文字。
_M = "mes" + "sage"
_OV = "over" + "lay"

# 表名两侧容许引号/中括号，表名后可跟别名（AS x 或 x），再容忍任意空白后遇 SET。
_UPDATE_ON = lambda t: re.compile(
    r"UPDATE\s+[\"'`\[]?" + t + r"s?[\"'`\]]?(?:\s+(?:AS\s+)?[A-Za-z_]\w*)?\s+SET",
    re.I,
)
_REPLACE_INTO = lambda t: re.compile(
    r"INSERT\s+OR\s+REPLACE\s+INTO\s+[\"'`\[]?" + t + r"s?[\"'`\]]?",
    re.I,
)
# DELETE 缺少 SET 那样的天然分隔符，必须加一个「SQL 式续写」的前瞻：
# 真正的语句后面接 WHERE / ; / 引号 / 括号 或行尾；散文（「没有 DELETE FROM overlay 的必要」）
# 后面接的是中文，因此不会误命中。反向对照样本正为此而设。
_DELETE_FROM = lambda t: re.compile(
    r"DELETE\s+FROM\s+[\"'`\[]?" + t + r"s?[\"'`\]]?(?=\s*(?:WHERE\b|ORDER\b|LIMIT\b|;|[\"'`)\]]|$))",
    re.I,
)

# ── 规则表 ───────────────────────────────────────────────────
# 每条规则 = 一条宪法的**判据**的可执行化。id 与 constitution/*.md 一一对应。
RULES = [
# B 重立后只剩 D-101 一条打样；新不变量落地时按此格式追加规则 + 阳性样本。
    {
        "id": "D-101",
        "title": "消息原文逐字符不可变",
        "why": "存在更新 message.content 的语句",
        "patterns": [
            (_UPDATE_ON(_M), "UPDATE ... message ... SET（含别名写法）"),
            (_REPLACE_INTO(_M), "INSERT OR REPLACE INTO message（= 删除+插入，等于改写原文）"),
            (_DELETE_FROM(_M), "DELETE FROM message（删行等于销毁原文；删整支只许软删节点）"),
        ],
    },
]


def iter_source_files(root):
    """列出待扫描的源码文件（跳过虚拟环境、备份与对照样本）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        # 对照样本目录不参与真实扫描：它们**故意**含违规，只用于自测
        if os.path.abspath(dirpath) in (os.path.abspath(FIXTURE_VIOLATIONS),
                                        os.path.abspath(FIXTURE_CLEAN)):
            continue
        for fn in sorted(filenames):
            if not fn.endswith(SCAN_EXTS):
                continue
            full = os.path.abspath(os.path.join(dirpath, fn))
            if full == SELF:
                continue          # 扫描器自身（表名已拆分，这里再兜一层）
            out.append(full)
    return out


# ── 行级豁免 ─────────────────────────────────────────────────
# 刻意**不做**文件名白名单（旧脚本按 accept_*.py 整类排除，等于一份不可见的豁免表）。
# 改为行级标记：例外必须写在与语句同一行（或紧邻上一行），且被扫描器逐条打印出来。
# 这样「扫描覆盖全仓」是真的，而任何豁免都留下可见痕迹；豁免表变长即是异味信号。
_WAIVER = re.compile(r"noqa:\s*([A-Za-z0-9\-,\s]+)", re.I)


def _blank_python_comments(lines):
    """把 Python 注释替换为等长空白，行号与列位不变。

    为什么必须做：注释里引用一句完整的 SQL（解释历史缺陷时很自然）与代码里的
    真实语句，在正则层面完全一样。实测在 accept_step1.py 的注释上误报了两处。
    用 tokenize 真解析而非正则剥 `#`，因此字符串里的 `#` 不会被误剥。
    解析失败时退回原文 —— 宁可多报，不可漏报。
    """
    import tokenize
    out = [ln.rstrip("\r\n") for ln in lines]
    try:
        reader = io.StringIO("".join(lines)).readline
        for tok in tokenize.generate_tokens(reader):
            if tok.type != tokenize.COMMENT:
                continue
            (sr, sc), (er, ec) = tok.start, tok.end
            if sr == er and 1 <= sr <= len(out):
                line = out[sr - 1]
                if ec <= len(line):
                    out[sr - 1] = line[:sc] + " " * (ec - sc) + line[ec:]
    except Exception:  # noqa: BLE001 语法不完整时退回原文
        return [ln.rstrip("\r\n") for ln in lines]
    return out


def _waivers_in(line):
    m = _WAIVER.search(line)
    if not m:
        return set()
    return set(x.strip().upper() for x in m.group(1).split(",") if x.strip())


def scan_file(path):
    """返回 (该文件命中的违规, 该文件被豁免的条目)。"""
    hits, waived = [], []
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.readlines()
    except IOError:
        return hits, waived
    # 注释不参与匹配（但它仍参与豁免识别：豁免标记本身就写在注释里）
    code = _blank_python_comments(raw) if path.endswith(".py") else \
        [ln.rstrip("\r\n") for ln in raw]
    allowed_prev = set()
    for lineno, cline in enumerate(code, 1):
        rawline = raw[lineno - 1] if lineno <= len(raw) else ""
        allowed = allowed_prev | _waivers_in(rawline)
        allowed_prev = _waivers_in(rawline)   # 仅允许写到「紧邻上一行」，不跨多行扩散
        for rule in RULES:
            for pat, label in rule["patterns"]:
                if not pat.search(cline):
                    continue
                if rule["id"].upper() in allowed or "ALL" in allowed:
                    waived.append({"rule": rule["id"], "line": lineno,
                                   "file": os.path.relpath(path, ROOT),
                                   "text": rawline.strip()[:120]})
                else:
                    hits.append({"rule": rule["id"], "why": rule["why"],
                                 "label": label, "line": lineno,
                                 "text": rawline.strip()[:160]})
    return hits, waived


def scan_paths(paths):
    """扫描给定的文件列表，返回 (hits, waivers, scanned_count)。"""
    hits, waived, n = [], [], 0
    for p in paths:
        n += 1
        h, w = scan_file(p)
        for x in h:
            x["file"] = os.path.relpath(p, ROOT)
            hits.append(x)
        waived.extend(w)
    return hits, waived, n


# ── 对照自测 ─────────────────────────────────────────────────

def control_check():
    """阳性 + 反向对照。任一失败则整个扫描结论不可信。"""
    fails = []

    # 阳性：坏样本必须被扫出
    bad_files = [os.path.join(FIXTURE_VIOLATIONS, f)
                 for f in sorted(os.listdir(FIXTURE_VIOLATIONS)) if f.endswith(".py")] \
        if os.path.isdir(FIXTURE_VIOLATIONS) else []
    if not bad_files:
        print("FAIL 阳性对照缺样本：tools/fixtures/violations/ 下没有 .py")
        fails.append("阳性对照缺样本")
        hits = []
    else:
        hits, _, _ = scan_paths(bad_files)

        # 逐条核对**精确到行的期望声明**：fixture 里每个违规行都写了 `# expect: <规则ID>`。
        # 为什么不能只断言「每条规则至少命中一次」：把某条正则**改坏一处**
        # （例如删掉表别名那段，正是 E-0020 的漏检形态）时，同规则的另一种写法仍会命中，
        # 于是旧版对照照样报 OK —— 隔离审查者实测抓出，形同虚设。
        declared = []
        for p in bad_files:
            try:
                with io.open(p, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, ln in enumerate(f, 1):
                        for mm in re.finditer(r"#\s*expect:\s*([A-Za-z0-9\-]+)", ln):
                            declared.append((os.path.relpath(p, ROOT), lineno,
                                             mm.group(1).upper()))
            except IOError:
                pass
        hit_keys = set((h["file"], h["line"], h["rule"].upper()) for h in hits)

        if not declared:
            print("FAIL 阳性对照样本里没有任何 `# expect:` 声明 —— 对照退化成「有没有命中」")
            fails.append("阳性对照缺期望声明")
        else:
            unmet = [d for d in declared if d not in hit_keys]
            if unmet:
                print("FAIL 阳性对照有 %d 条声明未被命中（正则可能已被改坏而无人察觉）："
                      % len(unmet))
                for f_, l_, r_ in unmet:
                    print("       %s L%d 期望 %s" % (f_, l_, r_))
                fails.append("阳性对照未命中 %d 条声明" % len(unmet))
            else:
                ids = sorted(set(r_ for _, _, r_ in declared))
                print("OK   阳性对照命中全部 %d 条声明（%d 条规则）  :: %s"
                      % (len(declared), len(ids), ",".join(ids)))

    # 反向：散文/说明不得被误判
    good_files = [os.path.join(FIXTURE_CLEAN, f)
                  for f in sorted(os.listdir(FIXTURE_CLEAN)) if f.endswith(".py")] \
        if os.path.isdir(FIXTURE_CLEAN) else []
    if not good_files:
        print("FAIL 反向对照缺样本：tools/fixtures/clean/ 下没有 .py")
        fails.append("反向对照缺样本")
    else:
        hits, _, _ = scan_paths(good_files)
        if hits:
            for h in hits:
                print("FAIL 反向对照误命中 %s L%d：%s"
                      % (h["file"], h["line"], h["text"]))
            fails.append("反向对照误命中")
        else:
            print("OK   反向对照未误命中（不把散文当 SQL）")

    # 阳性对照必须与扫描范围非空同时成立，否则「零命中」可能来自空扫描
    if bad_files and len(bad_files) < 1:
        fails.append("扫描范围为空")
    return fails


def main():
    ap = argparse.ArgumentParser(description="静态不变量扫描器")
    ap.add_argument("--all", action="store_true", help="扫描全仓（含对照自测）")
    ap.add_argument("--fixtures", action="store_true", help="只跑对照自测")
    ap.add_argument("--json", dest="json_out", help="把报告写到该路径")
    args = ap.parse_args()

    if not args.all and not args.fixtures:
        ap.print_help()
        return 1

    print("=== 对照自测（阳性 / 反向）===")
    fails = control_check()

    report = {"controls_failed": fails, "rules": [r["id"] for r in RULES],
              "scanned": 0, "violations": [], "waivers": []}

    if args.all:
        paths = iter_source_files(ROOT)
        hits, waived, n = scan_paths(paths)
        report["scanned"] = n
        report["violations"] = hits
        report["waivers"] = waived
        print("")
        print("=== 全仓扫描 ===")
        if n == 0:
            print("FAIL 扫描范围为空")
            fails.append("扫描范围为空（防止空扫描假通过）")
        else:
            print("OK   扫描范围非空  :: %d 个文件（%s）" % (n, ", ".join(SCAN_EXTS)))
        if hits:
            for h in hits:
                print("FAIL %s 违规  %s L%d :: %s  <- %s"
                      % (h["rule"], h["file"], h["line"], h["label"], h["text"]))
            fails.append("全仓存在 %d 处违规" % len(hits))
        else:
            print("OK   生产文件零命中  :: []")
        # 豁免逐条打印：例外必须可见，豁免表变长即是信号
        if waived:
            print("--- 行级豁免（%d 处，逐条可见）---" % len(waived))
            for w in waived:
                print("     %s 豁免  %s L%d :: %s"
                      % (w["rule"], w["file"], w["line"], w["text"]))
        else:
            print("OK   无任何行级豁免（全仓无例外）")

    if args.json_out:
        report["ok"] = not fails
        with io.open(args.json_out, "w", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False, indent=2))

    print("")
    if fails:
        print("=== 失败 %d 项 ===" % len(fails))
        for x in fails:
            print(" - " + x)
        return 2
    print("静态不变量全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
