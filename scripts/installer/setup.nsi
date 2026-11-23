; TTSMultiModel 桌面版 NSIS 安装器
; 用法：makensis setup.nsi
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "StrFunc.nsh"
; CLI 全局命令：PATH 增删需要字符串搜索/替换。安装段与卸载段各用一套函数
; （StrFunc 的 un 版本必须单独 Using，且不能在同一个宏里混用 install/uninstall 调用）。
${Using:StrFunc} StrStr
${Using:StrFunc} UnStrStr
${Using:StrFunc} UnStrRep

Name "TTSMultiModel 桌面版"
InstallDir "$LOCALAPPDATA\Programs\TTSMultiModel"
InstallDirRegKey HKCU "Software\TTSMultiModel" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
CRCCheck on
BrandingText "TTSMultiModel"

; 版本号：发布时用 -DAPP_VERSION=<pyproject.toml 的 version> 显式传入；下面的默认值只在
; 脱离参数编译时兜底，务必与 pyproject.toml 保持一致。
!ifndef APP_VERSION
  !define APP_VERSION "2.12.6"
!endif
OutFile "TTSMultiModel-Setup-v2.12.6.exe"
!define DATA_PREFIX "TTSMultiModel-Data.7z"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\TTSMultiModel"
!define APP_ICON "${__FILEDIR__}\..\..\desktop\src-tauri\icons\icon.ico"
; 7za 默认从 PATH 查找，或用 makensis -DSRC_7ZA=... 指定（勿写死本机绝对路径）
!ifndef SRC_7ZA
!define SRC_7ZA "7za.exe"
!endif
!ifndef SRC_DATA
  !error "单文件安装包：需用 -DSRC_DATA=<数据卷 .7z.001 路径> 内嵌数据分卷"
!endif

Var StartMenuFolder
Var TOOLSDIR

; ---------- CLI 全局命令：把 $INSTDIR 追加到当前用户 PATH（幂等）----------
; 无参数：直接使用 $INSTDIR。函数内部用 $0/$1/$2/$3 须自行压栈保存。
; 只在 PATH 中不存在该条目时追加，避免重复安装/升级产生重复项。
Function AddToUserPath
  Push $0
  Push $1
  Push $2
  Push $3
  ReadRegStr $1 HKCU "Environment" "Path"
  ${StrStr} $2 "$1" "$INSTDIR"
  ${If} $2 == ""
    ${If} $1 == ""
      StrCpy $1 "$INSTDIR"
    ${Else}
      ; 避免前一条目无尾随分号时粘连
      StrCpy $3 "$1" 1 -1
      ${If} $3 == ";"
        StrCpy $1 "$1$INSTDIR"
      ${Else}
        StrCpy $1 "$1;$INSTDIR"
      ${EndIf}
    ${EndIf}
    WriteRegExpandStr HKCU "Environment" "Path" "$1"
    ; 广播环境变量变更，让新开的终端/AI agent 立即看到 CLI 命令
    SendMessage ${HWND_BROADCAST} ${WM_WININICHANGE} 0 "STR:Environment" /TIMEOUT=5000
    DetailPrint "已将 CLI 命令目录加入当前用户 PATH: $INSTDIR"
  ${Else}
    DetailPrint "CLI 命令目录已在当前用户 PATH 中，跳过"
  ${EndIf}
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; ---------- 卸载：从当前用户 PATH 精确移除 $INSTDIR 条目 ----------
Function un.RemoveFromUserPath
  Push $0
  Push $1
  Push $2
  Push $3
  ReadRegStr $1 HKCU "Environment" "Path"
  ${UnStrStr} $2 "$1" "$INSTDIR"
  ${IfNot} $2 == ""
    ; 先按 ";<entry>" 与 "<entry>;" 两种粘连清理，再兜底精确替换，最后折叠重复分号
    ${UnStrRep} $1 "$1" ";$INSTDIR" ""
    ${UnStrRep} $1 "$1" "$INSTDIR;" ""
    ${UnStrRep} $1 "$1" "$INSTDIR" ""
    ${UnStrRep} $1 "$1" ";;" ";"
    ${UnStrRep} $1 "$1" ";;" ";"
    ; 去掉首尾分号
    StrCpy $3 "$1" 1
    ${If} $3 == ";"
      StrCpy $1 "$1" "" 1
    ${EndIf}
    StrCpy $3 "$1" 1 -1
    ${If} $3 == ";"
      StrCpy $1 "$1" -1
    ${EndIf}
    WriteRegExpandStr HKCU "Environment" "Path" "$1"
    SendMessage ${HWND_BROADCAST} ${WM_WININICHANGE} 0 "STR:Environment" /TIMEOUT=5000
    DetailPrint "已从当前用户 PATH 移除 CLI 命令目录: $INSTDIR"
  ${EndIf}
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; ---------- 终止运行中的 TTSMultiModel（壳 + 其 Python 子进程）----------
!macro KillRunning
  DetailPrint "检测到 TTSMultiModel 正在运行，正在自动终止..."
  nsExec::ExecToLog 'taskkill /f /im TTSMultiModel.exe'
  Sleep 1500
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$TOOLSDIR\killttsmultimodel.ps1"'
  Sleep 1500
