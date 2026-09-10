#Requires -Version 5.1
# scripts/portable_bundle_lib.ps1
# 便携分卷发布包的公共函数库（被 build_portable_bundle.ps1 / unpack_portable_bundle.ps1 dot-source）。
#
# 设计约束：
#   1. GitHub Release 单文件硬上限 2 GiB = 2147483648 字节；超过必须以「顺序字节切片」分卷。
#   2. 分卷命名统一为 `<archive>.001` / `<archive>.002` …，按序拼接即可还原原文件，
#      因此 .7z 与 .zip 两种容器共用同一套合并/校验逻辑。
#   3. 只依赖 Windows PowerShell 5.1 + 系统自带 tar.exe（bsdtar）；7-Zip 存在时优先用（压缩率更高）。
#      禁止使用 PowerShell 7 专属语法（?? 、三元、-Parallel、[IO.Path]::GetRelativePath）。
#   4. 本文件含中文，必须存为 **UTF-8 with BOM**：PowerShell 5.1 对无 BOM 的 .ps1 按 ANSI(GBK)
#      解码，中文会碎成乱码并直接破坏语法（同 AGENTS.md 陷阱 #18 一类问题）。

$script:GithubAssetLimitBytes = 2147483648
$script:DefaultMaxPartBytes = 1900MB
# 禁止随包分发的文件（NOTICE 第 4 条：ffmpeg/ffprobe 仅本地开发依赖，最终用户自行安装）。
# 必须用通配：imageio-ffmpeg 的 wheel 内自带 ffmpeg-win64-v7.1.exe，精确名匹配会漏。
$script:ForbiddenLeafPatterns = @('ffmpeg*.exe', 'ffprobe*.exe')
# 禁止进入任何分发物的本机私有文件（密钥 / 真实环境变量 / 配置备份）。
$script:DeniedLeafNames = @('.watermark_key', '.integrity_hmac_secret', '.manifest_signing_key', '.csrf_secret', '.pii_key', '.history_hmac_key', '.env', 'config.yaml.bak')

function Get-TTSMultiModelGithubAssetLimit {
    <# 返回 GitHub Release 单文件字节上限。 #>
    return [long]$script:GithubAssetLimitBytes
}

function Invoke-TTSMultiModelNative {
    <#
        执行外部命令并返回 @{ ExitCode; Text }。

        为什么必须走这个包装器：调用方普遍设了 `$ErrorActionPreference = 'Stop'`，
        而 Windows PowerShell 5.1 会把原生命令写到 stderr 的内容转成 error record，
        于是「命令本身正常但往 stderr 打了警告」会被升级为终止错误直接崩掉脚本
        （实测：`pip uninstall` 的 "Skipping ... not installed" 警告、
        以及 `python -c "import torch"` 这种*期望它失败*的探测都会炸）。
        这里在函数作用域内临时降为 Continue，退出后自动恢复，调用方只看 ExitCode。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @()
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $captured = $null
    try {
        $captured = & $Exe @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($null -eq $code) {
        $code = 0
    }
    return [pscustomobject]@{
        ExitCode = [int]$code
        Text     = (@($captured) | ForEach-Object { [string]$_ }) -join "`n"
    }
}

function Get-TTSMultiModelDefaultMaxPart {
    <# 返回默认分卷大小（1900MB，对 2GiB 留有余量）。 #>
    return [long]$script:DefaultMaxPartBytes
}

function Find-TTSMultiModelSevenZip {
    <# 探测 7z.exe：PATH → 常见安装目录 → CI runner 固定路径。找不到返回 $null。 #>
    foreach ($name in @('7z', '7za')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            return $cmd.Source
        }
    }
    $candidates = @(
        "$env:ProgramFiles\7-Zip\7z.exe",
        "${env:ProgramFiles(x86)}\7-Zip\7z.exe",
        "$env:LOCALAPPDATA\Programs\7-Zip\7z.exe",
        'C:\Program Files\7-Zip\7z.exe',
        "$env:ProgramFiles\NVIDIA Corporation\NVIDIA App\7z.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) {
            return $c
        }
    }
    return $null
}

