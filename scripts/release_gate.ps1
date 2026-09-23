#Requires -Version 5.1
# scripts/release_gate.ps1 — 发布门禁六步（任务书阶段四 / 报告2 §4.1 + 桌面链路补口）
#
#  ① 构建（build_portable_bundle 等价脚本）
#  ② 拆包验证：清单条目完整、分卷校验、公钥验签 PASS（diag VERIFY=True）
#  ③ 负向断言：包内无 .watermark_key / 私钥 / 注释残留
#  ④ 篡改模拟：改任一模块 1 字节 → 哈希与清单不一致 → enforce 拒绝启动
#  ⑤ 冒烟启动：便携 python 真实启动自检，Ed25519 验签 PASS（diag selfcheck/VERIFY）
#  ⑥ 桌面链路负载：跑 assemble_release_staging，核对发给用户的那份目录里
#     许可三件套 / fonts.local.css / woff2 与 OFL 计数都在，且无密钥与权重
#
# 用法：
#   # fixture 模式（本地快验，默认）：
#   .\scripts\release_gate.ps1
#   # 真实构建模式（CI 发布 tag，提供运行时/轮子/模型）：
#   .\scripts\release_gate.ps1 -RuntimeDir D:\WPy64-312101 -TorchWheelDir D:\wheels -ModelDir D:\model
#   # 指定 ⑤ 的解释器（CI 用便携 python）：
#   .\scripts\release_gate.ps1 -PythonExe D:\WPy64-312101\python-3.12.10.amd64\python.exe
#
# 退出码：全步通过 = 0；任一步失败 = 非 0（CI 阻断发布）。

[CmdletBinding()]
param(
    [string]$Version = '9.9.9-gate',
    [string]$RuntimeDir = '',
    [string]$TorchWheelDir = '',
    [string]$ModelDir = '',
    [string]$PythonExe = '',
    [string]$WorkDir = '',
    [string]$WinPythonUrl = '',
    [long]$MaxPartBytes = 0
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'portable_bundle_lib.ps1')
if (-not $WorkDir) { $WorkDir = Join-Path $env:TEMP 'tts_multimodel-release-gate' }
if (-not $PythonExe) {
    $venvPy = Join-Path $root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) { $PythonExe = $venvPy } else { $PythonExe = 'python' }
}
if ($MaxPartBytes -le 0) { $MaxPartBytes = 1MB }
$steps = @()
$failures = @()

function Write-Step {
    param([string]$Title)
    Write-Host ''
    Write-Host ("================ {0} ================" -f $Title) -ForegroundColor Cyan
}

function Assert-Step {
    param([bool]$Ok, [string]$Name, [string]$Detail)
    if ($Ok) {
        Write-Host ("  PASS  {0}  {1}" -f $Name, $Detail) -ForegroundColor Green
        $script:steps += $Name
    } else {
        Write-Host ("  FAIL  {0}  {1}" -f $Name, $Detail) -ForegroundColor Red
        $script:failures += $Name
    }
}

# ---------- 工作目录 ----------
if (Test-Path -LiteralPath $WorkDir) { Remove-Item -LiteralPath $WorkDir -Recurse -Force }
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
$fx = Join-Path $WorkDir 'fixture'
$outBundle = Join-Path $WorkDir 'out-bundle'
$installed = Join-Path $WorkDir 'installed'
foreach ($d in @($fx, $outBundle, $installed)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }

