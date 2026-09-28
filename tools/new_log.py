#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变更日志骨架生成。

grill 定稿的命名规则（`logs/grill/2026-09-22-01-constitution.md` 分支 3）：
一次会话一个文件，形如 `logs/YYYY-MM-DD-NN-<主题>.md`，NN 是**当日序号**。
序号由本脚本计算，避免手工编号撞号。

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\new_log.py --topic "批1-地基"
    .venv\\Scripts\\python.exe tools\\new_log.py --topic "hook探针" --dir logs/planning

已存在同名文件时不覆盖、不递增，直接报错返回 2（避免静默产出空骨架）。
"""
from __future__ import print_function

import argparse
import io
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 只认规范命名：日期 + 两位序号 + 主题
NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})-(.+)\.md$")

TEMPLATE = """# {title}

**日期**：{date}
**序号**：当日第 {nn} 篇
**状态**：进行中

---

## 目标

（要达成什么；必须可判定「是否达成」）

## 改动清单

| 文件 | 动作 | 说明 |
| --- | --- | --- |
|  |  |  |

## 证据

（实际执行了什么命令、退出码、关键输出。空口结论不算证据 —— C-002）

## 未决

（本次没解决的问题，以及为什么。这一节最值钱：三个月后没人记得当初的取舍）
"""


def existing_indices(dirpath, date_str):
    if not os.path.isdir(dirpath):
        return []
    out = []
    for fn in os.listdir(dirpath):
        m = NAME_RE.match(fn)
        if m and m.group(1) == date_str:
            out.append(int(m.group(2)))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description="变更日志骨架生成")
    ap.add_argument("--topic", required=True, help="主题，会进文件名")
    ap.add_argument("--dir", default="logs", help="输出目录（相对项目根），默认 logs")
    ap.add_argument("--date", default=None, help="覆盖日期，YYYY-MM-DD（默认今天）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要生成的文件名")
    args = ap.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    dirpath = args.dir if os.path.isabs(args.dir) else os.path.join(ROOT, args.dir)

    # 主题里的路径分隔与非法字符必须处理掉，否则会写到意料之外的位置
    safe = re.sub(r"[\\/:*?\"<>|\s]+", "-", args.topic).strip("-")
    if not safe:
        print("REJECT: --topic 去掉非法字符后为空")
        return 2

    idxs = existing_indices(dirpath, date_str)
    nn = (max(idxs) + 1) if idxs else 1
    fname = "%s-%02d-%s.md" % (date_str, nn, safe)
    path = os.path.join(dirpath, fname)

    if os.path.exists(path):
        print("REJECT: 目标已存在，不覆盖：%s" % path)
        return 2

    if args.dry_run:
        print("DRY-RUN 将生成：%s（当日已有 %d 篇）" % (path, len(idxs)))
        return 0

    if not os.path.isdir(dirpath):
        os.makedirs(dirpath)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(title=args.topic, date=date_str, nn=nn))
    print("CREATED %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
