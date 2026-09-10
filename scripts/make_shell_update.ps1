#Requires -Version 5.1
# scripts/make_shell_update.ps1
# 生成 Tauri 壳「增量更新」资产（任务书阶段三第 5 条：下载 app zip → SHA256 校验
# → 原子换载 → 失败回滚）。壳侧契约见 desktop/src-tauri/src/updater.rs：
#   - zip 根 = 安装根（app_dir）的直接子项：app/ + start_portable.py + config.yaml
#     + version.json（必须含 version.json，壳按它识别本地版本）；
#   - PRESERVE_TOP_DIRS = runtime / model / data / logs（旧目录整体保留，不进包）；
#   - PRESERVE_ROOT_FILES = config.yaml / .watermark_key（新包有则用新包，否则保留旧值）；
#   - shell-update.json 扁平 JSON：{ version, url, sha256, size, changelog }。
#
# 用法：
#   .\scripts\make_shell_update.ps1 -Version 2.2.1
#   .\scripts\make_shell_update.ps1 -Version 2.2.1 -Changelog "修复 XX"
#
# 产物（默认 OutDir = dist\shell-update\）：
#   app-v{ver}.zip / app-v{ver}.zip.sha256 / shell-update.json / SHA256SUMS.txt

[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$InstallRoot = '',
    [string]$OutDir = '',
    [string]$Changelog = '',
    [string]$RepoOwner = 'ReSerendipity',
    [string]$RepoName = 'TTS_MultiModel'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1')

if (-not $InstallRoot) {
    $InstallRoot = $root
}
if (-not $OutDir) {
    $OutDir = Join-Path $root 'dist\shell-update'
}
if (-not $Version) {
    $vj = Read-TTSMultiModelJson -Path (Join-Path $root 'version.json')
    $Version = [string]$vj.version
}
if ($Version -notmatch '^\d+\.\d+\.\d+') {
    throw "非法版本号: $Version"
}
if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot 'start_portable.py'))) {
    throw "安装根缺少 start_portable.py：$InstallRoot"
}

# ---------- staging：安装根内容（排除 PRESERVE_TOP_DIRS / 运行时状态 / 壳 / 开发杂物） ----------
$staging = Join-Path $OutDir "_staging-$Version"
Remove-TTSMultiModelTreeFast -Path $staging
New-Item -ItemType Directory -Path $staging -Force | Out-Null

$exclude = @(
    'runtime\*', 'model\*', 'data\*', 'logs\*',          # PRESERVE_TOP_DIRS（更新器保留旧目录，不进包）
    'cache\*', 'torch_compile_cache\*', 'outputs\*',     # 可再生的运行时状态
    'TTSMultiModel.exe',                                 # 壳本体不由应用增量更新
    '__pycache__\*', '*.pyc', '*.pyo', '.pytest_cache\*',
    '*.db', '*.db-wal', '*.db-shm', '*.log', '*.bak', '*.bak.*',
    '.csrf_secret', '.pii_key', '.history_hmac_key', '.integrity_hmac_secret',
    '.manifest_signing_key', '.watermark_key',
    'node_modules\*', '.venv\*', 'dist\*', 'build\*', 'desktop\*',
    'docs\*', 'tests\*', 'scripts\*', 'baselines\*', 'benchmarks\*', 'demo\*',
    'examples\*', 'perf\*', 'personas\*', 'reference_repos\*', 'screenshots\*', '_archive\*',
    '.git\*', '.github\*', '.gitignore', '.pre-commit-config.yaml', '.githooks\*',
    'coverage.xml', '.coverage', '.ruff_cache\*', '.mypy_cache\*', '*.egg-info\*', '.pytest_cache\*'
)
$stats = Copy-TTSMultiModelTree -Source $InstallRoot -Dest $staging -ExcludePatterns $exclude
Write-Host ("[make_shell_update] staging：{0} 文件 / {1}（排除 runtime/model/data/logs 等保留目录）" -f $stats.Files, (Format-TTSMultiModelSize $stats.Bytes))

