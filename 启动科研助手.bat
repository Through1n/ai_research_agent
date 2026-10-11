@echo off
REM ˵�����Ƽ�˫����������������.vbs���޴������������ű�Ϊ�ն˱��÷�ʽ������ʾ��־���ڣ�
cd /d "%~dp0"
call conda activate ai_research
streamlit run app.py
pause