#!/bin/sh
# 克隆后执行一次，把 .githooks 安装到本机 .git/hooks
DIR=$(cd "$(dirname "$0")" && pwd)
GD=$(git rev-parse --git-dir 2>/dev/null)
[ -z "$GD" ] && GD=".git"
mkdir -p "$GD/hooks"
for h in lib-depwatch.sh prepare-commit-msg commit-msg post-merge post-checkout; do
    cp "$DIR/$h" "$GD/hooks/$h" && chmod +x "$GD/hooks/$h"
done
echo "已安装：prepare-commit-msg commit-msg post-merge post-checkout"
echo "若还需轻量 pre-commit（大文件/密钥/卫生/守卫）："
echo "  cp .githooks/pre-commit-lite .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit"
