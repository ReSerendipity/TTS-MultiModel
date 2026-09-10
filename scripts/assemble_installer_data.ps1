#Requires -Version 5.1
# scripts/assemble_installer_data.ps1
# 组装 NSIS 安装器的数据分卷（TTSMultiModel-Data.7z.001 / .002 / ...）。
#
# 输入：发布目录（release_tauri.ps1 的 staging：TTSMultiModel.exe + app/）
#       + 可选模型目录（model/ 或 checkpoints/，随包分发时）。
# 输出：OutDir\TTSMultiModel-Data.7z.001...（7z 原生 -v 分卷，7za 解压 .001 时自动续卷）
#       + SHA256SUMS.txt（覆盖全部分卷）+ upload-list.txt。
#
# 为什么用 7z 原生 -v 分卷而非顺序字节切片：setup.nsi 只有一行
# `7za x ...001`（任务书「解压数据分卷（7za）」），7z 原生卷可被 7za 自动
# 拼接续卷；字节切片卷需先合并再解压，安装器无法保证该前置步骤。
# 分卷上限默认 1900MB（GitHub 2GiB 单文件硬约束内留余量）。
#
# 用法：
#   .\scripts\assemble_installer_data.ps1 -Version 2.2.1
#   .\scripts\assemble_installer_data.ps1 -Version 2.2.1 -ModelDir D:\models -OutDir D:\installer-data

