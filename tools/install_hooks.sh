#!/usr/bin/env bash
# 安装 git pre-commit 钩子：提交前跑快检（tools/check_all.sh --fast）。卸载：rm .git/hooks/pre-commit
set -e
cd "$(dirname "$0")/.."
hook=.git/hooks/pre-commit
cat > "$hook" <<'H'
#!/usr/bin/env bash
# convtree：提交前快检（跳过请用 git commit --no-verify，并在提交说明里写原因）
exec "$(git rev-parse --show-toplevel)/tools/check_all.sh" --fast
H
chmod +x "$hook"
echo "已安装 $hook"