function Find-TTSMultiModelSystemTar {
    <# 系统自带 bsdtar（Win10 1803+ 提供），可读 zip / 写 zip。
       注意：必须优先取 System32 的 bsdtar——从 Git Bash/MSYS 环境调用本脚本时
       PATH 里的 /usr/bin/tar（GNU tar）会排在前面，GNU tar 把 C:\ 路径当
       远程主机处理（"Cannot connect to C: resolve failed"，退出码 128），
       见 KNOWN_ISSUES #35。Get-Command 仅作 System32 缺失时的回退。 #>
    if (Test-Path -LiteralPath "$env:SystemRoot\System32\tar.exe") {
        return "$env:SystemRoot\System32\tar.exe"
    }
    $cmd = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

function Get-TTSMultiModelFileSha256 {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Get-TTSMultiModelFileSha256: 文件不存在 $Path"
    }
    $hash = Get-FileHash -LiteralPath $Path -Algorithm SHA256
    return $hash.Hash.ToLowerInvariant()
}

function Format-TTSMultiModelSize {
    param([Parameter(Mandatory = $true)][long]$Bytes)
    if ($Bytes -ge 1GB) {
        return ('{0:N2} GB' -f ($Bytes / 1GB))
    }
    if ($Bytes -ge 1MB) {
        return ('{0:N1} MB' -f ($Bytes / 1MB))
    }
    if ($Bytes -ge 1KB) {
        return ('{0:N1} KB' -f ($Bytes / 1KB))
    }
    return "$Bytes B"
}

function Get-TTSMultiModelFreeSpaceGb {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force
    $root = $item.Root.ToString()
    $di = New-Object System.IO.DriveInfo($root)
    return [math]::Round($di.AvailableFreeSpace / 1GB, 2)
}

function Assert-TTSMultiModelDiskSpace {
    <#
        构建前置检查。教训来源：磁盘仅剩 8GB 时跑分卷构建，[IO.File]::WriteAllText
        会先把目标文件截断为 0 字节再写、随后抛「not enough space」，直接毁掉源脚本。
        便携包构建需要 归档 + 全部分卷 同时落盘，必须提前算够。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][double]$NeededGb
    )
    $free = Get-TTSMultiModelFreeSpaceGb -Path $Path
    if ($free -lt $NeededGb) {
        throw "磁盘空间不足：$Path 所在盘可用 $free GB，本次构建至少需要 $NeededGb GB。请换盘（如 -OutDir H:\bundles -StagingDir H:\staging）或清理后重试。"
    }
    Write-Host ("  磁盘预检通过：{0} 可用 {1} GB（需要 {2} GB）" -f $Path, $free, $NeededGb) -ForegroundColor DarkGray
    return $free
}

function Write-TTSMultiModelJson {
    <# 以 UTF-8 无 BOM 写 JSON（BOM 会让 Python/Node 侧 json.load 报错）。 #>
    param(
        [Parameter(Mandatory = $true)][object]$Object,
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$Depth = 12
    )
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $json = $Object | ConvertTo-Json -Depth $Depth
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json, $enc)
}

function Read-TTSMultiModelJson {
    param([Parameter(Mandatory = $true)][string]$Path)
    $text = [System.IO.File]::ReadAllText($Path)
    return $text | ConvertFrom-Json
}

function ConvertTo-TTSMultiModelRelative {
    <# 计算 $Full 相对 $Root 的路径（不依赖 .NET Core 的 GetRelativePath）。 #>
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Full
    )
    $rootFull = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\', '/')
    $fullResolved = [System.IO.Path]::GetFullPath($Full)
    if ($fullResolved.Length -le $rootFull.Length) {
        return ''
    }
    if ($fullResolved.Substring(0, $rootFull.Length).ToLowerInvariant() -ne $rootFull.ToLowerInvariant()) {
        throw "ConvertTo-TTSMultiModelRelative: $fullResolved 不在 $rootFull 之下"
    }
    return $fullResolved.Substring($rootFull.Length).TrimStart('\', '/')
}