!macroend

; ---------- MUI 页面 ----------
!define MUI_ABORTWARNING
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "license.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_STARTMENU "Application" $StartMenuFolder
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\TTSMultiModel.exe"
!define MUI_FINISHPAGE_RUN_TEXT "立即运行 TTSMultiModel"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

; ---------- 安装 ----------
Section "TTSMultiModel 桌面版" SEC_APP
  ; ---------- 安装前告知（静默模式自动确认） ----------
  MessageBox MB_OKCANCEL|MB_ICONINFORMATION "安装前请知悉：`n`n  - 本次安装约 1.5GB（应用 + 引导解释器），PyTorch 运行环境与语音模型不在安装包内，安装后请到程序内「下载管理」页安装（合计约 30GB）`n  - 本软件本机离线运行，不上传任何用户数据`n  - 语音克隆仅限您拥有权利的音频，严禁冒充他人；AI 生成音频请依法标识`n  - IndexTTS 模型商用需 bilibili 书面授权`n`n完整条款见上一页「许可协议」。确定继续安装？" /SD IDOK
  Pop $0
  ${If} $0 == "IDCANCEL"
    Abort
  ${EndIf}

  ; 自建临时工具目录（不依赖 $PLUGINSDIR，该变量在 NSIS 3.10 实测为空）
  StrCpy $TOOLSDIR "$TEMP\tts_multimodel-tools"
  RMDir /r "$TOOLSDIR"
  CreateDirectory "$TOOLSDIR"
  SetOutPath "$TOOLSDIR"
  ; 必须 /oname=7za.exe：构建机给的可能是 7z.exe（或任意文件名），而下面 ExecWait
  ; 固定调 "$TOOLSDIR\7za.exe"。缺 /oname 时解出来的名字对不上 → 进程起不来 →
  ; 错误码为空串 → 弹「数据解压失败（错误码 ）」且安装目录空着。
  File /oname=7za.exe "${SRC_7ZA}"
  ; 单文件安装包：数据分卷直接内嵌（瘦包已不带环境/模型，体积可接受）
  File /oname=${DATA_PREFIX}.001 "${SRC_DATA}"
  File "killttsmultimodel.ps1"

  !insertmacro KillRunning

  ; 校验数据分卷存在
  ${IfNot} ${FileExists} "$TOOLSDIR\${DATA_PREFIX}.001"
    MessageBox MB_ICONSTOP "缺少数据分卷 ${DATA_PREFIX}.001。`n请确认 setup.exe 与全部数据分卷（.001/.002/...）位于同一目录。"
    Abort
  ${EndIf}

  ; 创建安装目录并解压数据分卷
  SetOutPath "$INSTDIR"
  DetailPrint "正在解压数据分卷（体积大，视磁盘速度需 3-10 分钟，请勿关闭窗口）..."
  ExecWait '"$TOOLSDIR\7za.exe" x "$TOOLSDIR\${DATA_PREFIX}.001" -o"$INSTDIR" -y -bso0 -bsp0' $0
  ; 释放临时工具（内嵌数据卷已解出，避免占用 %TEMP%）
  Delete "$TOOLSDIR\${DATA_PREFIX}.001"
  Delete "$TOOLSDIR\7za.exe"
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "数据解压失败（7za 错误码 $0）。`n请检查：1) 磁盘空间是否充足（需约 12GB）；2) 数据分卷是否完整（SHA256 校验见随附说明）。"
    Abort
  ${EndIf}

  ; 数据卷为旧基线，此处用安装器内嵌文件覆盖为最新（壳更新无需重打数据卷）
  SetOutPath "$INSTDIR"
  File "TTSMultiModel.exe"
  SetOutPath "$INSTDIR\app"
  File "version.json"

  ; 清理 runtime 内指向开发机的 editable .pth 残留（2026-09-18）：
  ; indextts 已实体捆绑进 site-packages，该 .pth 无需保留（且会暴露开发机路径）
  Delete "$INSTDIR\runtime\Lib\site-packages\_editable_impl_indextts.pth"



  ; ---------- 防御：数据卷布局异常（顶层文件被打包成同名目录） ----------
  ${If} ${FileExists} "$INSTDIR\TTSMultiModel.exe\*.*"
  MessageBox MB_ICONSTOP "安装异常：TTSMultiModel.exe 被解压为目录（数据卷布局错误，由旧版打包脚本产生）。`n请重新生成数据分卷后再安装。" /SD IDOK
    Abort
  ${EndIf}

  ; 校验主程序存在
  ${IfNot} ${FileExists} "$INSTDIR\TTSMultiModel.exe"
    MessageBox MB_ICONSTOP "安装异常：未找到 TTSMultiModel.exe，安装可能不完整。"
    Abort
  ${EndIf}

  ; ---------- CLI 全局命令：内嵌 shim + 注册当前用户 PATH ----------
  ; shim 放在安装根目录（PATH 加入的是这一层），运行时再定位 app\bootstrap
  SetOutPath "$INSTDIR"
  File "tts-multimodel.cmd"
  Call AddToUserPath

  ; 校验瘦包布局：归档根必须解开成 $INSTDIR\app\，且引导解释器就位
  ; （壳靠 app\bootstrap\python.exe 起后端并把用户带到「下载管理」页）
  ${IfNot} ${FileExists} "$INSTDIR\app\start_portable.py"
    MessageBox MB_ICONSTOP "安装异常：未找到 app\start_portable.py。$\n数据卷布局错误（归档根应为 app\），请重新生成数据卷。"
    Abort
  ${EndIf}
  ${IfNot} ${FileExists} "$INSTDIR\app\bootstrap\python.exe"
    MessageBox MB_ICONSTOP "安装异常：未找到 app\bootstrap\python.exe。$\n引导解释器缺失，应用无法启动。"
    Abort
  ${EndIf}

  ; 快捷方式（开始菜单 + 桌面）
  !insertmacro MUI_STARTMENU_WRITE_BEGIN Application
    CreateDirectory "$SMPROGRAMS\$StartMenuFolder"
    CreateShortcut "$SMPROGRAMS\$StartMenuFolder\TTSMultiModel.lnk" "$INSTDIR\TTSMultiModel.exe"
    ; CLI 快捷方式：打开命令行窗口并运行 shim，方便不熟终端的用户发现 CLI
    CreateShortcut "$SMPROGRAMS\$StartMenuFolder\TTSMultiModel CLI.lnk" "$SYSDIR\cmd.exe" '/k "$INSTDIR\tts-multimodel.cmd" --help'
  !insertmacro MUI_STARTMENU_WRITE_END
  CreateShortcut "$DESKTOP\TTSMultiModel.lnk" "$INSTDIR\TTSMultiModel.exe"

  ; 注册表（卸载信息）
  WriteRegStr HKCU "Software\TTSMultiModel" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "TTSMultiModel 桌面版"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "TTSMultiModel"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\TTSMultiModel.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" 1600000

  ; 卸载器
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; ---------- 更新器自动重启（自更新七步的第 7 步）----------
  ; 应用内更新器下载本安装包后以 `/S /RELAUNCH` 启动，安装完自动拉起主程序，
  ; 实现「点一下 → 退出 → 自动安装 → 自动回来」的无缝闭环；普通安装不带该标志，不受影响。
  ; 用 $CMDLINE（完整命令行）+ StrStr 检测，避免引入 GetParameters 等额外插件。
  ${StrStr} $0 "$CMDLINE" "/RELAUNCH"
  ${If} $0 != ""
    DetailPrint "更新安装完成，正在重新启动 TTS MultiModel…"
    Exec '"$INSTDIR\TTSMultiModel.exe"'
  ${EndIf}

  ; 清理临时工具目录
  RMDir /r "$TOOLSDIR"
