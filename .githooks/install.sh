#!/bin/sh
# 启用本仓库的 .githooks（克隆后执行一次即可）
# 之后钩子源码统一由 .githooks/ 提供，改动即时生效，无需再复制文件。
git config core.hooksPath .githooks
echo "已启用 .githooks：core.hooksPath=$(git config core.hooksPath)"
echo ""
echo "已生效钩子："
echo "  pre-commit          框架可用则走 pre-commit，否则走轻量检查+根目录守卫"
echo "  pre-push            执行 precheck.ps1（无则退回根目录守卫）"
echo "  prepare-commit-msg  自动补 DCO 签名（幂等）"
echo "  commit-msg          DCO 硬校验 + 规范软提示"
echo "  post-merge/-checkout 依赖清单变更提醒"