# ================ ① 构建 ================
Write-Step '① 构建（build_portable_bundle）'
$realBuild = [bool]$ModelDir
if (-not $realBuild) {
    # fixture 模式：伪运行时 / 2 个 wheel / model 目录 + LICENSE/NOTICE（与 test_portable_bundle 同构）
    Write-Host '  [fixture] 未提供真实 ModelDir，构造最小夹具...' -ForegroundColor DarkGray
    New-Item -ItemType Directory -Path (Join-Path $fx 'WPy64-FAKE\python-3.12.10.amd64\Lib\site-packages\numpy') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $fx 'WPy64-FAKE\python-3.12.10.amd64\python.exe') -Value 'not-a-real-python' -Encoding ascii
    New-Item -ItemType Directory -Path (Join-Path $fx 'wheels') -Force | Out-Null
    $whl = Join-Path $fx 'wheels\torch-2.13.0+cu132-cp312-cp312-win_amd64.whl'
    [System.IO.File]::WriteAllBytes($whl, (New-Object byte[] (1.7MB)))
    New-Item -ItemType Directory -Path (Join-Path $fx 'model') -Force | Out-Null
    [System.IO.File]::WriteAllBytes((Join-Path $fx 'model\model-a.safetensors'), (New-Object byte[] (3MB)))
    Set-Content -LiteralPath (Join-Path $fx 'LICENSE') -Value 'fake-apache-2.0-license' -Encoding ascii
    Set-Content -LiteralPath (Join-Path $fx 'NOTICE') -Value 'fake-notice' -Encoding ascii
    $RuntimeDir = Join-Path $fx 'WPy64-FAKE'
    $TorchWheelDir = Join-Path $fx 'wheels'
    $ModelDir = Join-Path $fx 'model'
}
# 真实构建：RuntimeDir/TorchWheelDir 可缺省 → build 脚本自动准备（WinPython 下载 + 依赖预装 + wheels 复用/下载）
# 注意：命名参数必须用哈希表 splat（数组 splat 是位置传参，会把 -Param 当普通值绑定到首个位置参数）
$buildArgs = @{
    Root         = $root
    Version      = $Version
    OutDir       = $outBundle
    StagingDir   = (Join-Path $WorkDir 'staging')
    ModelDir     = $ModelDir
    MaxPartBytes = $MaxPartBytes
}
if ($RuntimeDir) {
    $buildArgs.RuntimeDir = $RuntimeDir
    $buildArgs.SkipAutoPrepare = $true
}
if ($TorchWheelDir) { $buildArgs.TorchWheelDir = $TorchWheelDir }
if ($WinPythonUrl) { $buildArgs.WinPythonUrl = $WinPythonUrl }
if (-not $realBuild) {
    # fixture 模式：跳过离线可装性验证（伪 wheels 无意义）与自动准备
    $buildArgs.SkipOfflineTorchCheck = $true
    # fixture 的轮子是 1.7 MB 全零节造假，必然过不了 build 脚本的
    # wheel SHA256 门禁（那条门禁是为真实构建设的，这里显式关闭）。
    # 真实构建（-realBuild）不走这里，因此发版轨次永远被校验。
    $buildArgs.TorchSha256 = ''
    $buildArgs.TorchvisionSha256 = ''
    $buildArgs.TorchaudioSha256 = ''
    $buildArgs.SkipAutoPrepare = $true
}
& (Join-Path $PSScriptRoot 'build_portable_bundle.ps1') @buildArgs | Out-Host
Assert-Step ($LASTEXITCODE -eq 0) 'build' "exit=$LASTEXITCODE"

$manifest = Read-TTSMultiModelJson -Path (Join-Path $outBundle 'manifest.json')
Assert-Step (@($manifest.components).Count -eq 3) 'manifest-components' "components=$(@($manifest.components).Count)"
$vols = @(Get-ChildItem -LiteralPath $outBundle -File | Where-Object { $_.Name -match '\.\d{3}$' })
Assert-Step ($vols.Count -ge 3) 'manifest-volumes' "volumes=$($vols.Count)"

