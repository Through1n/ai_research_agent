' Silent launcher for the AI4SResearchAgent desktop shell (dev mode).
' Runs: pythonw.exe -m desktop with cwd = project root, hidden window, no wait.
' Pure ASCII on purpose (avoid VBS file encoding issues).
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\Administrator\ai_research_agent"
sh.Run """C:\Users\Administrator\anaconda3\envs\ai_research\pythonw.exe"" -m desktop", 0, False
