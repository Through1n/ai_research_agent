' Silent launcher for the AI4SResearchAgent desktop shell (dev mode).
' Runs: pythonw.exe -m desktop with cwd = project root, no wait.
' Window style MUST be 1 (SW_SHOWNORMAL), not 0 (SW_HIDE): style 0 makes the
' pywebview host window start invisible and it never reaches the foreground.
' pythonw has no console, so style 1 shows no black box anyway.
' Pure ASCII on purpose (avoid VBS file encoding issues).
'
' NOTE: no hardcoded user paths. pythonw.exe is auto-detected so the repo
' stays portable and leaks no personal information when published.
Option Explicit

Dim sh, fso, root, pyw
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)

Function DetectPythonw()
    Dim home, candidates, i
    home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
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
sh.CurrentDirectory = root
sh.Run Chr(34) & pyw & Chr(34) & " -m desktop", 1, False