function Test-TTSMultiModelPathExcluded {
    <# 排除判定：命中禁止随包分发的模式、本机私有文件名，或调用方传入的通配模式。 #>
    param(
        [Parameter(Mandatory = $true)][string]$Relative,
        [string[]]$ExcludePatterns = @()
    )
    $norm = $Relative.Replace('/', '\')
    $leaf = (Split-Path -Leaf $norm).ToLowerInvariant()
    foreach ($denied in $script:DeniedLeafNames) {
        if ($leaf -eq $denied -or $leaf -like "$denied*") {
            return $true
        }
    }
    foreach ($forbidden in $script:ForbiddenLeafPatterns) {
        if ($leaf -like $forbidden) {
            return $true
        }
    }
    foreach ($p in $ExcludePatterns) {
        if (-not $p) {
            continue
        }
        $pattern = $p.Replace('/', '\').ToLowerInvariant()
        if ($norm.ToLowerInvariant() -like $pattern -or $leaf -like $pattern) {
            return $true
        }
    }
    return $false
}

function Assert-TTSMultiModelNoForbiddenPayload {
    <#
        递归确认目录内没有任何 ffmpeg/ffprobe 可执行文件与私有密钥。
        这是 docs/COMPLIANCE_CHECKLIST.md「便携包分发检查项」第 1、2 条的真实实现，
        失败即抛异常中断构建，而不是留一个勾选项在文档里空转。
        无输出（成功时不打印任何内容）。
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Assert-TTSMultiModelNoForbiddenPayload: 目录不存在 $Path"
    }
    $hits = @()
    foreach ($pattern in ($script:ForbiddenLeafPatterns + $script:DeniedLeafNames)) {
        $found = Get-ChildItem -LiteralPath $Path -Recurse -Force -File -Filter $pattern -ErrorAction SilentlyContinue
        foreach ($f in $found) {
            $hits += $f.FullName
        }
    }
    if ($hits.Count -gt 0) {
        $list = ($hits | Select-Object -First 10) -join "`n  "
        throw "Assert-TTSMultiModelNoForbiddenPayload: 发现 $($hits.Count) 个禁止随包分发的文件（NOTICE 第 4 条 / 密钥外泄）:`n  $list"
    }
}

function New-TTSMultiModelHardLink {
    <#
        同盘时优先建硬链接（staging 几乎零成本、零额外磁盘），失败回退复制。
        PowerShell 5.1 没有 New-Item -ItemType HardLink，走 cmd 的 mklink /H。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$SourceFile,
        [Parameter(Mandatory = $true)][string]$LinkPath
    )
    $srcRoot = (Split-Path -Parent $SourceFile).Split('\')[0]
    $dstRoot = (Split-Path -Parent $LinkPath).Split('\')[0]
    if ($srcRoot -ne $dstRoot) {
        Copy-Item -LiteralPath $SourceFile -Destination $LinkPath -Force
        return 'copy'
    }
    if (Test-Path -LiteralPath $LinkPath) {
        Remove-Item -LiteralPath $LinkPath -Force
    }
    $r = Invoke-TTSMultiModelNative -Exe 'cmd.exe' -Arguments @('/c', "mklink /H `"$LinkPath`" `"$SourceFile`"")
    if ((Test-Path -LiteralPath $LinkPath) -and ($r.ExitCode -eq 0)) {
        return 'hardlink'
    }
    Remove-Item -LiteralPath $LinkPath -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $SourceFile -Destination $LinkPath -Force
    return 'copy'
}

function Copy-TTSMultiModelTree {
    <#
        单次遍历把 $Source 下的文件搬进 $Dest，应用排除规则，能用硬链接就用硬链接。
        不做「先判断目录是否有存活文件」的预扫描：那会对每个子目录再递归一次，
        面对 WinPython site-packages（约 6 万文件）是 O(n²)，会把构建拖死。
        空目录不创建（其内文件全部被排除时，目录自然不会出现）。
        返回 @{ Files; Bytes; HardLinks; Copies; Skipped }
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Dest,
        [string[]]$ExcludePatterns = @()
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Copy-TTSMultiModelTree: 源不存在 $Source"
    }
    $stats = [pscustomobject]@{ Files = 0; Bytes = [long]0; HardLinks = 0; Copies = 0; Skipped = 0 }
    $root = (Resolve-Path -LiteralPath $Source).Path.TrimEnd('\', '/')
    $rootLen = $root.Length
    $src = Get-Item -LiteralPath $root -Force
    if (-not $src.PSIsContainer) {
        if (Test-TTSMultiModelPathExcluded -Relative $src.Name -ExcludePatterns $ExcludePatterns) {
            $stats.Skipped = 1
            return $stats
        }
        New-Item -ItemType Directory -Path $Dest -Force | Out-Null
        $mode = New-TTSMultiModelHardLink -SourceFile $src.FullName -LinkPath (Join-Path $Dest $src.Name)
        $stats.Files = 1
        $stats.Bytes = [long]$src.Length
        if ($mode -eq 'hardlink') { $stats.HardLinks = 1 } else { $stats.Copies = 1 }
        return $stats
    }

    $files = 0
    $bytes = [long]0
    $links = 0
    $copies = 0
    $skipped = 0
    $stack = New-Object System.Collections.Stack
    $stack.Push($root)
    while ($stack.Count -gt 0) {
        $current = [string]$stack.Pop()
        foreach ($file in (Get-ChildItem -LiteralPath $current -Force -File -ErrorAction SilentlyContinue)) {
            $relative = $file.FullName.Substring($rootLen).TrimStart('\', '/')
            if (Test-TTSMultiModelPathExcluded -Relative $relative -ExcludePatterns $ExcludePatterns) {
                $skipped += 1
                continue
            }
            $target = [System.IO.Path]::Combine($Dest, $relative)
            $targetDir = [System.IO.Path]::GetDirectoryName($target)
            if (-not [System.IO.Directory]::Exists($targetDir)) {
                [System.IO.Directory]::CreateDirectory($targetDir) | Out-Null
            }
            $mode = New-TTSMultiModelHardLink -SourceFile $file.FullName -LinkPath $target
            $files += 1
            $bytes += [long]$file.Length
            if ($mode -eq 'hardlink') { $links += 1 } else { $copies += 1 }
        }
        foreach ($sub in (Get-ChildItem -LiteralPath $current -Force -Directory -ErrorAction SilentlyContinue)) {
            $stack.Push($sub.FullName)
        }
    }
    return [pscustomobject]@{ Files = $files; Bytes = $bytes; HardLinks = $links; Copies = $copies; Skipped = $skipped }
}

function New-TTSMultiModelArchive {
    <#
        把 $SourceDir 目录内容打成单文件归档。
        优先 7-Zip（-t7z，压缩率高）；无 7-Zip 时用系统 tar.exe 写 zip。
        返回 @{ Path; Format = '7z'|'zip'; Bytes; Sha256; Tool }
        注意：归档内条目为 $SourceDir 的直接子项（不含 $SourceDir 本身），
        且两种容器使用同一份顶层条目清单，解压布局完全一致。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [ValidateSet('auto', '7z', 'zip')][string]$Format = 'auto',
        [ValidateRange(0, 9)][int]$Level = 4
    )
    if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
        throw "New-TTSMultiModelArchive: 源目录不存在 $SourceDir"
    }
    $out = [System.IO.Path]::GetFullPath($OutFile)
    $outDir = Split-Path -Parent $out
    if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }
    $names = @(Get-ChildItem -LiteralPath $SourceDir -Force | ForEach-Object { $_.Name })
    if ($names.Count -eq 0) {
        throw "New-TTSMultiModelArchive: $SourceDir 为空，无内容可归档"
    }

    $sevenZip = $null
    if ($Format -ne 'zip') {
        $sevenZip = Find-TTSMultiModelSevenZip
    }
    if ($Format -eq '7z' -and -not $sevenZip) {
        throw "New-TTSMultiModelArchive: 指定 -Format 7z 但未找到 7-Zip"
    }
    # 不能用 [IO.Path]::ChangeExtension：版本号里的点（v1.4.2-win-x64-core）会被当作扩展名，
    # 导致不同组件的归档被改成同名而互相覆盖。这里显式拼后缀。
    if ($out -match '\.(7z|zip)$') {
        $out = $out -replace '\.(7z|zip)$', ''
    }

    if ($sevenZip) {
        $usedFormat = '7z'
        $out = $out + '.7z'
        if (Test-Path -LiteralPath $out) {
            Remove-Item -LiteralPath $out -Force
        }
        $arguments = @('a', '-t7z', "-mx=$Level", '-mmt=on', '-bd', '-bso0', '-bsp0', '-y', $out) + $names
        $prev = Get-Location
        try {
            Set-Location -LiteralPath $SourceDir
            $res = Invoke-TTSMultiModelNative -Exe $sevenZip -Arguments $arguments
            $sevenExit = $res.ExitCode
            $sevenText = $res.Text
        } finally {
            Set-Location -LiteralPath $prev
        }
        if ($sevenExit -ne 0 -or -not (Test-Path -LiteralPath $out)) {
            throw "New-TTSMultiModelArchive: 7z 归档失败，退出码 $sevenExit`n$sevenText"
        }
        $tool = $sevenZip
    } else {
        $tar = Find-TTSMultiModelSystemTar
        if (-not $tar) {
            throw "New-TTSMultiModelArchive: 既无 7-Zip 也无系统 tar.exe，无法生成归档"
        }
        $usedFormat = 'zip'
        $out = $out + '.zip'
        if (Test-Path -LiteralPath $out) {
            Remove-Item -LiteralPath $out -Force
        }
        $base = @('-a', '-cf', $out)
        # bsdtar 的 zip 压缩级别走 libarchive 写选项；不识别该选项的版本退回默认 deflate。
        $attempt = @($base + @('--options', "zip:compression-level=$Level") + $names)
        $prev = Get-Location
        try {
            Set-Location -LiteralPath $SourceDir
            $res = Invoke-TTSMultiModelNative -Exe $tar -Arguments $attempt
            $toolExit = $res.ExitCode
            if ($toolExit -ne 0 -or -not (Test-Path -LiteralPath $out)) {
                Remove-Item -LiteralPath $out -Force -ErrorAction SilentlyContinue
                $res = Invoke-TTSMultiModelNative -Exe $tar -Arguments ($base + $names)
                $toolExit = $res.ExitCode
            }
            $tarText = $res.Text
        } finally {
            Set-Location -LiteralPath $prev
        }
        if ($toolExit -ne 0 -or -not (Test-Path -LiteralPath $out)) {
            throw "New-TTSMultiModelArchive: tar 归档失败，退出码 $toolExit`n$tarText"
        }
        $tool = $tar
    }

    $item = Get-Item -LiteralPath $out
    return [pscustomobject]@{
        Path   = $out
        Format = $usedFormat
        Bytes  = [long]$item.Length
        Sha256 = Get-TTSMultiModelFileSha256 -Path $out
        Tool   = $tool
    }
}

function Split-TTSMultiModelFileIntoVolumes {
    <#
        顺序字节切片：$File → $File.001 / $File.002 …（按序拼接可还原）
        每个分卷写完后立即校验字节数并删除中间归档由调用方决定。
        返回分卷数组 @{ Index; Name; Path; Bytes; Sha256 }
    #>
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][long]$MaxBytes
    )
    if ($MaxBytes -lt 1MB) {
        throw "Split-TTSMultiModelFileIntoVolumes: MaxBytes 过小（$MaxBytes）"
    }
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
        throw "Split-TTSMultiModelFileIntoVolumes: 文件不存在 $File"
    }
    $src = Get-Item -LiteralPath $File
    $total = [long]$src.Length
    $partCount = [int][math]::Ceiling($total / [double]$MaxBytes)
    if ($partCount -lt 1) {
        $partCount = 1
    }
    $results = @()
    $in = [System.IO.File]::OpenRead($src.FullName)
    try {
        $buffer = New-Object byte[] 4194304
        for ($i = 1; $i -le $partCount; $i++) {
            $volPath = ('{0}.{1:D3}' -f $src.FullName, $i)
            if (Test-Path -LiteralPath $volPath) {
                Remove-Item -LiteralPath $volPath -Force
            }
            $outStream = [System.IO.File]::Create($volPath)
            try {
                $remaining = $MaxBytes
                while ($remaining -gt 0) {
                    $toRead = [int][math]::Min([long]$buffer.Length, $remaining)
                    $read = $in.Read($buffer, 0, $toRead)
                    if ($read -le 0) {
                        break
                    }
                    $outStream.Write($buffer, 0, $read)
                    $remaining -= $read
                }
            } finally {
                $outStream.Dispose()
            }
            $vi = Get-Item -LiteralPath $volPath
            $results += [pscustomobject]@{
                Index  = $i
                Name   = $vi.Name
                Path   = $vi.FullName
                Bytes  = [long]$vi.Length
                Sha256 = Get-TTSMultiModelFileSha256 -Path $vi.FullName
            }
        }
    } finally {
        $in.Dispose()
    }
    $sum = [long]0
    foreach ($r in $results) { $sum += $r.Bytes }
    if ($sum -ne $total) {
        throw "Split-TTSMultiModelFileIntoVolumes: 分卷总字节 $sum != 原文件 $total"
    }
    return $results
}

