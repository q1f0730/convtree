#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事件流账本：追加 / 校验 / 查询。

依据 constitution/CONSTITUTION.md 的 C-001（跨步骤传递必须结构化）与 C-002（状态变更走三道闸）。

设计约束：
- 追加式且不可变：本工具只 append，绝不修改或删除已有记录。
- 每条记录自带 based_on，形成可重放的因果链。
- 三道闸的校验结果写进记录本身，使「是否过闸」可事后审计。

用法：
    python tools/ledger.py append --kind goal --by main --summary "..." --artifact a.md
    python tools/ledger.py append --kind verification --by verifier --verify-of E-0010 \
        --evidence "运行 accept_step1.py，exit=0" --summary "..." --invariants pass
    python tools/ledger.py run --by verifier --verify-of E-0010 --summary "..." \
        -- .venv/Scripts/python.exe server/accept_step1.py
    python tools/ledger.py verify
    python tools/ledger.py list [--kind goal] [--tail N]

写入闸（只对**新记录**生效，历史链不追溯，见 verify 的警告）：
- verification 事件必须带 --verify-of，且被验事件的 by 不得与本次相同（C-003）。
- verification 事件的任一闸自报 pass 时，必须带 --evidence 或经 run 子命令产生（C-002：
  校验是执行出来的，不是声明出来的）。
- checks 的合法取值：pass / fail / n/a / none。
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "ledger", "events.jsonl")

# Windows 控制台默认 GBK，账本记录含 emoji 时 print 直接 UnicodeEncodeError
# （2026-09-23 实测：list 子命令崩在含 emoji 的 summary 上，P-002）。
# 账本文件写入在 commit 里已硬编码 UTF-8，不受影响；这里只管终端输出。
for _s in (sys.stdout, sys.stderr):
    _enc = (getattr(_s, "encoding", "") or "").lower().replace("-", "")
    if _enc != "utf8":
        _s.reconfigure(encoding="utf-8", errors="replace")

KINDS = ["goal", "operator", "artifact", "verification", "decision", "incident"]
ACTORS = ["main", "verifier", "human"]
CHECKS = ["schema", "permission", "invariants", "conflict"]
CHECK_VALUES = ["pass", "fail", "n/a", "none"]
STATUS = ["proposed", "committed", "rejected"]
REQUIRED = ["id", "ts", "by", "kind", "summary", "status"]


