#Requires -Version 5.1
<#
    scripts/build_slim_bundle.ps1
    ────────────────────────────────────────────────────────────────────────────
    构建「瘦包」安装数据卷：壳 + 应用代码 + 引导解释器（无 torch、无权重）。

    与 build_portable_bundle.ps1（便携分卷包，core/torch/model 三件套）的区别：
      · 本脚本只产出 **安装器数据卷**（NSIS setup.nsi 用），归档根是 `app\`，
        安装器解到 $INSTDIR 后校验 $INSTDIR\app\start_portable.py；
      · torch 与模型权重都不进包——用户在程序内「下载管理」页安装。

    产物（默认 $OutDir = dist\slim）：
      staging\app\start_portable.py      壳启动入口（打包态位于 app/ 根）
      staging\app\config.yaml            后端 PROJECT_ROOT 下的配置
      staging\app\app\integrated_app\…   Python 包（注意是 app/app 两层）
      staging\app\scripts\…              随包脚本（download_models.py 等）
      staging\app\bootstrap\python.exe   引导解释器（已剔除 torch）
      staging\TTSMultiModel.exe          壳
      TTSMultiModel-Data.7z.001…         数据卷（7z 原生 -v 分卷）
      SHA256SUMS.txt / manifest.json / upload-list.txt

    用法：
      .\scripts\build_slim_bundle.ps1                                  # 下载并准备引导解释器后打包
      .\scripts\build_slim_bundle.ps1 -RuntimeDir D:\WPy64-312101      # 复用已准备的运行时
      .\scripts\build_slim_bundle.ps1 -SkipDeps -SkipArchive           # 只组装 staging，快速验证布局

    注意：本文件含中文，必须存为 **UTF-8 with BOM**（PS 5.1 按 GBK 解码会碎语法）。
#>
[CmdletBinding()]
param(
    [ValidateSet('slim', 'full')]
    [string]$Mode = 'slim',
    [string]$Version = '',
    [string]$Root = '',
    [string]$OutDir = '',
    # 复用已解压/已装好依赖的 WinPython 根目录（省掉下载与 pip install）
    [string]$RuntimeDir = '',
    [string]$WinPythonUrl = 'https://github.com/winpython/winpython/releases/download/16.5.20250614/Winpython64-3.12.10.1dotb4.exe',
    [string]$WinPythonSha256 = '4061f0e936289ca1df48fc8e7357a4c30e6010f053ffd2f986f518a09bbf03e8',
    # 跳过非 torch 依赖安装（仅排障/布局验证用；正式包不能跳）
    [switch]$SkipDeps,
    # 壳 exe 来源；缺省依次尝试 dist\tauri-release\staging\TTSMultiModel.exe、scripts\installer\TTSMultiModel.exe
    [string]$ExeSrc = '',
    [long]$MaxPartBytes = 0,
    [switch]$SkipArchive
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1')

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not $OutDir) { $OutDir = Join-Path $Root 'dist\slim' }
if ($MaxPartBytes -le 0) { $MaxPartBytes = Get-TTSMultiModelDefaultMaxPart }

if ($Mode -ne 'slim') {
    throw "本脚本只实现 -Mode slim（瘦包）。完整便携包请用 scripts\build_portable_bundle.ps1。"
}

# ---------------------------------------------------------------- 版本 ----
if (-not $Version) {
    $vfile = Join-Path $Root 'version.json'
    if (Test-Path -LiteralPath $vfile) {
        $vj = Read-TTSMultiModelJson -Path $vfile
        $Version = [string]$vj.version
    }
}
if (-not $Version) { throw '无法确定版本号，请用 -Version 显式指定' }
if ($Version -notmatch '^\d+\.\d+\.\d+') { throw "非法版本号: $Version" }
Write-Host "[slim] 版本: $Version  输出目录: $OutDir" -ForegroundColor Cyan

# ------------------------------------------------ 引导解释器（无 torch） ----
function Get-SlimBootstrapRuntime {
    <#
        返回一个「只装非 torch 依赖」的解释器根目录。
        -RuntimeDir 给了就直接复用（仍会剔除 torch 并做 import 探测）；
        否则下载 WinPython → 解压 → pip 装 launcher\requirements-small.txt → 卸载 torch 家族。
    #>
    param([string]$ReuseDir)

    if ($ReuseDir) {
        if (-not (Test-Path -LiteralPath $ReuseDir)) { throw "-RuntimeDir 不存在: $ReuseDir" }
        $info = Resolve-TTSMultiModelRuntimeRoot -Path $ReuseDir
        Write-Host "  复用运行时: $($info.RuntimeRoot)"
        # 注意：复用路径同样要装 requirements-small——复用的 WinPython 上一个仓的
        # 依赖与本仓并不相同（真机踩过：复用 SeedVR2 的 rt，TTS 的 soundfile 缺失，
        # 引导态下 audio/generate/persona 等路由模块整体导入失败）。
        if ($SkipDeps) {
            Write-Host '  [SkipDeps] 跳过非 torch 依赖安装（仅用于布局验证，发布的包不能跳）' -ForegroundColor Yellow
            return $info.RuntimeRoot
        }
        $py = $info.PythonExe
        $req = Join-Path $Root 'launcher\requirements-small.txt'
        if (-not (Test-Path -LiteralPath $req)) { throw "缺少非 torch 依赖清单: $req" }
        Write-Host '  预装非 torch 依赖（launcher\requirements-small.txt）...'
        $res = Invoke-TTSMultiModelNative -Exe $py -Arguments @('-m', 'pip', 'install', '-r', $req, '--timeout', '300', '--retries', '3')
        if ($res.ExitCode -ne 0) {
            throw "依赖安装失败：$($res.Text | Select-Object -Last 6)"
        }
        return $info.RuntimeRoot
    }

    $work = Join-Path $OutDir '_wp'
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $exePath = Join-Path $work 'WinPython.exe'
    if (-not (Test-Path -LiteralPath $exePath)) {
        Write-Host "  下载 WinPython 安装器（约 1 GB）: $WinPythonUrl"
        Invoke-WebRequest -Uri $WinPythonUrl -OutFile $exePath -UseBasicParsing -TimeoutSec 1800
    }
    if ($WinPythonSha256) {
        $actual = (Get-TTSMultiModelFileSha256 -Path $exePath).ToLowerInvariant()
        if ($actual -ne $WinPythonSha256.ToLowerInvariant()) {
            throw ("WinPython 安装器 SHA256 不符（期望 {0}，实际 {1}）——下载损坏或上游被篡改" -f $WinPythonSha256, $actual)
        }
        Write-Host '  WinPython SHA256 校验通过'
    }
    $extractDir = Join-Path $work 'wp'
    if (-not (Test-Path -LiteralPath $extractDir)) {
        $sevenZip = Find-TTSMultiModelSevenZip
        if (-not $sevenZip) { throw '未找到 7za.exe，无法解压 WinPython 安装器' }
        Write-Host '  解压 WinPython ...'
        New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
        $res = Invoke-TTSMultiModelNative -Exe $sevenZip -Arguments @('x', $exePath, "-o$extractDir", '-y')
        if ($res.ExitCode -ne 0) { throw "解压 WinPython 失败，退出码 $($res.ExitCode)" }
    }
    $info = Resolve-TTSMultiModelRuntimeRoot -Path $extractDir
    $py = $info.PythonExe
    Write-Host "  解释器: $py"

    if (-not $SkipDeps) {
        $req = Join-Path $Root 'launcher\requirements-small.txt'
        if (-not (Test-Path -LiteralPath $req)) { throw "缺少非 torch 依赖清单: $req" }
        Write-Host '  预装非 torch 依赖（launcher\requirements-small.txt）...'
        Invoke-TTSMultiModelNative -Exe $py -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip') | Out-Null
        $res = Invoke-TTSMultiModelNative -Exe $py -Arguments @('-m', 'pip', 'install', '-r', $req, '--timeout', '300', '--retries', '3')
        if ($res.ExitCode -ne 0) {
            throw "依赖安装失败：$($res.Text.Split("`n")[-6..-1] -join ' | ')"
        }
        # descript-audiotools 元数据钉 protobuf<3.20（3.19.x 有 4 个 HIGH CVE），
        # 故 --no-deps 安装，依赖由 requirements-small 覆盖（同 build_portable_bundle.ps1）。
        $res = Invoke-TTSMultiModelNative -Exe $py -Arguments @('-m', 'pip', 'install', '--no-deps', 'descript-audiotools==0.7.2')
        if ($res.ExitCode -ne 0) { throw 'descript-audiotools 安装失败' }
        # funasr / audiotools 等会顺带把 CPU 版 torch 拉进来，必须显式卸掉：
        # 瘦包的引导解释器只能有 Web 依赖，torch 归「环境包」由用户装。
        Write-Host '  卸载 pip 顺带拉进来的 torch 家族...'
        Invoke-TTSMultiModelNative -Exe $py -Arguments @(
            '-m', 'pip', 'uninstall', '-y', 'torch', 'torchvision', 'torchaudio', 'torchgen'
        ) | Out-Null
    } else {
        Write-Host '  [SkipDeps] 跳过非 torch 依赖安装（仅用于布局验证，发布的包不能跳）' -ForegroundColor Yellow
    }
    return $info.RuntimeRoot
}