function Join-TTSMultiModelVolumes {
    <# 按给定顺序拼接分卷 → $OutFile，并可选校验总字节。 #>
    param(
        [Parameter(Mandatory = $true)][string[]]$VolumePaths,
        [Parameter(Mandatory = $true)][string]$OutFile,
        [long]$ExpectedBytes = 0
    )
    $out = [System.IO.Path]::GetFullPath($OutFile)
    $outDir = Split-Path -Parent $out
    if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $out) {
        Remove-Item -LiteralPath $out -Force
    }
    $stream = [System.IO.File]::Create($out)
    try {
        foreach ($vol in $VolumePaths) {
            if (-not (Test-Path -LiteralPath $vol -PathType Leaf)) {
                throw "Join-TTSMultiModelVolumes: 缺少分卷 $vol"
            }
            $fs = [System.IO.File]::OpenRead($vol)
            try {
                $fs.CopyTo($stream)
            } finally {
                $fs.Dispose()
            }
        }
    } finally {
        $stream.Dispose()
    }
    $bytes = [long](Get-Item -LiteralPath $out).Length
    if ($ExpectedBytes -gt 0 -and $bytes -ne $ExpectedBytes) {
        throw "Join-TTSMultiModelVolumes: 合并后 $bytes 字节 != 期望 $ExpectedBytes"
    }
    return $out
}