SectionEnd

; ---------- 版本信息 ----------
VIProductVersion "2.12.6.0"
VIAddVersionKey "ProductName" "TTSMultiModel 桌面版"
VIAddVersionKey "LegalCopyright" "Copyright (C) 2026 TTSMultiModel"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "FileDescription" "TTSMultiModel 桌面版安装程序"
VIAddVersionKey "FileVersion" "${APP_VERSION}"

; ---------- 卸载 ----------
Section "Uninstall"
  ; 自建临时工具目录
  StrCpy $TOOLSDIR "$TEMP\tts_multimodel-tools"
  RMDir /r "$TOOLSDIR"
  CreateDirectory "$TOOLSDIR"
  SetOutPath "$TOOLSDIR"
  File "killttsmultimodel.ps1"

  !insertmacro KillRunning

  ; 清理快捷方式
  Delete "$DESKTOP\TTSMultiModel.lnk"
  !insertmacro MUI_STARTMENU_GETFOLDER Application $StartMenuFolder
  Delete "$SMPROGRAMS\$StartMenuFolder\TTSMultiModel.lnk"
  Delete "$SMPROGRAMS\$StartMenuFolder\TTSMultiModel CLI.lnk"
  RMDir "$SMPROGRAMS\$StartMenuFolder"
  ; CLI 全局命令：从当前用户 PATH 精确移除安装目录（先删注册表键前处理）
  Call un.RemoveFromUserPath
  ; 清理注册表
  DeleteRegKey HKCU "Software\TTSMultiModel"
  DeleteRegKey HKCU "${UNINST_KEY}"
  ; 删除安装目录（循环重试 30 秒，等待句柄释放）
  RMDir /r "$INSTDIR"
  StrCpy $R0 0
  ${DoWhile} ${FileExists} "$INSTDIR"
    IntOp $R0 $R0 + 1
    ${If} $R0 >= 30
      ${Break}
    ${EndIf}
    Sleep 1000
    RMDir /r "$INSTDIR"
  ${Loop}
  ; 兜底：卸载器退出后由独立 cmd 延迟清理（等文件锁释放后删除）
  nsExec::Exec 'cmd /c ping -n 6 127.0.0.1 >nul & rd /s /q "$INSTDIR"'
  ; 清理临时工具目录
  RMDir /r "$TOOLSDIR"
SectionEnd