[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$StagingDir = '',
    [string]$ModelDir = '',
    [string]$OutDir = '',
    [long]$MaxPartBytes = 0
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1')

if (-not $StagingDir) { $StagingDir = Join-Path $root 'dist\tauri-release\staging' }
if (-not $OutDir) { $OutDir = Join-Path $root 'dist\installer-data' }
if ($MaxPartBytes -le 0) { $MaxPartBytes = Get-TTSMultiModelDefaultMaxPart }

# ---------- 版本号 ----------
if (-not $Version) {
    $vfile = Join-Path $StagingDir 'app\version.json'
    if (Test-Path -LiteralPath $vfile) {
        $vj = Read-TTSMultiModelJson -Path $vfile
        $Version = [string]$vj.version
    }
}
if (-not $Version) {
    throw '无法确定版本号，请用 -Version 显式指定'
}
if ($Version -notmatch '^\d+\.\d+\.\d+') {
    throw "非法版本号: $Version"
}

# ---------- 断言 staging 结构 ----------
$exePath = Join-Path $StagingDir 'TTSMultiModel.exe'
$appDir = Join-Path $StagingDir 'app'
foreach ($p in @($exePath, $appDir)) {
    if (-not (Test-Path -LiteralPath $p)) {
        throw "发布目录不完整，缺少: $p（先跑 release_tauri.ps1 的 staging 组装，或核对 -StagingDir）"
    }
}
# app/ 必须有 version.json（壳本地版本识别，缺失会静默跳过更新）
$verJson = Join-Path $appDir 'version.json'
if (-not (Test-Path -LiteralPath $verJson)) {
    $srcVer = Join-Path $root 'version.json'
    if (Test-Path -LiteralPath $srcVer) {
        Copy-Item -LiteralPath $srcVer -Destination $verJson -Force
        Write-Host "[assemble_installer_data] 已将根 version.json 复制到 app\"
    } else {
        throw "缺少 app\version.json 且仓库根也无 version.json，无法组装"
    }
}

# ---------- 合并目录（硬链接优先，避免大体积复制） ----------
$merge = Join-Path $OutDir '_merge'
Remove-TTSMultiModelTreeFast -Path $merge
New-Item -ItemType Directory -Path $merge -Force | Out-Null

$staged = Get-ChildItem -LiteralPath $StagingDir -Force
$totalBytes = [long]0
foreach ($item in $staged) {
    $stats = Copy-TTSMultiModelTree -Source $item.FullName -Dest (Join-Path $merge $item.Name) `
        -ExcludePatterns @('*.tmp', '*.bak', '*.bak.*', '__pycache__\*', '*.log')
    $totalBytes += $stats.Bytes
    Write-Host ("  · {0,-14} {1,6} 文件 / {2}" -f $item.Name, $stats.Files, (Format-TTSMultiModelSize $stats.Bytes))
}
if ($ModelDir -and (Test-Path -LiteralPath $ModelDir)) {
    $leaf = Split-Path -Leaf $ModelDir
    $stats = Copy-TTSMultiModelTree -Source $ModelDir -Dest (Join-Path $merge $leaf) `
        -ExcludePatterns @('*.tmp', '*.bak', '*.bak.*', '__pycache__\*', '*.log')
    $totalBytes += $stats.Bytes
    Write-Host ("  · {0,-14} {1,6} 文件 / {2}" -f $leaf, $stats.Files, (Format-TTSMultiModelSize $stats.Bytes))
}

# 禁止载荷断言（密钥 / ffmpeg 随包分发检查）
Assert-TTSMultiModelNoForbiddenPayload -Path $merge

# ---------- 磁盘预检 ----------
$needed = [math]::Round(($totalBytes * 1.35) / 1GB, 1)
Assert-TTSMultiModelDiskSpace -Path $OutDir -NeededGb $needed

# ---------- 7z 原生 -v 分卷 ----------
$sevenZip = Find-TTSMultiModelSevenZip
if (-not $sevenZip) {
    throw '未找到 7-Zip（7za.exe），无法生成安装器数据卷。请安装 7-Zip 或把 7za.exe 放入 PATH。'
}
$archiveBase = Join-Path $OutDir 'TTSMultiModel-Data.7z'
Get-ChildItem -LiteralPath $OutDir -Filter 'TTSMultiModel-Data.7z.*' -File -ErrorAction SilentlyContinue |
    Remove-Item -Force
$volMb = [math]::Floor($MaxPartBytes / 1MB)
Write-Host "[assemble_installer_data] 打包（7z 原生 -v${volMb}M 分卷）：$merge -> $archiveBase"
$names = @(Get-ChildItem -LiteralPath $merge -Force | ForEach-Object { $_.Name })
$arcArgs = @('a', '-t7z', "-v${volMb}M", '-mx=4', '-mmt=on', '-bd', '-bso0', '-bsp0', '-y', $archiveBase) + $names
$prev = Get-Location
try {
    Set-Location -LiteralPath $merge
    $res = Invoke-TTSMultiModelNative -Exe $sevenZip -Arguments $arcArgs
} finally {
    Set-Location -LiteralPath $prev
}
if ($res.ExitCode -ne 0) {
    throw "7z 打包失败，退出码 $($res.ExitCode)`n$($res.Text)"
}
Remove-TTSMultiModelTreeFast -Path $merge

# ---------- 回读验证：7za t 整个分卷集 ----------
Write-Host '[assemble_installer_data] 回读验证（7za t 分卷集完整性）...'
$tres = Invoke-TTSMultiModelNative -Exe $sevenZip -Arguments @('t', ($archiveBase + '.001'), '-y')
if ($tres.ExitCode -ne 0) {
    throw "分卷回读验证失败：$($tres.Text)"
}

# ---------- SHA256SUMS（覆盖全部分卷） ----------
$volumes = @(Get-ChildItem -LiteralPath $OutDir -Filter 'TTSMultiModel-Data.7z.*' -File | Sort-Object Name)
if ($volumes.Count -eq 0) {
    throw '分卷产物为空'
}
$sumLines = @()
foreach ($v in $volumes) {
    $sumLines += "{0}  {1}" -f (Get-TTSMultiModelFileSha256 -Path $v.FullName), $v.Name
}
$sumsFile = Join-Path $OutDir 'SHA256SUMS.txt'
[System.IO.File]::WriteAllLines($sumsFile, [string[]]$sumLines, (New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllLines((Join-Path $OutDir 'upload-list.txt'), [string[]]@($volumes | ForEach-Object { $_.FullName }), (New-Object System.Text.UTF8Encoding($false)))

$total = [long]0
foreach ($v in $volumes) { $total += $v.Length }
Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host (" 数据卷完成：{0} 个分卷 / {1}" -f $volumes.Count, (Format-TTSMultiModelSize $total)) -ForegroundColor Green
Write-Host (" 产物目录：$OutDir") -ForegroundColor Green
Write-Host ' 下一步：将 TTSMultiModel-Setup-v*.exe 与全部分卷放同一目录发布（SHA256SUMS.txt 逐行核对）' -ForegroundColor Green
Write-Host '==============================================' -ForegroundColor Green