# ================ ② 拆包验证 ================
Write-Step '② 拆包验证（VerifyOnly + 公钥验签）'
& (Join-Path $PSScriptRoot 'unpack_portable_bundle.ps1') -BundleDir $outBundle -TargetDir (Join-Path $WorkDir 'verify-only') -VerifyOnly 2>&1 | Out-Host
Assert-Step ($LASTEXITCODE -eq 0) 'verify-only' "exit=$LASTEXITCODE"
# 真实拆包（供 ③④⑤ 使用）
& (Join-Path $PSScriptRoot 'unpack_portable_bundle.ps1') -BundleDir $outBundle -TargetDir $installed -SkipTorchInstall 2>&1 | Out-Host
Assert-Step ($LASTEXITCODE -eq 0) 'unpack' "exit=$LASTEXITCODE"
$payloadApp = Join-Path $installed 'TTSMultiModel-Portable\app\integrated_app'
Assert-Step (Test-Path -LiteralPath $payloadApp) 'payload-app' $payloadApp
# 公钥验签：diag 对 payload 跑 VERIFY
$diagOut = & $PythonExe (Join-Path $PSScriptRoot 'diag_integrity.py') --app-dir $payloadApp 2>&1
$diagExit = $LASTEXITCODE
$diagOut | Select-Object -Last 3 | ForEach-Object { Write-Host "  [diag] $_" -ForegroundColor DarkGray }
Assert-Step ($diagExit -eq 0 -and ($diagOut -match 'VERIFY=True')) 'verify-signature' "diag-exit=$diagExit"
# ② 补注：无 .py 源码残留 — TTS 无闭源编译（无 A 线 .pyd 替换），此子项 N/A
Write-Host '  [NA] 无 .py 源码残留检查：TTS 未做闭源编译（无 Cython .pyd 替换线），不适用' -ForegroundColor DarkGray

# ================ ③ 负向断言 ================
Write-Step '③ 负向断言（密钥 / 私钥 / 残留）'
$leaked = @()
foreach ($denied in @('.watermark_key', '.manifest_signing_key', '.integrity_hmac_secret', '.csrf_secret', '.pii_key', '.env', 'config.yaml.bak')) {
    $hits = Get-ChildItem -LiteralPath $installed -Recurse -Force -File -Filter $denied -ErrorAction SilentlyContinue
    foreach ($h in $hits) { $leaked += $h.FullName }
}
Assert-Step ($leaked.Count -eq 0) 'no-secrets' "leaked=$($leaked.Count)"
# 残留断言：打包内容不应出现构建机本机路径（C:\Users\Doro）。
# 注意：SeedVR2 是 TTS 的合法署名引用（README/app 代码多处注明来源），不算残留。
# 仅扫文本文件，避免二进制（权重/wheel）产生噪声命中。
$payloadRoot = (Resolve-Path -LiteralPath (Join-Path $installed 'TTSMultiModel-Portable')).Path
$textExts = @('.py', '.yaml', '.yml', '.json', '.md', '.toml', '.txt', '.ini', '.bat', '.ps1', '.html', '.js', '.css', '.cfg')
$localPaths = @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $textExts -contains $_.Extension.ToLowerInvariant() } |
    Select-String -Pattern 'C:\\Users\\Doro' -List -ErrorAction SilentlyContinue)
Assert-Step ($localPaths.Count -eq 0) 'no-local-path-residue' "hits=$($localPaths.Count)"

# ================ ⑤ 冒烟启动 ================
Write-Step '⑤ 冒烟启动（自检 + 验签）'
# 真实便携解释器优先：解包载荷内的 WPy64 运行时 python.exe（真实启动自检）
$payloadRootPath = Join-Path $installed 'TTSMultiModel-Portable'
# fixture 模式塞进载荷的是 19 字节的文本假 python.exe（见本文件 fixture 段的
# 'not-a-real-python'），按目录名匹配会被当成真实便携解释器，⑤ 于是永远走不到
# 下面那条「回退仓库 .venv」分支；真实 WinPython 的 python.exe 远大于该阈值。
$payloadPy = @(Get-ChildItem -LiteralPath $payloadRootPath -Recurse -File -Filter 'python.exe' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'WPy64' -and $_.Length -gt 65536 } | Select-Object -First 1).FullName
$smokePython = if ($payloadPy) { $payloadPy } else { $PythonExe }
$smokeOut = & $smokePython (Join-Path $PSScriptRoot 'diag_integrity.py') --app-dir $payloadApp --enforce 2>&1
$smokeExit = $LASTEXITCODE
$smokeOut | Select-Object -Last 2 | ForEach-Object { Write-Host "  [smoke] $_" -ForegroundColor DarkGray }
Assert-Step ($smokeExit -eq 0 -and ($smokeOut -match 'selfcheck=True') -and ($smokeOut -match 'VERIFY=True')) 'smoke' "diag-exit=$smokeExit py=$([System.IO.Path]::GetFileName([System.IO.Path]::GetDirectoryName($smokePython)))"
if (-not $payloadPy) {
    Write-Host '  [note] 载荷内未找到便携 python，⑤ 回退仓库 .venv（等价逻辑）；真实构建应有 WPy64 运行时' -ForegroundColor DarkGray
}

