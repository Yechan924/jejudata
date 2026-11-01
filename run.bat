@echo off
REM 제주 혼잡·이동 진단 Agent — 실행 (Windows). 설치가 안 됐으면 install.bat 먼저.
cd /d %~dp0
if not exist .venv (echo install.bat 을 먼저 실행하세요. & pause & exit /b 1)
call .venv\Scripts\activate
if not exist data\synthetic\wifi_ap_usage.csv ( dir /b data\raw\*.csv >nul 2>&1 || python -m jeju_agent.data.synthetic )
streamlit run app.py --server.headless false
