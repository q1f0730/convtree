#!/usr/bin/env bash
# 一键跑全部检查。用法：tools/check_all.sh [--fast]
#   --fast  只跑快检（pre-commit 用）：验收 + 前端纯逻辑 + 不变量 + 账本；跳过真浏览器 e2e
# 解释器：$PY > .venv/bin/python > /tmp/ctv/bin/python > python3
set -u
cd "$(dirname "$0")/.."
PY="${PY:-}"
for c in .venv/bin/python /tmp/ctv/bin/python python3; do
  [ -z "$PY" ] && command -v "$c" >/dev/null 2>&1 && PY="$c"
done
FAST=0; [ "${1:-}" = "--fast" ] && FAST=1
fail=0
run() { local name="$1"; shift
  local out; out="$("$@" 2>&1)"; local rc=$?
  local last; last="$(printf '%s\n' "$out" | grep -E '^共 [0-9]+ 例' | tail -1)"; [ -z "$last" ] && last="$(printf '%s\n' "$out" | grep -v '^\s*$' | tail -1)"
  if [ $rc -eq 0 ]; then echo "PASS  $name  | $last"; else echo "FAIL  $name (exit $rc)"; printf '%s\n' "$out" | grep -E 'FAIL|Error|Traceback' | head -20; fail=1; fi
}
run "accept_full"      "$PY" server/accept_full.py
run "accept_v2"        "$PY" server/accept_v2.py
if command -v node >/dev/null 2>&1; then run "test_ui_logic" node tools/test_ui_logic.js; else echo "SKIP  test_ui_logic（无 node）"; fi
run "check_invariants" "$PY" tools/check_invariants.py --all
run "ledger verify"    "$PY" tools/ledger.py verify
if [ $FAST -eq 0 ]; then
  "$PY" tools/ui_e2e.py > /tmp/ct_e2e.out 2>&1; rc=$?
  if [ $rc -eq 0 ]; then echo "PASS  ui_e2e  | $(grep -v '^\s*$' /tmp/ct_e2e.out | tail -1)"
  elif [ $rc -eq 3 ]; then echo "SKIP  ui_e2e（无 chromium/playwright）"
  else echo "FAIL  ui_e2e (exit $rc)"; grep -E 'FAIL|Error' /tmp/ct_e2e.out | head -20; fail=1; fi
fi
[ $fail -eq 0 ] && echo "ALL GREEN" || echo "HAS FAILURES"
exit $fail