function Remove-SlimTorchFamily {
    <#
        剔除 WinPython 自带的 torch 家族（实测约 550 MB CPU 版）。
        只删目录不够稳：部分发行版没有 dist-info，故按名字前缀双路清理。
    #>
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)

    $patterns = @('torch', 'torchgen', 'torchvision', 'torchaudio', 'functorch', 'nvidia_*')
    $siteDirs = @(Get-ChildItem -LiteralPath $RuntimeRoot -Recurse -Force -Directory -Filter 'site-packages' -ErrorAction SilentlyContinue)
    $removed = 0
    $freed = [long]0
    foreach ($site in $siteDirs) {
        foreach ($pat in $patterns) {
            foreach ($item in @(Get-ChildItem -LiteralPath $site.FullName -Force -Filter $pat -ErrorAction SilentlyContinue)) {
                $size = 0
                if ($item.PSIsContainer) {
                    $size = (Get-ChildItem -LiteralPath $item.FullName -Recurse -Force -File -ErrorAction SilentlyContinue |
                        Measure-Object -Property Length -Sum).Sum
                } else {
                    $size = $item.Length
                }
                Remove-TTSMultiModelTreeFast -Path $item.FullName
                $removed++
                $freed += [long]$size
            }
        }
    }
    Write-Host ("  剔除 torch 家族：{0} 项 / {1}" -f $removed, (Format-TTSMultiModelSize $freed))
    return [pscustomobject]@{ Removed = $removed; Bytes = $freed }
}