# 包内必须含 version.json（更新器按它判定本地版本）
$verJson = Join-Path $staging 'version.json'
if (-not (Test-Path -LiteralPath $verJson)) {
    $srcVer = Join-Path $root 'version.json'
    if (Test-Path -LiteralPath $srcVer) {
        Copy-Item -LiteralPath $srcVer -Destination $verJson -Force
        Write-Host "[make_shell_update] 已将根 version.json 复制到增量包（本地版本识别所需）"
    } else {
        throw "安装根缺 version.json 且仓库根也无，无法生成增量包"
    }
}

# ---------- 打包 zip（System32 bsdtar，zip 容器，根 = 安装根直接子项） ----------
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$zip = Join-Path $OutDir "app-v$Version.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
$tar = Find-TTSMultiModelSystemTar
if (-not $tar) {
    throw '未找到系统 tar.exe（bsdtar），无法生成 zip'
}
$names = @(Get-ChildItem -LiteralPath $staging -Force | ForEach-Object { $_.Name })
$prev = Get-Location
try {
    Set-Location -LiteralPath $staging
    $res = Invoke-TTSMultiModelNative -Exe $tar -Arguments (@('-a', '-cf', $zip) + $names)
} finally {
    Set-Location -LiteralPath $prev
}
if ($res.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $zip)) {
    throw "zip 打包失败：$($res.Text)"
}
Remove-TTSMultiModelTreeFast -Path $staging

# ---------- 回读验证：zip 内必须含 version.json 与 app/ ----------
$tarList = Invoke-TTSMultiModelNative -Exe $tar -Arguments @('-tf', $zip)
$listText = $tarList.Text
foreach ($need in @('version.json', 'app/', 'start_portable.py', 'config.yaml')) {
    if ($listText -notmatch [regex]::Escape($need)) {
        throw "增量包回读验证失败：zip 内缺少 $need"
    }
}
foreach ($forbid in @('runtime', 'model', 'data', 'logs')) {
    if ($listText -match ("(?m)^" + [regex]::Escape($forbid) + "/")) {
        throw "增量包回读验证失败：zip 不应包含保留顶层目录 $forbid/"
    }
}

# ---------- sha256 + shell-update.json ----------
$sha = Get-TTSMultiModelFileSha256 -Path $zip
$size = [long](Get-Item -LiteralPath $zip).Length
$shaFile = "$zip.sha256"
[System.IO.File]::WriteAllText($shaFile, "$sha  $([System.IO.Path]::GetFileName($zip))`n", (New-Object System.Text.UTF8Encoding($false)))

$url = "https://github.com/$RepoOwner/$RepoName/releases/download/v$Version/app-v$Version.zip"
$flat = [ordered]@{
    version   = $Version
    url       = $url
    sha256    = $sha
    size      = $size
    changelog = $(if ($Changelog) { $Changelog } else { "TTSMultiModel v$Version 增量更新" })
}
Write-TTSMultiModelJson -Object $flat -Path (Join-Path $OutDir 'shell-update.json')

# ---------- SHA256SUMS（覆盖 zip + sha256 + json） ----------
$sums = Write-TTSMultiModelSha256Sums -Paths @($zip, $shaFile, (Join-Path $OutDir 'shell-update.json')) `
    -OutFile (Join-Path $OutDir 'SHA256SUMS.txt')

Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host (" 增量更新资产完成：{0}（{1}）" -f ([System.IO.Path]::GetFileName($zip)), (Format-TTSMultiModelSize $size)) -ForegroundColor Green
Write-Host ("   sha256: $sha") -ForegroundColor Green
Write-Host ("   shell-update.json: $url") -ForegroundColor Green
Write-Host (" 产物目录：$OutDir") -ForegroundColor Green
Write-Host ' 下一步：发布 v{ver} Release 时上传 app-v{ver}.zip + .sha256 + shell-update.json' -ForegroundColor Green
Write-Host '==============================================' -ForegroundColor Green
