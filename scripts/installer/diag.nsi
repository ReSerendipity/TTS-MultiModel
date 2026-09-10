Unicode true
!include "LogicLib.nsh"
Name "diag7z"
OutFile "diag7z-test.exe"
RequestExecutionLevel user
SetCompressor /SOLID lzma

Section
  SetOutPath "$PLUGINSDIR"
  File "${SRC_7ZA}"
!ifndef SRC_7ZA
!define SRC_7ZA "C:\Users\Doro\Tools\7z-extra\x64\7za.exe"
!endif
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
