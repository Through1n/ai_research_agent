' Silent launcher for the AI4SResearchAgent desktop shell (dev mode).
' Runs: pythonw.exe -m desktop with cwd = project root, no wait.
' Window style MUST be 1 (SW_SHOWNORMAL), not 0 (SW_HIDE): style 0 makes the
' pywebview host window start invisible and it never reaches the foreground.
' pythonw has no console, so style 1 shows no black box anyway.
' Pure ASCII on purpose (avoid VBS file encoding issues).
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\Administrator\ai_research_agent"
sh.Run """C:\Users\Administrator\anaconda3\envs\ai_research\pythonw.exe"" -m desktop", 1, False
