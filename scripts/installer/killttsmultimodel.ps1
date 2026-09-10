# 终止 TTSMultiModel 的 Python 子进程（按命令行路径匹配）
# 安装/卸载路径含 TTSMultiModel（$LOCALAPPDATA\Programs\TTSMultiModel），
# 开发路径含 TTS_MultiModel（C:\Users\Doro\TTS_MultiModel\...），两者都匹配。
$found = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*TTSMultiModel*' -or $_.CommandLine -like '*TTS_MultiModel*' })
$info = "time=$(Get-Date -Format 'HH:mm:ss') found=$($found.Count)`n" + ($found | ForEach-Object { "$($_.ProcessId):$($_.ExecutablePath)" } | Out-String)
[System.IO.File]::WriteAllText("$env:TEMP\tts-multimodel-kill.log", $info, [System.Text.Encoding]::UTF8)
foreach ($proc in $found) {
  Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
  Wait-Process -Id $proc.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
}
