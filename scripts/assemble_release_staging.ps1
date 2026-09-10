#Requires -Version 5.1
# scripts/assemble_release_staging.ps1
# 组装桌面版发布目录（release_tauri.ps1 / assemble_installer_data.ps1 的输入）：
#
#   staging\
#     TTSMultiModel.exe      <- 壳二进制（默认 desktop\src-tauri\target\release\tts-multimodel-desktop.exe）
#     start_portable.py      <- 壳启动入口（仓库根）
#     config.yaml            <- 运行时配置（安装根，更新器 PRESERVE_ROOT_FILES 保留）
#     version.json           <- 版本单源（壳本地版本识别）
#     app\                   <- 后端代码（仓库 app/，排除运行时状态/密钥/缓存）
#     runtime\               <- 便携 Python 运行时（-RuntimeDir 提供时并入；缺失则仅告警）
#     model\ | checkpoints\  <- 模型权重（-ModelDir 提供时并入，可选）
#     cache\ logs\ data\     <- 运行期自建目录（不随包，保留占位）
#
# 用法：
#   .\scripts\assemble_release_staging.ps1 -Version 2.2.1
#   .\scripts\assemble_release_staging.ps1 -ShellExe D:\build\TTSMultiModel.exe -RuntimeDir D:\WPy64-312101

[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$ShellExe = '',
    [string]$RuntimeDir = '',
    [string]$ModelDir = '',
    [string]$OutDir = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1')

if (-not $OutDir) { $OutDir = Join-Path $root 'dist\tauri-release\staging' }
if (-not $Version) {
    $vj = Read-TTSMultiModelJson -Path (Join-Path $root 'version.json')
    $Version = [string]$vj.version
}
if (-not $ShellExe) {
    foreach ($cand in @(
            (Join-Path $root 'desktop\src-tauri\target\release\tts-multimodel-desktop.exe'),
            (Join-Path $root 'desktop\src-tauri\target\debug\tts-multimodel-desktop.exe')
        )) {
        if (Test-Path -LiteralPath $cand) { $ShellExe = $cand; break }
    }
}
if (-not $ShellExe -or -not (Test-Path -LiteralPath $ShellExe)) {
    throw "找不到壳二进制，请用 -ShellExe 指定（如 desktop\src-tauri\target\release\tts-multimodel-desktop.exe）"
}

# ---------- 清理并重建 staging ----------
Remove-TTSMultiModelTreeFast -Path $OutDir
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$total = [long]0

$copyLeaf = {
    param($Src, $Dst)
    $stats = Copy-TTSMultiModelTree -Source $Src -Dest $Dst -ExcludePatterns @('*.tmp', '*.bak', '__pycache__\*', '*.log')
    Write-Host ("  · {0,-14} {1,6} 文件 / {2}" -f (Split-Path -Leaf $Dst), $stats.Files, (Format-TTSMultiModelSize $stats.Bytes))
    return $stats.Bytes
}

Write-Host '[assemble_release_staging] 组装发布目录...'
# 1) 壳二进制
Copy-Item -LiteralPath $ShellExe -Destination (Join-Path $OutDir 'TTSMultiModel.exe') -Force
$total += (Get-Item -LiteralPath (Join-Path $OutDir 'TTSMultiModel.exe')).Length
Write-Host ("  · TTSMultiModel.exe（{0}）" -f (Format-TTSMultiModelSize (Get-Item -LiteralPath (Join-Path $OutDir 'TTSMultiModel.exe')).Length))
# 2) 根文件：start_portable.py / config.yaml / version.json / 许可与说明
foreach ($f in @('start_portable.py', 'config.yaml', 'version.json', 'LICENSE', 'README.md', 'SECURITY.md', 'CHANGELOG.md', 'start.bat', 'pyproject.toml', 'requirements-lock.txt')) {
    $src = Join-Path $root $f
    if (Test-Path -LiteralPath $src -PathType Leaf) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $OutDir $f) -Force
    }
}
# 3) app/（后端代码，排除运行时状态/密钥/缓存）
$appExclude = @(
    '__pycache__\*', '*.pyc', '*.pyo', '.pytest_cache\*',
    'app\integrated_app\data\*', 'logs\*.log', '*.db', '*.db-wal', '*.db-shm', '*.log',
    '.csrf_secret', '.pii_key', '.history_hmac_key', '.integrity_hmac_secret',
    '.manifest_signing_key', '.watermark_key', '*.bak', '*.bak.*', 'cache\*', 'torch_compile_cache\*', 'outputs\*'
)
$total += (& $copyLeaf (Join-Path $root 'app') (Join-Path $OutDir 'app'))
# 4) runtime/（可选；缺失时壳可用系统 .venv 开发启动，发布包必须提供）
if ($RuntimeDir -and (Test-Path -LiteralPath $RuntimeDir)) {
    $total += (& $copyLeaf $RuntimeDir (Join-Path $OutDir 'runtime'))
} else {
    Write-Warning '未提供 -RuntimeDir：staging 缺 runtime\python.exe，release_tauri.ps1 将拒绝发布（本地开发冒烟可省略）'
}
# 5) 模型目录（可选）
if ($ModelDir -and (Test-Path -LiteralPath $ModelDir)) {
    $total += (& $copyLeaf $ModelDir (Join-Path $OutDir (Split-Path -Leaf $ModelDir)))
}
# 6) 运行期自建目录占位（data/logs/cache 由后端首次启动创建，这里不预建以避免空目录进入包）

Assert-TTSMultiModelNoForbiddenPayload -Path $OutDir
Write-Host ''
Write-Host ('[assemble_release_staging] 完成：{0}（未压缩）' -f (Format-TTSMultiModelSize $total))
Write-Host ("  staging: $OutDir")
Write-Host '  下一步：release_tauri.ps1（完整包分卷）/ assemble_installer_data.ps1（NSIS 数据卷）'
