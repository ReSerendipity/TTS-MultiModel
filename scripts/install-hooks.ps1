# =============================================================================
# ⚰️ RETIRED · 内容级墓碑（content-level tombstone）— 2026-09-18 加装
# 本脚本是旧的钩子启用方式：它会把 core.hooksPath 切向 scripts/git-hooks（该目录
# 在本仓并不存在），一旦运行会静默禁用现役的整套 .githooks/ 钩子链（commit-msg
# DCO 校验 / pre-commit / pre-push 预检）。自 2026-09-16 起，本仓统一用 hooksPath：
#     git config core.hooksPath .githooks      （或运行 ./.githooks/install.sh）
# 墓碑目的：让“已退役”随文件本身生效——运行会立即打印 RETIRED 并以非零码退出，
# 不再劫持 hooksPath。下方保留退役前原始逻辑（逐字存档，因 exit 1 永不执行）。
# 见 AGENTS.md（本地维护、不随仓库分发）「钩子复现」条 与 docs/agents/REVISION_LOG.md v1.45。
# =============================================================================
Write-Host "RETIRED: scripts/install-hooks.ps1 is retired."
Write-Host "RETIRED: use git config core.hooksPath .githooks  (or run ./.githooks/install.sh)"
Write-Host "         Running this script would have hijacked core.hooksPath to a non-existent dir."
exit 1

<# ARCHIVED — 以下为退役前原始逻辑，逐字保留于块注释中（永不执行，且不参与解析）。
# ---- 上面的 exit 1 已终止脚本；块注释确保旧代码即便被误读也不会劫持 hooksPath ----
# Install git hooks for this repo (Windows PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File scripts/install-hooks.ps1
# 通过 core.hooksPath 指向 scripts/git-hooks/，避免手工拷贝到 .git/hooks
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$hooksDir = Join-Path $root "scripts\git-hooks"

if (-not (Test-Path $hooksDir)) {
    New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null
}

# pre-push: sh wrapper（git 用 sh 执行 hook）→ 调 python check_local.py
$prePush = Join-Path $hooksDir "pre-push"
$wrapper = @'
#!/app/sh
# 本地提交前检查（快检）：ruff / format / compileall / UTF-8 扫描
# 完整检查请手动运行: python scripts/check_local.py --full
# CI 是唯一权威门禁；此 hook 为辅助提醒，可 git push --no-verify 绕过（不推荐）。
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT" || exit 1
PY="$(command -v python || command -v python3)"
if [ -z "$PY" ]; then
  echo "pre-push: python not found, skipping local checks"
  exit 0
fi
EXTRA=""
if [ -f "$ROOT/scripts/check_local.py" ]; then
  # SeedVR2 等仓库启用 mypy 检查
  if grep -q "mypy" "$ROOT/scripts/check_local.py" 2>/dev/null; then
    EXTRA="--mypy"
  fi
  "$PY" "$ROOT/scripts/check_local.py" $EXTRA
  exit $?
fi
exit 0
'@
Set-Content -Path $prePush -Value $wrapper -Encoding UTF8 -NoNewline

# 确保 sh wrapper 无 BOM（git 的 sh 可能不认 BOM）
$bytes = [System.IO.File]::ReadAllBytes($prePush)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    [System.IO.File]::WriteAllBytes($prePush, $bytes[3..($bytes.Length - 1)])
}

# 指向 hooks 目录（git 2.9+）
git config core.hooksPath "scripts/git-hooks"

Write-Host "✅ git hooks installed: $hooksDir (core.hooksPath = scripts/git-hooks)"
Write-Host "   下次 git push 前会自动跑本地快检（ruff/format/compileall/UTF-8）"
#>
