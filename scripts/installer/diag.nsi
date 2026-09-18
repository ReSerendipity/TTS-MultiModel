Unicode true
!include "LogicLib.nsh"
Name "diag7z"
OutFile "diag7z-test.exe"
RequestExecutionLevel user
SetCompressor /SOLID lzma
; 7za 默认从 PATH 查找，或用 makensis -DSRC_7ZA=... 指定（勿写死本机绝对路径）
!ifndef SRC_7ZA
!define SRC_7ZA "7za.exe"
!endif

Section
  SetOutPath "$PLUGINSDIR"
  File "${SRC_7ZA}"
  FileOpen $1 "$TEMP\diag7z-result.txt" w
  FileWrite $1 "PLUGINSDIR=$PLUGINSDIR`n"
  ${If} ${FileExists} "$PLUGINSDIR\7za.exe"
    FileWrite $1 "7za-exists=YES`n"
  ${Else}
    FileWrite $1 "7za-exists=NO`n"
  ${EndIf}
  ExecWait '"$PLUGINSDIR\7za.exe" i' $0
  FileWrite $1 "exitcode=$0`n"
  FileWrite $1 "done`n"
  FileClose $1
SectionEnd
