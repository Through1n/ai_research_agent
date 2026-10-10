' AI4S Research Agent - silent launcher (no CMD window)
' Runs launch_silent.pyw with pythonw (windowless); browser opens when service is ready.
Option Explicit
Dim shell, fso, root, pyw, target
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = "C:\Users\Administrator\anaconda3\envs\ai_research\pythonw.exe"
target = root & "\scripts\launch_silent.pyw"
If Not fso.FileExists(pyw) Then
    MsgBox "Runtime not found: " & pyw, vbExclamation, "AI4S Research Agent"
ElseIf Not fso.FileExists(target) Then
    MsgBox "Launcher not found: " & target, vbExclamation, "AI4S Research Agent"
Else
    shell.CurrentDirectory = root
    shell.Run Chr(34) & pyw & Chr(34) & " " & Chr(34) & target & Chr(34), 0, False
End If