$runtimeRoot = Get-SlimBootstrapRuntime -ReuseDir $RuntimeDir
$strip = Remove-SlimTorchFamily -RuntimeRoot $runtimeRoot
$pyExe = (Resolve-TTSMultiModelRuntimeRoot -Path $runtimeRoot).PythonExe
# 期望失败：torch 已摘干净。必须走 Invoke-TTSMultiModelNative，否则 stderr 的
# Traceback 在 EAP=Stop 下会被当成终止错误（CI 实测踩过）。
$probe = Invoke-TTSMultiModelNative -Exe $pyExe -Arguments @('-c', 'import torch')
if ($probe.ExitCode -eq 0) {
    throw 'torch 仍存在于引导解释器中：瘦包体积会失控，且壳的 RUNTIME.json 判据可能被绕过'
}
Write-Host '  探测确认：bootstrap 中 import torch 失败（符合预期）' -ForegroundColor Green

# ------------------------------------------------------------ 组装 staging ----
$staging = Join-Path $OutDir 'staging'
Remove-TTSMultiModelTreeFast -Path $staging
$appDir = Join-Path $staging 'app'
New-Item -ItemType Directory -Path $appDir -Force | Out-Null

$exclude = @(
    '__pycache__\*', '*.pyc', '*.pyo', '.pytest_cache\*', '.mypy_cache\*', '.ruff_cache\*',
    '*.log', '*.db', '*.db-wal', '*.db-shm', '*.bak', '*.bak.*',
    '.csrf_secret', '.pii_key', '.history_hmac_key', '.integrity_hmac_secret',
    '.manifest_signing_key', '.watermark_key', '.env', '.server_port', '*.egg-info\*',
    'node_modules\*', '.venv\*', 'build\*', 'dist\*'
)

