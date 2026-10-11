' AI4S Research Agent - silent launcher (no CMD window)
' Runs launch_silent.pyw with pythonw (windowless); browser opens when service is ready.
'
' NOTE: no hardcoded user paths. pythonw.exe is auto-detected (conda env first,
' then PATH fallback) so the repo stays portable and leaks no personal info.
Option Explicit

Dim shell, fso, root, pyw, target
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
target = root & "\scripts\launch_silent.pyw"

Function DetectPythonw()
    Dim home, candidates, i
    home = shell.ExpandEnvironmentStrings("%USERPROFILE%")
    candidates = Array( _
        home & "\anaconda3\envs\ai_research\pythonw.exe", _
        home & "\miniconda3\envs\ai_research\pythonw.exe", _
        "C:\ProgramData\anaconda3\envs\ai_research\pythonw.exe", _
        "C:\ProgramData\miniconda3\envs\ai_research\pythonw.exe", _
        home & "\anaconda3\pythonw.exe", _
        home & "\miniconda3\pythonw.exe", _
        "C:\ProgramData\anaconda3\pythonw.exe")
    For i = 0 To UBound(candidates)
        If fso.FileExists(candidates(i)) Then
            DetectPythonw = candidates(i)
            Exit Function
        End If
    Next
    DetectPythonw = "pythonw"
End Function

pyw = DetectPythonw()

If Not fso.FileExists(target) Then
    MsgBox "Launcher not found: " & target, vbExclamation, "AI4S Research Agent"
Else
    shell.CurrentDirectory = root
    shell.Run Chr(34) & pyw & Chr(34) & " " & Chr(34) & target & Chr(34), 0, False
End If
