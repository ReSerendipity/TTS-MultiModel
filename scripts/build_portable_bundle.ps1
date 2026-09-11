#Requires -Version 5.1
# scripts/build_portable_bundle.ps1
# 构建「分卷压缩包」离线便携包，产物直接上传 GitHub Release（单文件恒 < 2 GiB）。
#
# 三个组件（默认全建；分卷体积仅供参考，最终以「体积门禁复核」输出的 total_gb 为准）：
#   core          应用代码 + 便携 Python 运行时（预装全部非 torch 依赖）  → 视 WinPython 体积
#   torch         PyTorch cu132 wheels（首次解包时离线 pip --no-index）  → ~2.8 GB
#   model         全部引擎权重（model/ + checkpoints/，内容寻址）        → 视权重体积
#
# 用法：
#   .\scripts\build_portable_bundle.ps1                       # 自动准备运行时并全量构建
#   .\scripts\build_portable_bundle.ps1 -Component core,model -RuntimeDir D:\WPy64-312101
#   .\scripts\build_portable_bundle.ps1 -SkipAutoPrepare -RuntimeDir ... -TorchWheelDir ...

[CmdletBinding()]
param(
    [string]$Version = '',
    [string]$Root = '',
    [string]$OutDir = '',
    [string]$StagingDir = '',
    [string]$RuntimeDir = '',
    [string]$TorchWheelDir = '',
    [string]$ModelDir = '',
    [string[]]$Component = @(),
    [ValidateSet('auto', '7z', 'zip')][string]$Format = 'auto',
    [long]$MaxPartBytes = 0,
    [double]$MinFreeGb = 0,
    [string]$WinPythonUrl = 'https://github.com/winpython/winpython/releases/download/16.5.20250614/Winpython64-3.12.10.1dotb4.exe',
    # 官方 release 资产 digest（GitHub API 实测，2026-08-30），防下载损坏/上游篡改；
    # 升级 WinPython 版本时必须同步更新 URL 与哈希。
    [string]$WinPythonSha256 = '4061f0e936289ca1df48fc8e7357a4c30e6010f053ffd2f986f518a09bbf03e8',
    # 上游单点兜底（云原生评估 P1-3）：官方 release 下线/网络不可达时，依次尝试
    # 「前缀 + 官方 URL」改写的镜像地址（gh-proxy.com 前缀已在 gpu-smoke.yml 的
    # 下载通道实测可用）。每个来源均须通过 WinPythonSha256 校验才会被采用，
    # 镜像返回的 HTML 错误页会被哈希门禁拦下。也可把 -WinPythonUrl 直接指向
    # 本地安装器文件（离线兜底），脚本会跳过下载、仍做哈希校验。
    [string[]]$WinPythonMirrorPrefixes = @('https://gh-proxy.com/'),
    [string]$TorchIndexUrl = 'https://download.pytorch.org/whl/cu132',
    # torch 三件套钉版（P1-3 复现性；2026-09-06 起交付轨与开发/lock 环境统一为 cu132，
    # 消除评估报告 P2-2 的双轨分叉）：升级时先查 pip index versions torch --index-url
    # <TorchIndexUrl> 再同步三处，并保持与 requirements-lock.txt 一致。
    [string]$TorchVersion = '2.13.0',
    [string]$TorchvisionVersion = '0.28.0',
    [string]$TorchaudioVersion = '2.11.0',
    # 可选 Authenticode 代码签名（P3）：提供代码签名证书 .pfx 时，随包分发的
    # .ps1 助手在生成 SHA256SUMS.txt 之前完成签名（否则清单哈希会失配）。
    [string]$SigningPfxPath = '',
    [string]$SigningPfxPassword = '',
    [switch]$SkipAutoPrepare,
    [switch]$SkipOfflineTorchCheck,
    [switch]$PrintModelFiles,
    [switch]$KeepStaging,
    [switch]$KeepArchive,
    [switch]$SkipSizeGate,
    # A 线：闭源组件 zip（scripts/build_closed_components.py 产物，Cython .pyd）。
    # 提供时把 security/ 的 .py 替换为 .pyd 并删除源码——发布包内水印实现不可读，
    # 且 A-6 重算清单发生在注入之后，.pyd 哈希进入清单（防"换 .py 绕过清单"）。
    [string]$ClosedComponentsZip = ''
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1')

$PortableRootName = 'TTSMultiModel-Portable'
$AllComponents = @('core', 'torch', 'model')

# ------------------------------------------------------- 组件内容版本 ----
function Get-TTSMultiModelContentVersion {
    <#
        组件内容版本（评估报告 P1-3a）：对一组文件做 sha256-of-sha256s，取前 12 位
        十六进制。同一内容恒得同一版本、与发布 tag 无关——升级发布时由此判定
        「哪些组件没变」（配合解包器 -ExistingInstall 的增量解包；后续 CI 侧按需
        下载也是基于它）。对大权重文件哈希约需 10-30 秒，构建期可接受。
    #>
    param([Parameter(Mandatory = $true)][string[]]$Paths)
    $parts = foreach ($p in $Paths) {
        Get-TTSMultiModelFileSha256 -Path $p
    }
    $joined = [System.Text.Encoding]::UTF8.GetBytes(($parts -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha.ComputeHash($joined)
    } finally {
        $sha.Dispose()
    }
    return (($digest | ForEach-Object { $_.ToString('x2') }) -join '').Substring(0, 12)
}

# ---------------------------------------------------------------- 组件规格 ----
function Get-TTSMultiModelComponentSpec {
    param([Parameter(Mandatory = $true)][string]$Name)
    switch ($Name) {
        'core' {
            return [pscustomobject]@{
                Id          = 'core'
                Title       = '应用与便携 Python 运行时（不含 torch）'
                Required    = $true
                Level       = 6
                Description = '应用源码白名单 + WinPython 3.12 便携解释器（已预装全部非 torch 依赖）+ 便携启动脚本。'
            }
        }
        'torch' {
            return [pscustomobject]@{
                Id          = 'torch'
                Title       = 'PyTorch CUDA 13.2 离线 wheels'
                Required    = $true
                Level       = 0
                Description = 'torch / torchvision / torchaudio 的 cu132 wheel（与开发环境 requirements-lock.txt 同轨）。解包脚本用 pip --no-index 离线装入便携解释器，全程不联网。'
            }
        }
        'model' {
            return [pscustomobject]@{
                Id          = 'model'
                Title       = '全部引擎权重（model/ + checkpoints/）'
                Required    = $true
                Level       = 1
                Description = 'model/ 与 checkpoints/ 目录全量权重（IndexTTS2/2.0、VoxCPM 等引擎），按目录内容寻址；体积大，按 1900MB 分卷（避坑指南第 7 条）。'
            }
        }
        default {
            throw "未知组件 '$Name'，可选：$($AllComponents -join ', ')"
        }
    }
}

# ------------------------------------------------------- 仓库/配置事实读取 ----
function Get-TTSMultiModelProjectVersion {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $pyproject = Join-Path $ProjectRoot 'pyproject.toml'
    if (Test-Path -LiteralPath $pyproject) {
        $m = Select-String -LiteralPath $pyproject -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($m) {
            return $m.Matches[0].Groups[1].Value
        }
    }
    throw "无法从 pyproject.toml 解析版本号，请显式传 -Version"
}

function Clear-TTSMultiModelYamlValue {
    param([Parameter(Mandatory = $true)][string]$Raw)
    $v = $Raw.Trim()
    $hash = $v.IndexOf('#')
    if ($hash -gt 0) {
        $v = $v.Substring(0, $hash).Trim()
    }
    $v = $v.Trim([char]0x22, [char]0x27)
    return $v
}

function Get-TTSMultiModelModelDirs {
    <#
        解析要随包分发的模型目录：优先仓库根下的 model/（TTS 引擎权重主目录），
        其次 checkpoints/（避坑指南第 7 条：模型在 checkpoints/，若加入安装包必须分卷）。
        只返回实际存在的目录；都不存在时返回空（调用方据此跳过模型组件）。
    #>
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $result = @()
    foreach ($d in @('model', 'checkpoints')) {
        $p = Join-Path $ProjectRoot $d
        if (Test-Path -LiteralPath $p -PathType Container) {
            $result += $p
        }
    }
    return $result
}

# --------------------------------------------------- 组件 payload 组装 ----

$CoreIncludeDirs = @('app', 'launcher')
$CoreIncludeFiles = @('config.yaml', 'pyproject.toml', 'requirements.txt', 'requirements-lock.txt',
    'LICENSE', 'README.md', 'SECURITY.md', 'CHANGELOG.md', 'start.bat', 'start_portable.py', 'version.json')
$CoreExcludePatterns = @(
    '__pycache__\*', '*.pyc', '*.pyo', '.pytest_cache\*',
    'data\*', 'app\integrated_app\data\*', 'logs\*.log', '*.db', '*.db-wal', '*.db-shm', '*.log',
    '.csrf_secret', '.pii_key', '.history_hmac_key', '.integrity_hmac_secret',
    '.manifest_signing_key', '.watermark_key',
    '.setup_state.json', '.torch_cache\*', '*.bak', '*.bak.*',
    'node_modules\*', '.venv\*', 'dist\*', 'build\*', 'cache\*', 'torch_compile_cache\*',
    'outputs\*', 'logs\*'
)

function New-TTSMultiModelCorePayload {
    param(
        [Parameter(Mandatory = $true)][string]$PayloadDir,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Runtime,
        [Parameter(Mandatory = $true)][string]$Ver,
        [Parameter(Mandatory = $false)][string]$TorchLabel = ''
    )
    $appDir = Join-Path $PayloadDir $PortableRootName
    New-Item -ItemType Directory -Path $appDir -Force | Out-Null
    $totalFiles = 0
    $totalBytes = 0
    foreach ($d in $CoreIncludeDirs) {
        $src = Join-Path $ProjectRoot $d
        if (-not (Test-Path -LiteralPath $src)) {
            Write-Warning "跳过不存在的源码目录 $d"
            continue
        }
        $stats = Copy-TTSMultiModelTree -Source $src -Dest (Join-Path $appDir $d) -ExcludePatterns $CoreExcludePatterns
        $totalFiles += $stats.Files
        $totalBytes += $stats.Bytes
        Write-Host ("    · {0,-12} {1,6} 文件 / {2}" -f $d, $stats.Files, (Format-TTSMultiModelSize $stats.Bytes))
    }
    foreach ($f in $CoreIncludeFiles) {
        $src = Join-Path $ProjectRoot $f
        if (-not (Test-Path -LiteralPath $src)) {
            continue
        }
        $stats = Copy-TTSMultiModelTree -Source $src -Dest $appDir -ExcludePatterns $CoreExcludePatterns
        $totalFiles += $stats.Files
        $totalBytes += $stats.Bytes
    }
    if ($Runtime) {
        $stats = Copy-TTSMultiModelTree -Source $Runtime -Dest (Join-Path $appDir (Split-Path -Leaf $Runtime)) `
            -ExcludePatterns @('pip-log.txt', '*.typecheck.log', 'qt.conf', 'WINPYTHON_*', 'apps\*', 'dev\*', 'notebooks\*', 'settings\*', 'data\*')
        Write-Host ("    · {0,-12} {1,6} 文件 / {2}" -f (Split-Path -Leaf $Runtime), $stats.Files, (Format-TTSMultiModelSize $stats.Bytes))
        $totalFiles += $stats.Files
        $totalBytes += $stats.Bytes
    }
    Set-Content -LiteralPath (Join-Path $appDir 'VERSION.txt') -Value "TTSMultiModel Portable $Ver" -Encoding ascii
    Write-TTSMultiModelStartScript -AppDir $appDir
    Write-TTSMultiModelReadme -AppDir $appDir -Ver $Ver -TorchLabel $TorchLabel
    return [pscustomobject]@{ Files = $totalFiles; Bytes = $totalBytes }
}

function Write-TTSMultiModelStartScript {
    param([Parameter(Mandatory = $true)][string]$AppDir)
    $bat = @(
        '@echo off'
        'setlocal enableextensions'
        'cd /d "%~dp0"'
        'set "PY="'
        'for /d %%P in ("%~dp0WPy64-*") do if exist "%%~fP\python\python.exe" set "PY=%%~fP\python\python.exe"'
        'if not defined PY for /d %%P in ("%~dp0WPy64-*") do for /d %%Q in ("%%~fP\python-*") do if exist "%%~fQ\python.exe" set "PY=%%~fQ\python.exe"'
        'if not defined PY for /d %%P in ("%~dp0WPy64-*") do if exist "%%~fP\python.exe" set "PY=%%~fP\python.exe"'
        'if defined PY set "PYTHONHOME="'
        'if not defined PY (set "PY=python"'
        'echo [WARN] 未找到便携解释器，回退使用系统 python)'
        'if not defined KMP_DUPLICATE_LIB_OK set "KMP_DUPLICATE_LIB_OK=TRUE"'
        'if not defined TORCHINDUCTOR_CACHE_DIR set "TORCHINDUCTOR_CACHE_DIR=%~dp0.torch_cache\inductor"'
        'echo 使用解释器: %PY%'
        'echo 启动 TTSMultiModel 于 http://127.0.0.1:7869'
        '"%PY%" app\clean_launch.py'
        'if errorlevel 1 pause'
    )
    [System.IO.File]::WriteAllLines((Join-Path $AppDir 'start-portable.bat'), [string[]]$bat, (New-Object System.Text.ASCIIEncoding))
}

function Write-TTSMultiModelReadme {
    param(
        [Parameter(Mandatory = $true)][string]$AppDir,
        [Parameter(Mandatory = $true)][string]$Ver,
        [Parameter(Mandatory = $false)][string]$TorchLabel = ''
    )
    $torchLine = if ($TorchLabel) {
        "  3. torch_wheels\（若已解包 torch 组件；随包版本：$TorchLabel，需较新的 NVIDIA 驱动支持其 CUDA 运行时）"
    } else {
        '  3. torch_wheels\（若已解包 torch 组件）'
    }
    $txt = @"
TTSMultiModel 便携离线包 v$Ver
================================================================

本目录由分卷压缩包解包而来，包含：
  1. 应用代码与配置（config.yaml）
  2. 便携 Python 解释器 WPy64-*（已预装全部非 torch 依赖，含 cryptography）
$torchLine
  4. model\ 与 checkpoints\（全部引擎权重，IndexTTS2/2.0、VoxCPM 等）

启动：双击 start-portable.bat，浏览器打开 http://127.0.0.1:7869
（或以 Tauri 桌面壳 TTSMultiModel.exe 启动，自动拉起后端并打开窗口）

torch 通常无需手动处理：下载目录里的 unpack_portable_bundle.ps1 在解包时已自动
用 .\torch_wheels 离线装好（全程不联网）。若当时用了 -SkipTorchInstall 或报错过，
在便携解释器上手动补装：

  WPy64-XXXX\python-3.12.10.amd64\python.exe -m pip install --no-index ^
      --find-links .\torch_wheels torch torchvision torchaudio

完整性：config.yaml 默认 enforce=true；清单由构建机 Ed25519 签名，改包即阻断启动。
模型说明：
  - 模型权重按目录内容寻址，权重不变则增量更新无需重下模型组件。
  - 想增删引擎权重，放入 model\ 或 checkpoints\ 后重新构建 model 组件即可。
"@
    [System.IO.File]::WriteAllText((Join-Path $AppDir 'README-PORTABLE.txt'), $txt, (New-Object System.Text.UTF8Encoding($false)))
}

function New-TTSMultiModelTorchPayload {
    param(
        [Parameter(Mandatory = $true)][string]$PayloadDir,
        [Parameter(Mandatory = $true)][string]$WheelDir
    )
    $appDir = Join-Path $PayloadDir $PortableRootName
    $dest = Join-Path $appDir 'torch_wheels'
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    $whl = @(Get-ChildItem -LiteralPath $WheelDir -Recurse -File -Filter '*.whl')
    if ($whl.Count -eq 0) {
        throw "torch 组件：$WheelDir 之下没有 .whl 文件（先跑 pip download 或去掉 torch 组件）"
    }
    foreach ($w in $whl) {
        # 硬链接优先：CI runner 上 wheels 目录与 staging 同盘，可省下 2.8GB 峰值占用。
        New-TTSMultiModelHardLink -SourceFile $w.FullName -LinkPath (Join-Path $dest $w.Name) | Out-Null
    }
    $bytes = ($whl | Measure-Object -Property Length -Sum).Sum
    return [pscustomobject]@{ Files = $whl.Count; Bytes = [long]$bytes }
}

function New-TTSMultiModelModelPayload {
    <#
        TTS 模型组件：整目录搬运（model/ 或 checkpoints/），不做逐文件白名单——
        引擎权重命名随引擎迭代变化，逐文件清单会与 config.yaml 漂移；目录级
        内容寻址（Get-TTSMultiModelContentVersion）已保证「权重不变则版本不变」。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$PayloadDir,
        [Parameter(Mandatory = $true)][string]$SourceModelDir
    )
    $appDir = Join-Path $PayloadDir $PortableRootName
    $dest = Join-Path $appDir (Split-Path -Leaf $SourceModelDir)
    if (Test-Path -LiteralPath $dest) {
        Remove-TTSMultiModelTreeFast -Path $dest
    }
    $stats = Copy-TTSMultiModelTree -Source $SourceModelDir -Dest $dest -ExcludePatterns @(
        '*.tmp', '*.bak', '*.bak.*', '__pycache__\*', '*.log'
    )
    foreach ($license in @('LICENSE', 'NOTICE')) {
        $lsrc = Join-Path (Split-Path -Parent $SourceModelDir) $license
        if (Test-Path -LiteralPath $lsrc) {
            Copy-Item -LiteralPath $lsrc -Destination (Join-Path $dest $license) -Force
        }
    }
    return [pscustomobject]@{ Files = $stats.Files; Bytes = $stats.Bytes }
}

# ------------------------------------------------------------ 自动准备 ----
function Start-TTSMultiModelRuntimePrepare {
    param(
        [Parameter(Mandatory = $true)][string]$WorkDir,
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Url
    )
    $sevenZip = Find-TTSMultiModelSevenZip
    $exePath = Join-Path $WorkDir 'WinPython.exe'
    if (-not (Test-Path -LiteralPath $exePath)) {
        # 多源兜底（云原生评估 P1-3）：-WinPythonUrl 指向本地安装器文件时直接采用
        # （离线兜底，仍过哈希门禁）；否则按「官方 URL → 镜像前缀改写 URL」逐源
        # 下载，每源下载后先过 SHA256 再采用，不符即删除换下一源（镜像可能返回
        # HTML 错误页，靠哈希门禁识别）。
        $verifiedInLoop = $false
        if (Test-Path -LiteralPath $Url -PathType Leaf) {
            Write-Host "  使用本地 WinPython 安装器：$Url"
            Copy-Item -LiteralPath $Url -Destination $exePath -Force
        } else {
            $candidates = @($Url)
            foreach ($prefix in $WinPythonMirrorPrefixes) {
                if ($prefix) { $candidates += ($prefix.TrimEnd('/') + '/' + $Url) }
            }
            $lastError = '未尝试任何来源'
            $chosen = ''
            foreach ($candidate in $candidates) {
                Write-Host "  下载 WinPython 便携解释器：$candidate"
                try {
                    Invoke-WebRequest -Uri $candidate -OutFile $exePath -UseBasicParsing -TimeoutSec 900
                } catch {
                    $lastError = $_.Exception.Message
                    if (Test-Path -LiteralPath $exePath) { Remove-Item -LiteralPath $exePath -Force }
                    Write-Host "    下载失败：$lastError —— 切换下一来源"
                    continue
                }
                if (-not $WinPythonSha256) { $chosen = $candidate; break }
                $actual = (Get-TTSMultiModelFileSha256 -Path $exePath).ToLowerInvariant()
                if ($actual -eq $WinPythonSha256.ToLowerInvariant()) {
                    $chosen = $candidate
                    $verifiedInLoop = $true
                    break
                }
                $lastError = "SHA256 不符（期望 {0}，实际 {1}）" -f $WinPythonSha256.ToLowerInvariant(), $actual
                Remove-Item -LiteralPath $exePath -Force
                Write-Host "    $lastError —— 切换下一来源"
            }
            if (-not (Test-Path -LiteralPath $exePath)) {
                throw ("WinPython 便携解释器全部 {0} 个来源不可用。离线兜底：把安装器预先下载到本地，再以 -WinPythonUrl <本地路径> 重试。最后错误：{1}" -f $candidates.Count, $lastError)
            }
            Write-Host "  WinPython 安装器就绪（来源：$chosen）"
        }
        if (-not $verifiedInLoop -and $WinPythonSha256) {
            # 本地路径采用 / 既有缓存文件的最终哈希门禁（循环内已验证的下载不重复哈希）
            $actual = (Get-TTSMultiModelFileSha256 -Path $exePath).ToLowerInvariant()
            if ($actual -ne $WinPythonSha256.ToLowerInvariant()) {
                throw ("WinPython 安装器 SHA256 不符（期望 {0}，实际 {1}）——下载损坏或上游被篡改" -f $WinPythonSha256, $actual)
            }
            Write-Host "  WinPython 安装器 SHA256 校验通过"
        }
    }
    $extractDir = Join-Path $WorkDir 'wp'
    if (-not (Test-Path -LiteralPath $extractDir)) {
        New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
        Write-Host "  解压 WinPython ..."
        if ($sevenZip) {
            $res = Invoke-TTSMultiModelNative -Exe $sevenZip -Arguments @('x', $exePath, "-o$extractDir", '-y')
            if ($res.ExitCode -ne 0) {
                throw "7z 解压 WinPython 失败，退出码 $($res.ExitCode)：$($res.Text.Split("`n")[-4..-1] -join ' | ')"
            }
        } else {
            $psi = Start-Process -FilePath $exePath -ArgumentList '/S', "/D=$extractDir" -PassThru -Wait
            if ($psi.ExitCode -ne 0) {
                throw "WinPython 自解压失败，退出码 $($psi.ExitCode)（可安装 7-Zip 后重试）"
            }
        }
    }
    $info = Resolve-TTSMultiModelRuntimeRoot -Path $extractDir
    $py = $info.PythonExe
    $runtimeRoot = $info.RuntimeRoot
    $reqSmall = Join-Path $ProjectRoot 'launcher\requirements-small.txt'
    if (Test-Path -LiteralPath $reqSmall) {
        Write-Host "  预装非 torch 依赖（launcher\requirements-small.txt）..."
        Invoke-TTSMultiModelNative -Exe $py -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip') | Out-Null
        $res = Invoke-TTSMultiModelNative -Exe $py -Arguments @('-m', 'pip', 'install', '-r', $reqSmall, '--timeout', '300', '--retries', '3')
        if ($res.ExitCode -ne 0) {
            throw "便携解释器依赖安装失败：$($res.Text.Split("`n")[-6..-1] -join ' | ')"
        }
    }
    Write-Host "  摘除 torch 家族（归入独立 torch 组件）..."
    Invoke-TTSMultiModelNative -Exe $py -Arguments @('-m', 'pip', 'uninstall', '-y', 'torch', 'torchvision', 'torchaudio') | Out-Null
    # 期望 import 失败（证明已摘干净），因此必须走 Invoke-TTSMultiModelNative：
    # 直接 `& python -c` 在 EAP=Stop 下会把 stderr 的 Traceback 升级成终止错误（CI 实测踩过）。
    $probe = Invoke-TTSMultiModelNative -Exe $py -Arguments @('-c', 'import torch')
    if ($probe.ExitCode -eq 0) {
        throw "torch 仍存在于便携解释器中，core 组件会体积失控"
    }
    $link = Join-Path $runtimeRoot 'python'
    if (Test-Path -LiteralPath $link) {
        $item = Get-Item -LiteralPath $link -Force
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            Write-Host "  移除 python 目录联接（归档器不跟随 reparse point）..."
            [System.IO.Directory]::Delete($link)
        }
    }
    return $runtimeRoot
}

function Start-TTSMultiModelTorchWheelPrepare {
    param(
        [Parameter(Mandatory = $true)][string]$WheelDir,
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$IndexUrl
    )
    New-Item -ItemType Directory -Path $WheelDir -Force | Out-Null
    if (@(Get-ChildItem -LiteralPath $WheelDir -File -Filter '*.whl').Count -gt 0) {
        Write-Host "  复用已有 wheels：$WheelDir"
        return
    }
    Write-Host "  pip download torch 家族（含传递依赖，约 2.8 GB，需联网）..."
    # 关键：不能加 --no-deps。离线安装用 pip --no-index --find-links，torch 的传递依赖
    # （filelock / fsspec / jinja2 / networkx / sympy / typing-extensions）必须一起落盘，
    # 否则解包端 pip 会因找不到依赖而失败（旧 exe 安装器正是踩了这个坑，已删除）。
    # 双源（2026-09-06，T9 统一 cu132 时实测）：torch/torchvision 从 CUDA 索引取 +cu132
    # 轮；torchaudio 从 PyPI 取 CPU 轮——cu132 索引实测无 torchaudio 轮子（pip index
    # versions 报 No matching distribution，与 pyproject.toml [tool.uv.sources] 同口径），
    # PyPI CPU 轮与 torch+cu132 搭配正常（本机 venv 与 requirements-lock.txt 同此组合），
    # 且其 METADATA 不声明 torch 依赖，不会拉入第二个 torch 轮污染离线目录。
    $resTorch = Invoke-TTSMultiModelNative -Exe $PythonExe -Arguments @(
        '-m', 'pip', 'download',
        "torch==$TorchVersion", "torchvision==$TorchvisionVersion",
        '--index-url', $IndexUrl, '-d', $WheelDir, '--timeout', '300', '--retries', '3'
    )
    if ($resTorch.ExitCode -ne 0) {
        throw "pip download torch/torchvision wheels 失败：$($resTorch.Text.Split("`n")[-8..-1] -join ' | ')"
    }
    $resAudio = Invoke-TTSMultiModelNative -Exe $PythonExe -Arguments @(
        '-m', 'pip', 'download', "torchaudio==$TorchaudioVersion",
        '-d', $WheelDir, '--timeout', '300', '--retries', '3'
    )
    if ($resAudio.ExitCode -ne 0) {
        throw "pip download torchaudio wheel 失败（PyPI 源）：$($resAudio.Text.Split("`n")[-8..-1] -join ' | ')"
    }
}

function Test-TTSMultiModelTorchOfflineInstallable {
    <#
        构建期真实验证：用便携解释器对 wheels 目录做一次 --no-index 解析，
        确认「离线可装」，而不是等用户解包时才发现装不上。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$WheelDir
    )
    $res = Invoke-TTSMultiModelNative -Exe $PythonExe -Arguments @(
        '-m', 'pip', 'install', '--no-index', '--find-links', $WheelDir,
        '--dry-run', '--ignore-installed', 'torch', 'torchvision', 'torchaudio'
    )
    if ($res.ExitCode -ne 0) {
        $lines = @($res.Text.Split("`n"))
        $text = ($lines[([math]::Max(0, $lines.Count - 12))..($lines.Count - 1)]) -join "`n  "
        throw "离线可装性验证失败（wheels 缺依赖或平台不匹配）：`n  $text"
    }
    Write-Host '  离线可装性验证通过（pip --no-index --dry-run）' -ForegroundColor Green
}

function Invoke-TTSMultiModelAuthenticodeSign {
    <#
        可选 Authenticode 签名（P3）：对随包分发的 .ps1 助手脚本签名，需提供
        代码签名证书 .pfx。必须在 SHA256SUMS.txt 生成之前调用——签名会改变
        文件字节，先签名后生成清单才能保证哈希一致。
    #>
    param(
        [Parameter(Mandatory = $true)][string[]]$Files,
        [Parameter(Mandatory = $true)][string]$PfxPath,
        [Parameter(Mandatory = $true)][string]$PfxPassword
    )
    $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if (-not $signtool) {
        $kits = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin' -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
        foreach ($kit in $kits) {
            $candidate = Join-Path $kit.FullName 'x64\signtool.exe'
            if (Test-Path -LiteralPath $candidate) { $signtool = Get-Item $candidate; break }
        }
    }
    if (-not $signtool) {
        throw "signtool.exe 不可用：请安装 Windows SDK 或把 signtool 加入 PATH"
    }
    foreach ($f in $Files) {
        if (-not (Test-Path -LiteralPath $f)) { continue }
        $res = Invoke-TTSMultiModelNative -Exe $signtool.FullName -Arguments @(
            'sign', '/fd', 'SHA256', '/f', $PfxPath, '/p', $PfxPassword, $f
        )
        if ($res.ExitCode -ne 0) {
            throw "Authenticode 签名失败 $(Split-Path -Leaf $f)：$($res.Text.Split("`n")[-5..-1] -join ' | ')"
        }
        Write-Host ("  Authenticode 已签名：{0}" -f (Split-Path -Leaf $f))
    }
}

# ------------------------------------------------------------------ 主流程 ----
if (-not $Root) {
    $Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}
$Root = (Resolve-Path -LiteralPath $Root).Path
if (-not $Version) {
    $Version = Get-TTSMultiModelProjectVersion -ProjectRoot $Root
}
if (-not $OutDir) {
    $OutDir = Join-Path $Root 'dist\bundles'
}
if (-not $StagingDir) {
    $StagingDir = Join-Path $Root 'dist\portable-staging'
}
if (-not $MaxPartBytes) {
    $MaxPartBytes = Get-TTSMultiModelDefaultMaxPart
}
$Limit = Get-TTSMultiModelGithubAssetLimit
if ($MaxPartBytes -gt $Limit) {
    throw "-MaxPartBytes ($MaxPartBytes) 超过 GitHub 单文件上限 ($Limit)"
}
if ($Component.Count -eq 0) {
    $Component = $AllComponents
}
foreach ($c in $Component) {
    if ($AllComponents -notcontains $c) {
        throw "未知组件 '$c'，可选：$($AllComponents -join ', ')"
    }
}

$modelDirs = @(Get-TTSMultiModelModelDirs -ProjectRoot $Root)
if (-not $ModelDir) {
    $ModelDir = if ($modelDirs.Count -gt 0) { $modelDirs[0] } else { Join-Path $Root 'model' }
}
if ($PrintModelFiles) {
    # 供 CI 复用同一份模型目录事实来源，避免工作流里再硬编码一遍。
    Write-Output ($modelDirs -join "`n")
    exit 0
}

Write-Host ''
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " TTSMultiModel 便携分卷包构建 v$Version" -ForegroundColor Cyan
Write-Host " 组件：$($Component -join ', ')" -ForegroundColor Cyan
Write-Host " 分卷上限：$(Format-TTSMultiModelSize $MaxPartBytes)（GitHub 单文件上限 $(Format-TTSMultiModelSize $Limit)）" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
New-Item -ItemType Directory -Path $StagingDir -Force | Out-Null

# ------------------------------------------------------ 磁盘空间预检 ----
function Get-TTSMultiModelPeakGbEstimate {
    <#
        峰值占用 ≈ 所有组件未压缩载荷之和 × 1.3。
        构成：staging（同盘时是硬链接，几乎为 0）+ 归档 + 归档切出的全部分卷
        （归档在切完前与分卷共存，且前序组件的分卷会累积在 OutDir）。
    #>
    param(
        [string]$Runtime,
        [string]$Wheels,
        [string]$ModelDirectory,
        [string[]]$Components
    )
    $raw = [long]0
    if ($Components -contains 'core' -and $Runtime -and (Test-Path -LiteralPath $Runtime)) {
        $raw += [long](Get-ChildItem -LiteralPath $Runtime -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    }
    if ($Components -contains 'torch' -and $Wheels -and (Test-Path -LiteralPath $Wheels)) {
        $raw += [long](Get-ChildItem -LiteralPath $Wheels -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    }
    if ($ModelDirectory -and (Test-Path -LiteralPath $ModelDirectory)) {
        # TTS 模型组件为整目录（model/ 或 checkpoints/），按目录体积计入预检。
        $raw += [long](Get-ChildItem -LiteralPath $ModelDirectory -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    }
    return [math]::Round(($raw * 1.3) / 1GB, 1)
}

$estimatedPeak = Get-TTSMultiModelPeakGbEstimate -Runtime $RuntimeDir -Wheels $TorchWheelDir `
    -ModelDirectory $ModelDir -Components $Component
# 自动准备模式下这两类来源此刻还不存在，按已知量补估，否则预检会严重低估：
# WinPython 解压后约 2 GB、cu132 torch wheels 约 3 GB，且都会与产物同时驻留到构建结束。
if ($Component -contains 'core' -and -not $RuntimeDir -and -not $SkipAutoPrepare) {
    $estimatedPeak += 2.0
}
if ($Component -contains 'torch' -and -not $TorchWheelDir -and -not $SkipAutoPrepare) {
    $estimatedPeak += 3.0
}
$requiredFree = if ($MinFreeGb -gt 0) { [double]$MinFreeGb } else { [math]::Max($estimatedPeak, 2.0) }
Write-Host "`n[预检] 磁盘空间"
Assert-TTSMultiModelDiskSpace -Path $OutDir -NeededGb $requiredFree | Out-Null
if ((Split-Path -Qualifier $StagingDir) -ne (Split-Path -Qualifier $OutDir)) {
    Assert-TTSMultiModelDiskSpace -Path $StagingDir -NeededGb $requiredFree | Out-Null
}
Write-Host ("  预估峰值 {0} GB（staging 与 OutDir 同盘时用硬链接，可显著降低实际占用）" -f $estimatedPeak)

$needRuntime = ($Component -contains 'core')
$needWheels = ($Component -contains 'torch')
if ($needRuntime -and -not $RuntimeDir -and $SkipAutoPrepare) {
    throw "core 组件需要 -RuntimeDir（已解压且未装 torch 的便携解释器目录）"
}
if ($needWheels -and -not $TorchWheelDir -and $SkipAutoPrepare) {
    throw "torch 组件需要 -TorchWheelDir（含 cu132 wheels 的目录）"
}

$prepDir = Join-Path $StagingDir '_prepare'
New-Item -ItemType Directory -Path $prepDir -Force | Out-Null
if ($needRuntime -and -not $RuntimeDir) {
    Write-Host "`n[准备] 便携 Python 运行时"
    $RuntimeDir = Start-TTSMultiModelRuntimePrepare -WorkDir $prepDir -ProjectRoot $Root -Url $WinPythonUrl
}
if ($needWheels -and -not $TorchWheelDir) {
    if (-not $RuntimeDir) {
        throw "torch 组件需要便携解释器执行 pip download：请一并构建 core，或传 -TorchWheelDir"
    }
    $pyInfo = Resolve-TTSMultiModelRuntimeRoot -Path $RuntimeDir
    $TorchWheelDir = Join-Path $prepDir 'torch_wheels'
    Write-Host "`n[准备] torch wheels"
    Start-TTSMultiModelTorchWheelPrepare -WheelDir $TorchWheelDir -PythonExe $pyInfo.PythonExe -IndexUrl $TorchIndexUrl
}
if ($TorchWheelDir -and $RuntimeDir -and -not $SkipOfflineTorchCheck) {
    Write-Host "`n[验证] torch wheels 在便携解释器上是否可离线安装"
    $probe = Resolve-TTSMultiModelRuntimeRoot -Path $RuntimeDir
    $alive = Invoke-TTSMultiModelNative -Exe $probe.PythonExe -Arguments @('-c', "print('ok')")
    if ($alive.ExitCode -ne 0 -or ($alive.Text -notmatch 'ok')) {
        Write-Warning "$($probe.PythonExe) 不是可执行的解释器，跳过离线可装性验证（真实构建必须通过此检查）"
    } else {
        Test-TTSMultiModelTorchOfflineInstallable -PythonExe $probe.PythonExe -WheelDir $TorchWheelDir
    }
}


# 清理 WinPython Scripts 下 .py 脚本的 shebang 绝对路径：
# WinPython 自带脚本（jp.py 等）首行 #!硬编码构建机临时路径，
# 打包后成为本机路径残留，被门禁 3 no-local-path-residue 拦下。统一重写为相对名。
if ($RuntimeDir) {
    $scriptsDir = Join-Path $RuntimeDir 'python\Scripts'
    if (Test-Path $scriptsDir) {
        $shebangFixed = 0
        Get-ChildItem $scriptsDir -Filter '*.py' -File -Force | ForEach-Object {
            $pyLines = [System.IO.File]::ReadAllLines($_.FullName)
            if ($pyLines.Count -gt 0 -and $pyLines[0] -match '^#!.+python\.exe') {
                $pyLines[0] = '#!python.exe'
                [System.IO.File]::WriteAllLines($_.FullName, $pyLines, [System.Text.UTF8Encoding]::new($false))
                $shebangFixed++
            }
        }
        if ($shebangFixed -gt 0) {
            Write-Host "  [清理] WinPython shebang 本机路径重写：$shebangFixed 个脚本"
        }
    }
}

$components = @()
$allParts = @()
foreach ($id in $Component) {
    $spec = Get-TTSMultiModelComponentSpec -Name $id
    Write-Host "`n[$id] $($spec.Title)" -ForegroundColor Yellow
    $payload = Join-Path $StagingDir "$id-payload"
    Remove-TTSMultiModelTreeFast -Path $payload
    New-Item -ItemType Directory -Path $payload -Force | Out-Null

    switch ($id) {
        'core' {
            $variant = if ($TorchIndexUrl -match '/whl/([^/]+)/?$') { $Matches[1] } else { 'cpu' }
            $built = New-TTSMultiModelCorePayload -PayloadDir $payload -ProjectRoot $Root -Runtime $RuntimeDir -Ver $Version `
                -TorchLabel "torch $TorchVersion+$variant"

            # 打破硬链接（GOTCHAS #91，2026-09-09 实测）：Copy-TTSMultiModelTree 对同盘文件优先
            # 硬链接，payload 与源码目录共享 inode；后续 enforce 翻转 / 清单重算 / 签名写入
            # 都是就地改写，会穿透回源码目录（曾把源码 config.yaml 翻成 true、清单重算成
            # .pyd 版）。对将被改写的文件先 Copy→Remove→Move 换成独立 inode。
            foreach ($_t in @(
                (Join-Path $payload (Join-Path $PortableRootName 'config.yaml')),
                (Join-Path $payload (Join-Path $PortableRootName 'app\integrated_app\security\integrity_manifest.json')),
                (Join-Path $payload (Join-Path $PortableRootName 'app\integrated_app\security\integrity_manifest.json.sig')),
                (Join-Path $payload (Join-Path $PortableRootName 'app\integrated_app\security\integrity_manifest.json.sig.ed25519'))
            )) {
                if (Test-Path -LiteralPath $_t) {
                    $tmp = "$_t.breaklink"
                    Copy-Item -LiteralPath $_t -Destination $tmp -Force
                    Remove-Item -LiteralPath $_t -Force
                    Move-Item -LiteralPath $tmp -Destination $_t -Force
                }
            }

            # A 线闭源注入：-ClosedComponentsZip 提供 Cython 编译的 security .pyd 包时，
            # 解压覆盖 security/ 并删除对应 .py 源码（水印实现不随包分发，防普通用户
            # 按源码注释定点拆水印；算法本体仍按 Apache-2.0 在公开仓库可查）。
            if ($ClosedComponentsZip) {
                $closedZip = Resolve-Path -LiteralPath $ClosedComponentsZip -ErrorAction SilentlyContinue
                if (-not $closedZip) { throw "闭源组件 zip 不存在: $ClosedComponentsZip" }
                $securityPayload = Join-Path $payload (Join-Path $PortableRootName 'app\integrated_app\security')
                $tmpExtract = Join-Path $StagingDir 'closed-components-extract'
                Remove-TTSMultiModelTreeFast -Path $tmpExtract
                New-Item -ItemType Directory -Path $tmpExtract -Force | Out-Null
                Expand-Archive -LiteralPath $closedZip.Path -DestinationPath $tmpExtract -Force
                $replaceManifest = Join-Path $tmpExtract '.closed_replacements.json'
                $replaceMap = @{}
                if (Test-Path -LiteralPath $replaceManifest) {
                    $replaceMap = Get-Content -LiteralPath $replaceManifest -Raw -Encoding UTF8 | ConvertFrom-Json
                }
                foreach ($f in Get-ChildItem -LiteralPath $tmpExtract -File) {
                    Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $securityPayload $f.Name) -Force
                }
                foreach ($prop in $replaceMap.PSObject.Properties) {
                    $pyName = [string]$prop.Value
                    $pyPath = Join-Path $securityPayload $pyName
                    if (Test-Path -LiteralPath $pyPath) {
                        Remove-Item -LiteralPath $pyPath -Force
                        Write-Host "  [A 线] 已移除源码: security/$pyName"
                    }
                }
                Write-Host "  [A 线] 闭源组件已注入 security/（$($replaceMap.PSObject.Properties.Count) 个模块）"
            }

            # B-8：发布版默认强制完整性校验（enforce=true）。新装/重装用户直接获得
            # 防篡改阻断；老用户更新后由更新器保留其 config.yaml（可自行改回）。
            $payloadConfig = Join-Path $payload (Join-Path $PortableRootName 'config.yaml')
            if (Test-Path -LiteralPath $payloadConfig) {
                $cfgText = Get-Content -LiteralPath $payloadConfig -Raw -Encoding UTF8
                $cfgText = $cfgText -replace '(?m)^(\s*enforce:\s*)false(\s*)$', '${1}true${2}'
                [System.IO.File]::WriteAllText($payloadConfig, $cfgText, (New-Object System.Text.UTF8Encoding($false)))
            } else {
                Write-Warning "payload 缺少 config.yaml，跳过 enforce 注入"
            }

            # A-6：构建期重算完整性清单并 Ed25519 签名——清单哈希必须基于实际分发
            # 文件（闭源注入/去注释后哈希已变），且签名由构建机私钥完成，用户端
            # 用内置公钥验签（D/P2-3；私钥 data/.manifest_signing_key 不进包）。
            $payloadAppDir = Join-Path $payload (Join-Path $PortableRootName 'app\integrated_app')
            # A-6 签名步骤优先用仓库 .venv（已装 cryptography，Ed25519 依赖）；
            # CI runner 无 .venv 时回退系统 python（portable-release.yml 构建 step 已补装）。
            $repoPy = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
            $py = if (Test-Path -LiteralPath $repoPy) { (Resolve-Path -LiteralPath $repoPy).Path } else { 'python' }
            $gen = Invoke-TTSMultiModelNative -Exe $py -Arguments @('scripts\generate_integrity_manifest.py', '--app-dir', $payloadAppDir)
            if ($gen.ExitCode -ne 0) {
                throw "A-6 重算便携包完整性清单失败: $($gen.Text)"
            }
            $payloadManifest = Join-Path $payloadAppDir 'security\integrity_manifest.json'
            $sig = Invoke-TTSMultiModelNative -Exe $py -Arguments @('scripts\sign_integrity_manifest.py', '--manifest', $payloadManifest)
            if ($sig.ExitCode -ne 0) {
                throw "A-6 便携包清单签名失败: $($sig.Text)"
            }
            Write-Host "  [A-6/B-8] 已重算清单并签名（Ed25519）；config.yaml enforce=true"
            # A-6 诊断：payload 内置公钥必须与仓库公钥一致，否则用户端验签必然失败
            # （GOTCHAS #98：CI 冒烟曾在验签失败，payload 公钥来源需与签名私钥配套）。
            $pubPayload = Join-Path $payloadAppDir 'security\manifest_signing_public_key.pem'
            $pubRepo = Join-Path $Root 'app\integrated_app\security\manifest_signing_public_key.pem'
            if ((Test-Path -LiteralPath $pubPayload) -and (Test-Path -LiteralPath $pubRepo)) {
                $h1 = (Get-FileHash -LiteralPath $pubPayload -Algorithm SHA256).Hash
                $h2 = (Get-FileHash -LiteralPath $pubRepo -Algorithm SHA256).Hash
                if ($h1 -eq $h2) {
                    Write-Host "  [A-6] 诊断：payload 公钥 == 仓库公钥（SHA256 $h1）"
                } else {
                    throw "A-6 诊断失败：payload 公钥与仓库公钥不一致（$h1 vs $h2），验签必败"
                }
            } else {
                Write-Warning "A-6 诊断：公钥文件缺失（payload=$pubPayload, repo=$pubRepo）"
            }
        }
        'torch' {
            $built = New-TTSMultiModelTorchPayload -PayloadDir $payload -WheelDir $TorchWheelDir
        }
        'model' {
            if (-not $ModelDir -or -not (Test-Path -LiteralPath $ModelDir)) {
                throw "model 组件需要存在的模型目录（仓库 model/ 或 checkpoints/），或传 -ModelDir"
            }
            $built = New-TTSMultiModelModelPayload -PayloadDir $payload -SourceModelDir $ModelDir
        }
    }
    Write-Host ("  载荷：{0} 文件 / {1}（未压缩）" -f $built.Files, (Format-TTSMultiModelSize $built.Bytes))

    Assert-TTSMultiModelNoForbiddenPayload -Path $payload

    $archiveBase = Join-Path $StagingDir "TTSMultiModel-Portable-v$Version-win-x64-$id"
    $archive = New-TTSMultiModelArchive -SourceDir $payload -OutFile $archiveBase -Format $Format -Level $spec.Level
    Write-Host ("  归档：{0} → {1}（{2}）" -f $archive.Format, (Format-TTSMultiModelSize $archive.Bytes), (Split-Path -Leaf $archive.Tool))

    $volumes = @(Split-TTSMultiModelFileIntoVolumes -File $archive.Path -MaxBytes $MaxPartBytes)
    $volCount = $volumes.Count
    foreach ($v in $volumes) {
        Move-Item -LiteralPath $v.Path -Destination (Join-Path $OutDir $v.Name) -Force
        $allParts += (Join-Path $OutDir $v.Name)
        Write-Host ("    卷 {0}/{1}：{2}  {3}" -f $v.Index, $volCount, (Format-TTSMultiModelSize $v.Bytes), $v.Name)
    }
    if (-not $KeepArchive) {
        Remove-Item -LiteralPath $archive.Path -Force
    }
    # 载荷清单：小集合逐项记录（模型/wheels 组件），大集合只记总量
    # （core 含整个 WinPython 树，逐项写入会让 manifest.json 膨胀到数 MB）。
    $payloadRoot = (Resolve-Path -LiteralPath $payload).Path
    $payloadEnum = @(Get-ChildItem -LiteralPath $payload -Recurse -File -Force)
    $payloadFiles = @()
    $payloadTruncated = $false
    if ($payloadEnum.Count -le 1000) {
        foreach ($f in $payloadEnum) {
            $payloadFiles += [pscustomobject]@{
                path  = (ConvertTo-TTSMultiModelRelative -Root $payloadRoot -Full $f.FullName).Replace('\', '/')
                bytes = [long]$f.Length
            }
        }
    } else {
        $payloadTruncated = $true
        foreach ($f in @('config.yaml', 'app\clean_launch.py', 'README-PORTABLE.txt', 'start-portable.bat')) {
            $probePath = Join-Path $payload (Join-Path $PortableRootName $f)
            if (Test-Path -LiteralPath $probePath) {
                $payloadFiles += [pscustomobject]@{
                    path  = "$PortableRootName/$f".Replace('\', '/')
                    bytes = [long](Get-Item -LiteralPath $probePath).Length
                }
            }
        }
    }
    $volInfos = @($volumes | ForEach-Object {
            [pscustomobject]@{
                index  = $_.Index
                file   = $_.Name
                bytes  = $_.Bytes
                sha256 = $_.Sha256
            }
        })
    # 组件内容版本（P1-3a）：core 跟应用版本；torch 跟「钉版 + 索引变体」；
    # 模型组件跟权重内容（sha256-of-sha256s 前 12 位）——权重不变则版本不变，
    # 升级时据此复用旧安装/旧分卷。纯增量字段，schema 保持 1。
    $compVersion = switch ($id) {
        'core' { $Version }
        'torch' {
            $variant = if ($TorchIndexUrl -match '/whl/([^/]+)/?$') { $Matches[1] } else { 'cpu' }
            "$TorchVersion+$variant"
        }
        'model' {
            $modelPayloadRoot = Join-Path $payloadRoot (Join-Path $PortableRootName 'model')
            if (Test-Path -LiteralPath $modelPayloadRoot) {
                $modelFiles = @(Get-ChildItem -LiteralPath $modelPayloadRoot -Recurse -File -Force | ForEach-Object { $_.FullName })
                if ($modelFiles.Count -gt 0) {
                    Get-TTSMultiModelContentVersion -Paths $modelFiles
                } else {
                    'empty'
                }
            } else {
                'missing'
            }
        }
    }
    $components += [pscustomobject]@{
        id                       = $spec.Id
        version                  = $compVersion
        title                    = $spec.Title
        description              = $spec.Description
        required                 = $spec.Required
        archive                  = (Split-Path -Leaf $archive.Path)
        format                   = $archive.Format
        raw_bytes                = $archive.Bytes
        sha256                   = $archive.Sha256
        payload_dir              = $PortableRootName
        payload_file_count       = $payloadEnum.Count
        payload_files_truncated  = $payloadTruncated
        payload_files            = $payloadFiles
        volume_count             = $volCount
        volumes                  = $volInfos
    }
    if (-not $KeepStaging) {
        # 逐组件释放 staging（同盘为硬链接几乎不占空间；跨盘回退复制时必须及时回收）。
        Remove-TTSMultiModelTreeFast -Path $payload
    }
}

# --------------------------------------------------------- 体积门禁 ----
$violations = @(Test-TTSMultiModelAssetSizeGate -Paths $allParts)
if ($violations.Count -gt 0) {
    Write-Host "`n体积门禁未通过：" -ForegroundColor Red
    foreach ($v in $violations) {
        Write-Host ("  {0}  {1}" -f $v.Path, $v.Reason) -ForegroundColor Red
    }
    if (-not $SkipSizeGate) {
        throw "存在超过 GitHub 单文件上限的分卷，禁止上传"
    }
}

# --------------------------------------------------------- 清单与说明 ----
$unpackScript = Join-Path $PSScriptRoot 'unpack_portable_bundle.ps1'
if (Test-Path -LiteralPath $unpackScript) {
    Copy-Item -LiteralPath $unpackScript -Destination $OutDir -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1') -Destination $OutDir -Force
}
$libName = 'portable_bundle_lib.ps1'

if ($SigningPfxPath) {
    Write-Host ''
    Write-Host '[Authenticode] 签名随包 .ps1 助手（先签名后生成 SHA256SUMS.txt）...' -ForegroundColor Yellow
    Invoke-TTSMultiModelAuthenticodeSign -Files @(
        (Join-Path $OutDir 'unpack_portable_bundle.ps1'),
        (Join-Path $OutDir $libName)
    ) -PfxPath $SigningPfxPath -PfxPassword $SigningPfxPassword
}

$totalRaw = 0
$totalDist = 0
foreach ($c in $components) {
    $totalRaw += $c.raw_bytes
    foreach ($v in $c.volumes) { $totalDist += $v.bytes }
}
$manifest = [ordered]@{
    schema      = 1
    product     = 'TTSMultiModel-Portable'
    version     = $Version
    arch        = 'win-x64'
    created_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    volume_limit_bytes = $Limit
    max_part_bytes     = $MaxPartBytes
    unpack_helper   = 'unpack_portable_bundle.ps1'
    unpack_requires = @('Windows PowerShell 5.1 或更高', '同目录的 portable_bundle_lib.ps1')
    total_payload_bytes = $totalRaw
    total_dist_bytes    = $totalDist
    components  = $components
    compliance  = [ordered]@{
        ffmpeg_not_distributed = $true
        watermark_key_excluded = $true
        license_and_notice_included = $true
        note = '构建期由 Assert-TTSMultiModelNoForbiddenPayload 递归断言，见 NOTICE 第 4 条与 docs/COMPLIANCE_CHECKLIST.md §2'
    }
}
Write-TTSMultiModelJson -Object $manifest -Path (Join-Path $OutDir 'manifest.json')
$sumFiles = @($allParts)
foreach ($f in @('manifest.json', 'unpack_portable_bundle.ps1', $libName)) {
    $p = Join-Path $OutDir $f
    if (Test-Path -LiteralPath $p) { $sumFiles += $p }
}
$sumsFile = Write-TTSMultiModelSha256Sums -Paths $sumFiles -OutFile (Join-Path $OutDir 'SHA256SUMS.txt')
$uploadList = ($allParts + @((Join-Path $OutDir 'manifest.json'), $sumsFile, (Join-Path $OutDir 'unpack_portable_bundle.ps1'), (Join-Path $OutDir $libName)))
[System.IO.File]::WriteAllLines((Join-Path $OutDir 'upload-list.txt'), [string[]]$uploadList, (New-Object System.Text.UTF8Encoding($false)))

if (-not $KeepStaging) {
    Remove-TTSMultiModelTreeFast -Path $StagingDir
}

Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host " 构建完成：$($components.Count) 个组件 / $($allParts.Count) 个分卷 / $(Format-TTSMultiModelSize $totalDist)" -ForegroundColor Green
Write-Host " 产物目录：$OutDir" -ForegroundColor Green
foreach ($c in $components) {
    $cSum = 0
    foreach ($v in $c.volumes) { $cSum += $v.bytes }
    Write-Host ("   {0,-13} {1} 卷  {2}" -f $c.id, $c.volume_count, (Format-TTSMultiModelSize $cSum))
}
Write-Host '==============================================' -ForegroundColor Green
Write-Host '下一步：上传到 Release（每个文件均 < 2 GiB；已发布资产不可变，禁用 --clobber）' -ForegroundColor Green
Write-Host '  gh release create v<ver> --target main --generate-notes'
Write-Host "  Get-Content '$(Join-Path $OutDir 'upload-list.txt')' | %{ gh release upload v<ver> `"`$_`" }"
