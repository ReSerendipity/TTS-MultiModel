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
#!/bin/sh
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