# ================ ④ 篡改模拟 ================
Write-Step '④ 篡改模拟（改 1 字节 → 校验失败）'
$tamperBundle = Join-Path $WorkDir 'tamper-bundle'
New-Item -ItemType Directory -Path $tamperBundle -Force | Out-Null
Copy-Item -Path (Join-Path $outBundle '*') -Destination $tamperBundle -Force
$target = Join-Path $tamperBundle $vols[0].Name
$bytes = [System.IO.File]::ReadAllBytes($target)
$bytes[0] = $bytes[0] -bxor 0x01
[System.IO.File]::WriteAllBytes($target, $bytes)
# 篡改后校验必须失败：unpack 对校验失败抛终止异常（try/catch 预期捕获）
$tamperRejected = $false
$tamperMsg = ''
try {
    $null = & (Join-Path $PSScriptRoot 'unpack_portable_bundle.ps1') -BundleDir $tamperBundle `
        -TargetDir (Join-Path $WorkDir 'tamper-install') -VerifyOnly -ErrorAction Stop 2>&1
} catch {
    $tamperRejected = $true
    $tamperMsg = $_.Exception.Message
}
Write-Host "  [tamper] rejected=$tamperRejected  $($tamperMsg.Split([Environment]::NewLine)[0])" -ForegroundColor DarkGray
Assert-Step $tamperRejected 'tamper-blocked' "rejected=$tamperRejected（校验必须失败）"
# enforce 阻断：篡改后的包若强行解包并启动自检，应被 enforce 拒绝（④ 的运行时面）
if ($tamperRejected) {
    # 磁盘调度：⑤ 已跑完，installed/out-bundle 不再需要；
    # 删除两者释放空间，供 tamper-install 解包（峰值从 116GB 降到 ~58GB）。
    if (Test-Path -LiteralPath $installed) {
        Remove-Item -LiteralPath $installed -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  [清理] 删除 installed 释放磁盘" -ForegroundColor DarkGray
    }
    if (Test-Path -LiteralPath $outBundle) {
        Remove-Item -LiteralPath $outBundle -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  [清理] 删除 out-bundle 释放磁盘（tamper-bundle 为副本）" -ForegroundColor DarkGray
    }
    $tamperInstall = Join-Path $WorkDir 'tamper-install'
    try {
        $null = & (Join-Path $PSScriptRoot 'unpack_portable_bundle.ps1') -BundleDir $tamperBundle `
            -TargetDir $tamperInstall -SkipTorchInstall -ErrorAction Stop 2>&1
    } catch {
        Write-Host "  [enforce] 篡改包解包被拒（$($_.Exception.Message.Split([Environment]::NewLine)[0])）" -ForegroundColor DarkGray
    }
    $tamperPayload = Join-Path $tamperInstall 'TTSMultiModel-Portable\app\integrated_app'
    if (Test-Path -LiteralPath $tamperPayload) {
        $enforceOut = & $PythonExe (Join-Path $PSScriptRoot 'diag_integrity.py') --app-dir $tamperPayload --enforce 2>&1
        $enforceExit = $LASTEXITCODE
        $enforceOut | Select-Object -Last 2 | ForEach-Object { Write-Host "  [enforce] $_" -ForegroundColor DarkGray }
        Assert-Step ($enforceExit -ne 0) 'enforce-rejects-tamper' "exit=$enforceExit（enforce 必须拒绝）"
    }
}

