#!/usr/bin/env bash
# 规范引用审计的状态消费者（CI 侧）。
#
# 为什么需要这个文件：scripts/check_spec_refs.py 的退出码契约里，0 同时表示
# 「CHECKED 且无新增 HARD 项」和「SKIPPED：审计器缺失、本轮一条都没查」——它按设计
# 把审计器放在仓外（家族级工具，干净检出与 runner 结构性拿不到），所以 SKIPPED 退 0
# 是既定口径，本脚本不重开它。但仓内验收清单（docs/DOD.md、docs/release-governance.md）
# 原文写的是「退出码 0（无幻影/死链/假门禁）」，只看退出码就等于把「根本没查」勾成「查过了」。
# 本脚本消费包装器专门为此输出的 SPEC_REFS STATE= 标记行，把两种状态在日志里分开。
#
# 退出码：0 = 本轮确实审计过且无新增 HARD 项，或审计器缺失（SKIPPED，只告警不阻断）；
#         非 0 = 出现新增 HARD 项 / 包装器自身故障 / 状态不可证。
#
# 两处使用：.github/workflows/structure-guard.yml、.github/workflows/docs-consistency.yml。
# 默认 shell 为 bash --noprofile --norc -eo pipefail，故在捕获退出码前先关 -e，
# 否则「检查器判红」会在读到结论之前先把步骤打断。

set -uo pipefail

if [ ! -f scripts/check_spec_refs.py ]; then
    echo "::warning title=规范引用审计未运行::scripts/check_spec_refs.py 不在本轮检出里，本步未做任何审计"
    exit 0
fi

set +e
OUT=$(python scripts/check_spec_refs.py)
RC=$?
set -e

printf '%s\n' "$OUT"

if [ "$RC" -ne 0 ]; then
    echo "::error title=规范引用审计判定不通过::exit=$RC（1=新增 HARD 项，2=包装器自身故障）"
    exit "$RC"
fi

# 只认「行首即 SPEC_REFS STATE=」的行。包装器另会往 stderr 打若干 "probed, not found:"
# 行、正文里也可能复述该字样，用「出现过」判定会被无关行满足；抽不到标记时绝不按通过处理
# ——缺失的期望值不算命中。
STATE_LINES=$(printf '%s\n' "$OUT" | grep -c '^SPEC_REFS STATE=' || true)
STATE=$(printf '%s\n' "$OUT" | grep -m1 '^SPEC_REFS STATE=' | sed -n 's/^SPEC_REFS STATE=\([A-Z]*\).*/\1/p' || true)

if [ "$STATE_LINES" != "1" ] || [ -z "$STATE" ]; then
    echo "::error title=规范引用审计状态不可证::退出码 0 但行首 STATE 标记行数为 ${STATE_LINES}（期望恰好 1），无法判定本轮是否真审计过，拒绝按通过处理"
    exit 1
fi

case "$STATE" in
    CHECKED)
        echo "SPEC_REFS：本轮已审计（STATE=CHECKED），可作为验收依据"
        ;;
    SKIPPED)
        echo "::warning title=规范引用本轮未审计::STATE=SKIPPED（仓外审计器缺失），一条规范引用都没查；不得据此勾选 DOD / 发布治理里的「无幻影/死链/假门禁」"
        ;;
    *)
        echo "::error title=规范引用审计状态未知::STATE=$STATE 不在 CHECKED/SKIPPED 之内"
        exit 1
        ;;
esac