function Test-TTSMultiModelAssetSizeGate {
    <#
        判定所有产物单文件是否不超过上限，返回违规项数组（空数组 = 通过）。
        这是整条发布链路唯一的体积门禁，必须真实执行而非写在文档里。
    #>
    param(
        [Parameter(Mandatory = $true)][string[]]$Paths,
        [long]$LimitBytes = 0
    )
    if ($LimitBytes -le 0) {
        $LimitBytes = [long]$script:GithubAssetLimitBytes
    }
    $violations = @()
    foreach ($p in $Paths) {
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) {
            $violations += [pscustomobject]@{ Path = $p; Bytes = [long](-1); Limit = $LimitBytes; Reason = 'missing' }
            continue
        }
        $bytes = [long](Get-Item -LiteralPath $p).Length
        if ($bytes -gt $LimitBytes) {
            $violations += [pscustomobject]@{ Path = $p; Bytes = $bytes; Limit = $LimitBytes; Reason = 'oversize' }
        }
    }
    return $violations
}

function Write-TTSMultiModelSha256Sums {
    <# 生成经典 `hash  filename` 清单（不含路径，便于 sha256sum -c / Get-FileHash 比对）。 #>
    param(
        [Parameter(Mandatory = $true)][string[]]$Paths,
        [Parameter(Mandatory = $true)][string]$OutFile
    )
    $lines = @()
    foreach ($p in $Paths) {
        $item = Get-Item -LiteralPath $p
        $lines += ('{0}  {1}' -f (Get-TTSMultiModelFileSha256 -Path $item.FullName), $item.Name)
    }
    $dir = Split-Path -Parent $OutFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($OutFile, [string[]]$lines, $enc)
    return $OutFile
}