def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def load():
    if not os.path.exists(LEDGER):
        return []
    out = []
    with io.open(LEDGER, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError as exc:
                raise SystemExit("第 %d 行不是合法 JSON：%s" % (lineno, exc))
    return out


def validate(events):
    """返回错误列表。这是三道闸里的「校验」环节。"""
    errs = []
    seen = set()
    for e in events:
        eid = e.get("id", "<无 id>")
        for k in REQUIRED:
            if not e.get(k):
                errs.append("%s 缺字段 %s" % (eid, k))
        if e.get("by") not in ACTORS:
            errs.append("%s by 非法：%r" % (eid, e.get("by")))
        if e.get("kind") not in KINDS:
            errs.append("%s kind 非法：%r" % (eid, e.get("kind")))
        if e.get("status") not in STATUS:
            errs.append("%s status 非法：%r" % (eid, e.get("status")))
        if eid in seen:
            errs.append("id 重复：%s" % eid)
        base = e.get("based_on")
        if base and base not in seen:
            errs.append("%s 的 based_on=%s 不在它之前的记录里（悬空或指向未来）" % (eid, base))
        seen.add(eid)
    return errs


def next_id(events):
    top = 0
    for e in events:
        try:
            top = max(top, int(str(e.get("id", "")).split("-")[1]))
        except (IndexError, ValueError):
            pass
    return "E-%04d" % (top + 1)


def gate_new_record(rec, events):
    """三道闸的**执行面**：只约束新记录。历史链不追溯（否则旧账全部非法）。"""
    errs = []
    for c in CHECKS:
        v = rec["checks"].get(c)
        if v not in CHECK_VALUES:
            errs.append("checks.%s 取值非法：%r（合法：%s）" % (c, v, "/".join(CHECK_VALUES)))
    if rec["kind"] == "verification":
        vo = rec.get("verify_of")
        if not vo:
            errs.append("verification 事件必须带 --verify-of（验的是哪条记录）")
        else:
            target = next((e for e in events if e["id"] == vo), None)
            if not target:
                errs.append("--verify-of=%s 不存在于既有记录中" % vo)
            elif target["by"] == rec["by"]:
                errs.append("C-003：验者不得与产出者同为 %r（%s 的 by=%r）"
                            % (rec["by"], vo, target["by"]))
        has_pass = any(rec["checks"].get(c) == "pass" for c in CHECKS)
        if has_pass and not rec.get("evidence"):
            errs.append("自报 pass 必须附 --evidence（实际执行了什么、结果如何），"
                        "或改用 run 子命令由工具真实执行")
    return errs


def commit(rec, events):
    errs = validate(events + [rec]) + gate_new_record(rec, events)
    if errs:
        for e in errs:
            print("REJECT: " + e)
        return 2
    parent = os.path.dirname(LEDGER)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("COMMITTED %s  %s  %s" % (rec["id"], rec["kind"], rec["summary"][:48]))
    return 0


def cmd_append(args):
    events = load()
    rec = {
        "id": next_id(events),
        "ts": now_iso(),
        "by": args.by,
        "kind": args.kind,
        "based_on": args.based_on or (events[-1]["id"] if events else None),
        "summary": args.summary,
        "artifacts": args.artifact or [],
        "checks": dict((k, getattr(args, k) or "n/a") for k in CHECKS),
        "status": args.status,
    }
    if args.verify_of:
        rec["verify_of"] = args.verify_of
    if args.evidence:
        rec["evidence"] = args.evidence
    return commit(rec, events)


def cmd_run(args):
    """真实执行一条命令，把 exit code 与输出尾部作为证据落账。

    这是三道闸里「校验」环节的唯一诚实来源：pass/fail 由进程退出码决定，
    不由执行者口头声明。
    """
    import subprocess
    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("REJECT: 没有要执行的命令（-- 之后为空）")
        return 2
    # 先拒后跑：缺 --verify-of 的记录注定过不了闸，
    # 不能等命令跑完才告诉执行者（E-0033 后补强）。
    if not args.verify_of:
        print("REJECT: run 落的是 verification 记录，必须带 --verify-of（验的是哪条记录）")
        return 2
    events = load()
    proc = subprocess.run(cmd, cwd=ROOT,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = proc.stdout.decode("utf-8", "replace")
    verdict = "pass" if proc.returncode == 0 else "fail"
    rec = {
        "id": next_id(events),
        "ts": now_iso(),
        "by": args.by,
        "kind": "verification",
        "based_on": events[-1]["id"] if events else None,
        "summary": args.summary,
        "artifacts": args.artifact or [],
        "checks": {"schema": "n/a", "permission": "n/a",
                   "invariants": verdict, "conflict": "n/a"},
        "status": "committed",
        "evidence": {
            "cmd": cmd,
            "exit": proc.returncode,
            "tail": out[-800:],
        },
    }
    if args.verify_of:
        rec["verify_of"] = args.verify_of
    print(out[-1200:])
    return commit(rec, events)


def cmd_verify(args):
    events = load()
    errs = validate(events)
    print("记录数：%d" % len(events))
    if errs:
        for e in errs:
            print("FAIL: " + e)
        return 2
    # 历史遗留警告：自报 pass 但无证据的 verification（新记录已禁止，旧账只警告）
    warned = 0
    for e in events:
        if e["kind"] == "verification" and not e.get("evidence"):
            if any(e.get("checks", {}).get(c) == "pass" for c in CHECKS):
                print("WARN: %s 自报 pass 但无证据（历史遗留，新记录已禁止）" % e["id"])
                warned += 1
    kinds = {}
    for e in events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print("分布：" + "  ".join("%s=%d" % kv for kv in sorted(kinds.items())))
    print("OK：链完整，字段合法，无悬空 based_on（历史无证据 pass：%d 条，仅警告）" % warned)
    return 0


def cmd_list(args):
    events = load()
    if args.kind:
        events = [e for e in events if e["kind"] == args.kind]
    if args.tail:
        events = events[-args.tail:]
    for e in events:
        print("%s  %-12s %-6s %s" % (e["id"], e["kind"], e["by"], e["summary"]))
        for a in e.get("artifacts") or []:
            print("             └─ " + a)
    return 0


def main():
    p = argparse.ArgumentParser(description="事件流账本")
    sub = p.add_subparsers(dest="cmd")

    ap = sub.add_parser("append", help="追加一条记录（过闸后才落账）")
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--by", required=True, choices=ACTORS)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--artifact", action="append", help="可重复，产出物路径")
    ap.add_argument("--based-on", dest="based_on")
    ap.add_argument("--verify-of", dest="verify_of", help="verification 必填：验的是哪条记录")
    ap.add_argument("--evidence", help="自报 pass 必填：实际执行了什么、结果如何")
    ap.add_argument("--status", default="committed", choices=STATUS)
    for c in CHECKS:
        ap.add_argument("--" + c, default="n/a",
                        choices=CHECK_VALUES, help="该闸的校验结果")
    ap.set_defaults(func=cmd_append)

    rp = sub.add_parser("run", help="真实执行命令并落账（pass/fail 由退出码决定）")
    rp.add_argument("--by", required=True, choices=ACTORS)
    rp.add_argument("--summary", required=True)
    rp.add_argument("--verify-of", dest="verify_of")
    rp.add_argument("--artifact", action="append")
    rp.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="-- 之后是要执行的命令")
    rp.set_defaults(func=cmd_run)

    vp = sub.add_parser("verify", help="校验链完整性")
    vp.set_defaults(func=cmd_verify)

    lp = sub.add_parser("list", help="列出记录")
    lp.add_argument("--kind", choices=KINDS)
    lp.add_argument("--tail", type=int)
    lp.set_defaults(func=cmd_list)

    args = p.parse_args()
    if not getattr(args, "func", None):
        p.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