# ---------- ⑥ 桌面链路负载（staging 目录里到底有什么） ----------
# 桌面安装包走 assemble_release_staging → data 7z → NSIS，与 ① 的便携包链路各持一份
# 白名单：THIRD_PARTY_NOTICES.md 曾两次不在表上、SECURITY.md 迁走后旧路径静默失效（#134）。
# 这条只要几秒（app/ 走硬链接），把"会发给用户的那份目录"钉死。
Write-Step '⑥ 桌面链路负载（assemble_release_staging）'
$stagingDir = Join-Path $WorkDir 'desktop-staging'
$stubDir = Join-Path $WorkDir 'desktop-stub'
New-Item -ItemType Directory -Path $stubDir -Force | Out-Null
# 壳二进制不进 git（CI 干净检出没有），这里用桩文件：staging 只负责把它拷进包
$shellExe = Join-Path $stubDir 'TTSMultiModel.exe'
Set-Content -LiteralPath $shellExe -Value 'stub-shell-for-gate' -Encoding ascii
$staged = $true
try {
    & (Join-Path $PSScriptRoot 'assemble_release_staging.ps1') -Version $Version -ShellExe $shellExe -OutDir $stagingDir | Out-Host
} catch {
    $staged = $false
    Write-Host ("  staging 失败：{0}" -f $_.Exception.Message.Split([Environment]::NewLine)[0]) -ForegroundColor Red
}
Assert-Step $staged 'desktop-staging-runs' ''

$need = @(
    'TTSMultiModel.exe', 'start_portable.py', 'config.yaml', 'version.json', 'start.bat',
    'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md', 'README.md',
    'app\integrated_app\static\css\fonts.local.css',
    'app\integrated_app\templates\base.html',
    'app\integrated_app\security\manifest_signing_public_key.pem'
)
$missing = @($need | Where-Object { -not (Test-Path -LiteralPath (Join-Path $stagingDir $_)) })
$fontDir = Join-Path $stagingDir 'app\integrated_app\static\fonts'
$woff2 = @(Get-ChildItem -LiteralPath $fontDir -Filter '*.woff2' -File -ErrorAction SilentlyContinue)
$ofl = @(Get-ChildItem -LiteralPath (Join-Path $fontDir 'licenses') -Filter '*.txt' -File -ErrorAction SilentlyContinue)
# 禁区：密钥/证书/权重/测试与文档不得进桌面包
$denyLeaves = @('.watermark_key', '.csrf_secret', '.pii_key', '.integrity_hmac_secret', '.manifest_signing_key',
    'key.pem', 'cert.pem', '.env', '.server_port', 'config.yaml.bak')
$leaked = @()
$allStaged = @(Get-ChildItem -LiteralPath $stagingDir -Recurse -Force -File -ErrorAction SilentlyContinue)
foreach ($f in $allStaged) {
    if ($denyLeaves -contains $f.Name) { $leaked += $f.Name }
}
$weights = @($allStaged | Where-Object { $_.Extension -in '.safetensors', '.bin', '.pt', '.onnx' })
$payloadBad = @()
if ($missing.Count) { $payloadBad += "缺 $($missing -join ', ')" }
if ($woff2.Count -lt 700) { $payloadBad += "woff2 仅 $($woff2.Count) 个（期望 ≥700）" }
if ($ofl.Count -lt 12) { $payloadBad += "字体许可文本仅 $($ofl.Count) 份（期望 ≥12）" }
if ($leaked.Count) { $payloadBad += "禁区文件混入：$(($leaked | Select-Object -Unique) -join ', ')" }
if ($weights.Count) { $payloadBad += "未提供 -ModelDir 却有 $($weights.Count) 个权重文件混入" }
Write-Host ("  staging 共 {0} 个文件；woff2 {1} / 许可 {2} / 排除项命中 {3}" -f $allStaged.Count, $woff2.Count, $ofl.Count, $leaked.Count) -ForegroundColor DarkGray
Assert-Step ($payloadBad.Count -eq 0) 'desktop-payload' ($payloadBad -join '；')

# ---------- 汇总 ----------
Write-Host ''
Write-Host '==============================================' -ForegroundColor Cyan
if ($failures.Count -eq 0) {
    Write-Host (" 发布门禁六步：全部通过（{0}）" -f ($steps -join ' / ')) -ForegroundColor Green
    Write-Host '==============================================' -ForegroundColor Green
    exit 0
} else {
    Write-Host (" 发布门禁六步：失败 {0} 项（{1}）" -f $failures.Count, ($failures -join ' / ')) -ForegroundColor Red
    Write-Host '==============================================' -ForegroundColor Red
    exit 1
}
