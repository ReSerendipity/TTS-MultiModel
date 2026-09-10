#Requires -Version 5.1
# scripts/release_gate.ps1 — 发布门禁五步（任务书阶段四 / 报告2 §4.1）
#
#  ① 构建（build_portable_bundle 等价脚本）
#  ② 拆包验证：清单条目完整、分卷校验、公钥验签 PASS（diag VERIFY=True）
#  ③ 负向断言：包内无 .watermark_key / 私钥 / 注释残留
#  ④ 篡改模拟：改任一模块 1 字节 → 哈希与清单不一致 → enforce 拒绝启动
#  ⑤ 冒烟启动：便携 python 真实启动自检，Ed25519 验签 PASS（diag selfcheck/VERIFY）
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
$realBuild = $RuntimeDir -and $TorchWheelDir -and $ModelDir
if (-not $realBuild) {
    # fixture 模式：伪运行时 / 2 个 wheel / model 目录 + LICENSE/NOTICE（与 test_portable_bundle 同构）
    Write-Host '  [fixture] 未提供真实 RuntimeDir/TorchWheelDir/ModelDir，构造最小夹具...' -ForegroundColor DarkGray
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
& (Join-Path $PSScriptRoot 'build_portable_bundle.ps1') `
    -Root $root -Version $Version -OutDir $outBundle -StagingDir (Join-Path $WorkDir 'staging') `
    -RuntimeDir $RuntimeDir -TorchWheelDir $TorchWheelDir -ModelDir $ModelDir `
    -MaxPartBytes $MaxPartBytes -SkipOfflineTorchCheck -SkipAutoPrepare | Out-Host
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
    $tamperInstall = Join-Path $WorkDir 'tamper-install'
    try {
        $null = & (Join-Path $PSScriptRoot 'unpack_portable_bundle.ps1') -BundleDir $tamperBundle `
            -TargetDir $tamperInstall -SkipTorchInstall -ErrorAction Stop 2>&1
    } catch {
        Write-Host "  [enforce] 篡改包解包被拒（$($_.Exception.Message.Split([Environment]::NewLine)[0])）" -ForegroundColor DarkGray
    }
    if (Test-Path -LiteralPath (Join-Path $tamperInstall 'TTSMultiModel-Portable\app\integrated_app')) {
        $enforceOut = & $PythonExe (Join-Path $PSScriptRoot 'diag_integrity.py') --app-dir (Join-Path $tamperInstall 'TTSMultiModel-Portable\app\integrated_app') --enforce 2>&1
        $enforceExit = $LASTEXITCODE
        $enforceOut | Select-Object -Last 2 | ForEach-Object { Write-Host "  [enforce] $_" -ForegroundColor DarkGray }
        # diag --enforce 中 enforce=True 抛 RuntimeError 被记为 False → exit 1 = 阻断成功
        Assert-Step ($enforceExit -ne 0) 'enforce-rejects-tamper' "exit=$enforceExit（enforce 必须拒绝）"
    }
}

# ================ ⑤ 冒烟启动 ================
Write-Step '⑤ 冒烟启动（自检 + 验签）'
$smokeOut = & $PythonExe (Join-Path $PSScriptRoot 'diag_integrity.py') --app-dir $payloadApp --enforce 2>&1
$smokeExit = $LASTEXITCODE
$smokeOut | Select-Object -Last 2 | ForEach-Object { Write-Host "  [smoke] $_" -ForegroundColor DarkGray }
Assert-Step ($smokeExit -eq 0 -and ($smokeOut -match 'selfcheck=True') -and ($smokeOut -match 'VERIFY=True')) 'smoke' "diag-exit=$smokeExit"
Write-Host '  [note] ⑤ 使用便携解释器时即为真实启动自检；本地默认仓库 .venv（等价逻辑），CI 传 -PythonExe' -ForegroundColor DarkGray

# ---------- 汇总 ----------
Write-Host ''
Write-Host '==============================================' -ForegroundColor Cyan
if ($failures.Count -eq 0) {
    Write-Host (" 发布门禁五步：全部通过（{0}）" -f ($steps -join ' / ')) -ForegroundColor Green
    Write-Host '==============================================' -ForegroundColor Green
    exit 0
} else {
    Write-Host (" 发布门禁五步：失败 {0} 项（{1}）" -f $failures.Count, ($failures -join ' / ')) -ForegroundColor Red
    Write-Host '==============================================' -ForegroundColor Red
    exit 1
}
