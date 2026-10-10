@echo off
cd /d "C:\Users\Administrator\ai_research_agent"
call conda activate ai_research
streamlit run app.py
pause