$totals = [pscustomobject]@{ Files = 0; Bytes = [long]0 }
function Add-SlimPart {
    param([string]$Label, [string]$Source, [string]$Dest, [string[]]$ExtraExclude = @())
    $stats = Copy-TTSMultiModelTree -Source $Source -Dest $Dest -ExcludePatterns ($exclude + $ExtraExclude)
    $script:totals.Files += $stats.Files
    $script:totals.Bytes += $stats.Bytes
    Write-Host ("    · {0,-22} {1,6} 文件 / {2}" -f $Label, $stats.Files, (Format-TTSMultiModelSize $stats.Bytes))
}

# 1) Python 包（必须是 app/app 两层：PROJECT_ROOT 上溯三级正好是 <InstallDir>/app）
Add-SlimPart -Label 'app\integrated_app' -Source (Join-Path $Root 'app') -Dest (Join-Path $appDir 'app') `
    -ExtraExclude @('data\*', 'app\integrated_app\data\*', 'ffmpeg\*', '*.pem')

# 2) 壳启动入口 + 配置 + 版本 + 包元信息
#    pyproject.toml 必须落在数据卷根（安装态 = <InstallDir>\app\pyproject.toml）：
#    同族仓的 version 读取走 ``parents[2] / "pyproject.toml"``，少这一个文件版本号
#    会退化成 unknown，下载页/关于页一起显示 unknown。本仓 config.py 用 config.yaml
#    的 version，但保留该文件可让两套读取路径都成立。
foreach ($f in @('scripts\start_portable.py', 'config.yaml', 'version.json', 'pyproject.toml')) {
    $src = Join-Path $Root $f
    if (-not (Test-Path -LiteralPath $src)) { throw "缺少必需文件: $f" }
    Copy-Item -LiteralPath $src -Destination (Join-Path $appDir (Split-Path -Leaf $f)) -Force
    $totals.Files++
    $totals.Bytes += (Get-Item -LiteralPath $src).Length
}

# 3) 环境包清单（有才拷）：downloads_catalog.json 决定「安装运行环境」的下载地址与
$catalogSrc = Join-Path $Root 'app\downloads_catalog.json'
# 种子与后端读取同源：_catalog_path() = PROJECT_ROOT/downloads_catalog.json，
# 源码态 PROJECT_ROOT 即仓库 app/ 目录，故种子放 app\ 下（与 Image 仓一致）。
if (Test-Path -LiteralPath $catalogSrc) {
    Copy-Item -LiteralPath $catalogSrc -Destination (Join-Path $appDir 'downloads_catalog.json') -Force
    $totals.Files++
    $totals.Bytes += (Get-Item -LiteralPath $catalogSrc).Length
} else {
    Write-Host '  [warn] 未找到 downloads_catalog.json：安装环境包将在运行期报 NO_CATALOG' -ForegroundColor Yellow
}

# 4) 随包脚本（模型下载与校验，程序内「下载模型」直接调它）
$scriptsDest = Join-Path $appDir 'scripts'
New-Item -ItemType Directory -Path $scriptsDest -Force | Out-Null
foreach ($name in @('download_models.py', 'download_indextts2.py', 'download_indextts20.py')) {
    $src = Join-Path $Root "scripts\$name"
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $scriptsDest $name) -Force
        $totals.Files++
        $totals.Bytes += (Get-Item -LiteralPath $src).Length
    }
}

# 5) 引导解释器（无 torch）
$bootDest = Join-Path $appDir 'bootstrap'
$bootStats = Copy-TTSMultiModelTree -Source $runtimeRoot -Dest $bootDest -ExcludePatterns @(
    'pip-log.txt', '*.typecheck.log', 'qt.conf', 'WINPYTHON_*', 'apps\*', 'dev\*', 'notebooks\*', 'settings\*', 'scripts\*', 't\*'
)
$totals.Files += $bootStats.Files
$totals.Bytes += $bootStats.Bytes
Write-Host ("    · {0,-22} {1,6} 文件 / {2}" -f 'bootstrap', $bootStats.Files, (Format-TTSMultiModelSize $bootStats.Bytes))
if (-not (Test-Path -LiteralPath (Join-Path $bootDest 'python.exe'))) {
    # WinPython 真实布局是 <root>\python-3.12.x\python.exe，而壳找的是 <app>\bootstrap\python.exe
    $inner = Get-ChildItem -LiteralPath $bootDest -Recurse -Force -Filter 'python.exe' -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } | Select-Object -First 1
    if (-not $inner) { throw 'bootstrap 内找不到 python.exe，壳将无法启动后端' }
    Copy-Item -LiteralPath $inner.FullName -Destination (Join-Path $bootDest 'python.exe')
}

# 6) 运行态目录（壳/后端会在这里落日志、历史库、输出）
foreach ($d in @('data', 'logs', 'outputs', 'cache')) {
    $p = Join-Path $appDir $d
    New-Item -ItemType Directory -Path $p -Force | Out-Null
    $keep = Join-Path $p '.gitkeep'
    [System.IO.File]::WriteAllText($keep, '')
}

# 7) 壳 exe
if (-not $ExeSrc) {
    foreach ($cand in @(
        (Join-Path $Root 'dist\tauri-release\staging\TTSMultiModel.exe'),
        (Join-Path $Root 'scripts\installer\TTSMultiModel.exe')
    )) {
        if (Test-Path -LiteralPath $cand) { $ExeSrc = $cand; break }
    }
}
if (-not $ExeSrc -or -not (Test-Path -LiteralPath $ExeSrc)) {
    throw '找不到壳 exe：请用 -ExeSrc 指定（cargo build --release 产物需改名为 TTSMultiModel.exe）'
}
Copy-Item -LiteralPath $ExeSrc -Destination (Join-Path $staging 'TTSMultiModel.exe') -Force
Write-Host ("    · {0,-22} 1 文件 / {1}" -f 'TTSMultiModel.exe', (Format-TTSMultiModelSize (Get-Item -LiteralPath $ExeSrc).Length))

# 8) 布局断言（四者必须对齐，错一个安装后就起不来）
$mustHave = @(
    'app\start_portable.py',
    'app\config.yaml',
    'app\app\integrated_app\app_server.py',
    'app\app\integrated_app\download_service.py',
    'app\app\integrated_app\routes\downloads.py',
    'app\app\integrated_app\templates\downloads.html',
    'app\bootstrap\python.exe',
    'TTSMultiModel.exe'
)
foreach ($rel in $mustHave) {
    $p = Join-Path $staging $rel
    if (-not (Test-Path -LiteralPath $p)) { throw "staging 布局不完整，缺少: $rel" }
}
# 禁止项：引导态不得带 RUNTIME.json（壳会误判成 Full → 永不进下载页），也不得带权重
$forbidden = @('app\bootstrap\RUNTIME.json', 'app\model', 'app\checkpoints')
foreach ($rel in $forbidden) {
    if (Test-Path -LiteralPath (Join-Path $staging $rel)) { throw "staging 不应包含: $rel" }
}
$torchLeft = @(Get-ChildItem -LiteralPath (Join-Path $staging 'app\bootstrap') -Recurse -Force -Directory -Filter 'torch' -ErrorAction SilentlyContinue)
if ($torchLeft.Count -gt 0) { throw "staging\app\bootstrap 仍含 torch 目录: $($torchLeft[0].FullName)" }
Write-Host '[slim] staging 布局校验通过' -ForegroundColor Green

Write-Host ("[slim] staging 合计: {0} 文件 / {1}" -f $totals.Files, (Format-TTSMultiModelSize $totals.Bytes)) -ForegroundColor Cyan

if ($SkipArchive) {
    Write-Host '[slim] -SkipArchive：跳过数据卷打包' -ForegroundColor Yellow
    return
}

# ------------------------------------------------------------ 数据卷 ----
$sevenZip = Find-TTSMultiModelSevenZip
if (-not $sevenZip) { throw '未找到 7za.exe，无法生成数据卷' }
# PS 5.1 里空管道调 Remove-Item 会报 'missing path operand'（首包时无旧分卷必踩），
# 先收进数组再逐个删。
$oldVolumes = @(Get-ChildItem -LiteralPath $OutDir -Filter 'TTSMultiModel-Data.7z.*' -File -ErrorAction SilentlyContinue)
foreach ($old in $oldVolumes) { Remove-Item -LiteralPath $old.FullName -Force }
$archiveBase = Join-Path $OutDir 'TTSMultiModel-Data.7z'
$volMb = [math]::Floor($MaxPartBytes / 1MB)
# 归档根必须是 app/：安装器解到 $INSTDIR 后要能在 $INSTDIR\app\start_portable.py 找到入口
$arcArgs = @('a', '-t7z', "-v${volMb}M", '-mx=4', '-mmt=on', '-bd', '-bso0', '-bsp0', '-y', $archiveBase, 'app')
Write-Host "[slim] 打包数据卷（7z -v${volMb}M 分卷）..."
Assert-TTSMultiModelDiskSpace -Path $OutDir -NeededGb ([math]::Round(($totals.Bytes * 1.6) / 1GB, 1))
$prev = Get-Location
try {
    Set-Location -LiteralPath $staging
    $res = Invoke-TTSMultiModelNative -Exe $sevenZip -Arguments $arcArgs
} finally {
    Set-Location -LiteralPath $prev
}
if ($res.ExitCode -ne 0) { throw "7z 打包失败，退出码 $($res.ExitCode)：$($res.Text)" }

# 回读验证（分卷集完整性）
$tres = Invoke-TTSMultiModelNative -Exe $sevenZip -Arguments @('t', ($archiveBase + '.001'), '-y')
if ($tres.ExitCode -ne 0) { throw "分卷回读验证失败：$($tres.Text)" }

$volumes = @(Get-ChildItem -LiteralPath $OutDir -Filter 'TTSMultiModel-Data.7z.*' -File | Sort-Object Name)
if ($volumes.Count -eq 0) { throw '数据卷产物为空' }
$volTotal = [long]0
$volEntries = @()
foreach ($v in $volumes) {
    $hash = Get-TTSMultiModelFileSha256 -Path $v.FullName
    $volTotal += $v.Length
    $volEntries += [pscustomobject]@{ name = $v.Name; bytes = $v.Length; sha256 = $hash }
}
Write-TTSMultiModelSha256Sums -Paths @($volumes | ForEach-Object { $_.FullName }) -OutFile (Join-Path $OutDir 'SHA256SUMS.txt') | Out-Null

$manifest = [pscustomobject]@{
    product        = 'TTSMultiModel'
    version        = $Version
    mode           = 'slim'
    built_at       = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    archive_prefix = 'TTSMultiModel-Data.7z'
    archive_root   = 'app'
    install_layout = 'app/start_portable.py + app/app/integrated_app/* + app/bootstrap/python.exe'
    entry_point    = 'app/start_portable.py'
    bootstrap      = [pscustomobject]@{
        python        = (Split-Path -Leaf $runtimeRoot)
        torch_removed = $strip.Removed
        bytes_freed   = $strip.Bytes
    }
    staging_files  = $totals.Files
    staging_bytes  = $totals.Bytes
    volumes        = $volEntries
    volumes_bytes  = $volTotal
}
Write-TTSMultiModelJson -Path (Join-Path $OutDir 'manifest.json') -Object $manifest | Out-Null
[System.IO.File]::WriteAllLines(
    (Join-Path $OutDir 'upload-list.txt'),
    [string[]]@($volumes | ForEach-Object { $_.FullName }),
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host (" 瘦包数据卷完成：{0} 个分卷 / {1}" -f $volumes.Count, (Format-TTSMultiModelSize $volTotal)) -ForegroundColor Green
Write-Host (" 产物目录：$OutDir") -ForegroundColor Green
Write-Host ' 下一步：makensis -DAPP_VERSION=<ver> -DSRC_7ZA=<7za.exe> scripts\installer\setup.nsi' -ForegroundColor Green
Write-Host '==============================================' -ForegroundColor Green