function Resolve-TTSMultiModelRuntimeRoot {
    <#
        给定目录，反查便携解释器：返回 WinPython 根与 python.exe 全路径。
        WinPython 真实布局是 <root>\python-3.12.10.amd64\python.exe（<root>\python 只是联接），
        所以取 python.exe 所在目录的上一层作为根；按路径长度排序优先取最浅的那个。
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Resolve-TTSMultiModelRuntimeRoot: 路径不存在 $Path"
    }
    $py = Get-ChildItem -LiteralPath $Path -Recurse -Force -Filter python.exe -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } | Select-Object -First 1
    if (-not $py) {
        throw "Resolve-TTSMultiModelRuntimeRoot: $Path 之下找不到 python.exe"
    }
    $parent = $py.Directory.Parent
    $root = if ($parent) { $parent.FullName } else { $py.Directory.FullName }
    return [pscustomobject]@{
        RuntimeRoot = $root
        PythonExe   = $py.FullName
    }
}

function Remove-TTSMultiModelTreeFast {
    <# 长路径安全删除（torch 的 site-packages 常见超 260 字符）。 #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        return
    } catch {
        Invoke-TTSMultiModelNative -Exe 'cmd.exe' -Arguments @('/c', "rmdir /s /q `"$(Resolve-Path -LiteralPath $Path)`"") | Out-Null
        if (Test-Path -LiteralPath $Path) {
            throw "Remove-TTSMultiModelTreeFast: 无法删除 $Path"
        }
    }
}
