@echo off
REM 说明：推荐双击「启动科研助手.vbs」无窗口启动；本脚本为终端备用方式（会显示日志窗口）
cd /d "C:\Users\Administrator\ai_research_agent"
call conda activate ai_research
streamlit run app.py
pause