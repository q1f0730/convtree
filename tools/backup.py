#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动备份（G-1 的落地）。

数据全在本地 SQLite 且 append-only——只增长，丢了即永久丢。
「导出 Markdown/JSON」是迁出路径，靠人主动触发，不防意外；本工具是防意外的机制。

设计：
- 用 SQLite 在线 backup API 拷贝（WAL 下也能拿到一致性快照，不要求停服）。
- 每份备份是一个时间戳目录：全部 data/*.db + 账本 + manifest（行数/大小/校验结果）。
- 写完后立即自验：对副本跑 integrity_check，并比对关键表行数——备份不验等于没备。
- 轮转只保留最近 N 份（默认 10）。
- auto_backup() 供服务端启动时调用：距上次备份超过 max_age_hours 才动手。

用法：
    python tools/backup.py            # 立即备份一份
    python tools/backup.py --keep 5
"""
from __future__ import print_function

import argparse
import io
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
LEDGER = os.path.join(ROOT, "ledger", "events.jsonl")
BACKUP_DIR = os.path.join(ROOT, "backups")

# 验收库（accept_*.db / smoke.db / e2e.db）是易失测试产物，不值得备份
SKIP_PREFIXES = ("accept_", "smoke", "e2e")
KEY_TABLES = ("notebook", "node", "message", "overlay", "note")


def _list_dbs(data_dir):
    if not os.path.isdir(data_dir):
        return []
    out = []
    for fn in sorted(os.listdir(data_dir)):
        if fn.endswith(".db") and not fn.startswith(SKIP_PREFIXES):
            out.append(os.path.join(data_dir, fn))
    return out


def _table_counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        counts = {}
        for t in KEY_TABLES:
            try:
                counts[t] = conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
            except sqlite3.OperationalError:
                pass  # 表不存在（例如旧库没 note）——如实缺省
        return counts
    finally:
        conn.close()


def _backup_one_db(src_path, dst_path):
    """在线一致性拷贝 + 对副本跑 integrity_check。返回副本的 integrity_check 结果。"""
    src = sqlite3.connect(src_path)
    try:
        src.execute("PRAGMA busy_timeout=10000")
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
            return ok
        finally:
            dst.close()
    finally:
        src.close()


def run_backup(keep=10, data_dir=None, backup_dir=None):
    data_dir = data_dir or DATA_DIR
    backup_dir = backup_dir or BACKUP_DIR
    dbs = _list_dbs(data_dir)

    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backup_dir, ts)
    suffix = 1
    while os.path.exists(dest):   # 同一秒内连跑两次也不撞目录
        suffix += 1
        dest = os.path.join(backup_dir, "%s-%d" % (ts, suffix))
    os.makedirs(dest)

    manifest = {"ts": os.path.basename(dest), "created_at": int(time.time()),
                "dbs": {}, "ledger": None}
    try:
        for db in dbs:
            name = os.path.basename(db)
            # 行数比对必须是「防竞态」的：源库 append-only、备份期间可能仍在写入，
            # 所以先取备份前的源行数，副本行数只要求 **>=** 它（副本是备份时点之后的一致性快照）。
            # 要求「==」会在服务运行中误报失败（隔离审查 E-review-4 的 MEDIUM 项）。
            rows_before = _table_counts(db)
            integ = _backup_one_db(db, os.path.join(dest, name))
            rows_copy = _table_counts(os.path.join(dest, name))
            grew_ok = all(rows_copy.get(t, 0) >= v for t, v in rows_before.items())
            manifest["dbs"][name] = {
                "integrity_check": integ,
                "rows_before": rows_before,
                "rows_copy": rows_copy,
                "rows_ge_before": grew_ok,
                "size": os.path.getsize(os.path.join(dest, name)),
            }
            if integ != "ok" or not grew_ok:
                raise RuntimeError("备份自验失败：%s integrity=%r rows_ge_before=%r"
                                   % (name, integ, grew_ok))
        if os.path.exists(LEDGER):
            shutil.copyfile(LEDGER, os.path.join(dest, "events.jsonl"))
            manifest["ledger"] = os.path.getsize(LEDGER)
        with io.open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(manifest, ensure_ascii=False, indent=2))
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)  # 失败不留半成品
        raise

    _rotate(backup_dir, keep)
    return dest


def _rotate(backup_dir, keep):
    stamps = sorted(d for d in os.listdir(backup_dir)
                    if os.path.isdir(os.path.join(backup_dir, d)))
    while len(stamps) > keep:
        shutil.rmtree(os.path.join(backup_dir, stamps.pop(0)))


def _latest_backup_time(backup_dir):
    latest = 0
    if os.path.isdir(backup_dir):
        for d in os.listdir(backup_dir):
            m = os.path.join(backup_dir, d, "manifest.json")
            if os.path.exists(m):
                try:
                    with io.open(m, "r", encoding="utf-8") as f:
                        latest = max(latest, json.load(f).get("created_at", 0))
                except (ValueError, IOError):
                    continue
    return latest


def auto_backup(max_age_hours=24, keep=10):
    """距上次成功备份超过 max_age_hours 才执行；否则返回 None（调用方不打印）。"""
    last = _latest_backup_time(BACKUP_DIR)
    if time.time() - last < max_age_hours * 3600:
        return None
    return run_backup(keep=keep)


def main():
    p = argparse.ArgumentParser(description="数据自动备份（G-1）")
    p.add_argument("--keep", type=int, default=10, help="保留最近几份（默认 10）")
    args = p.parse_args()
    dest = run_backup(keep=args.keep)
    with io.open(os.path.join(dest, "manifest.json"), "r", encoding="utf-8") as f:
        m = json.load(f)
    print("备份完成：", dest)
    for name, info in m["dbs"].items():
        print("  %-16s rows=%s integrity=%s" % (name, info["rows_copy"], info["integrity_check"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
