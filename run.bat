@echo off
REM 제주 혼잡·이동 진단 Agent — 한 번에 실행 (Windows)
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q -r requirements.txt
if not exist data\synthetic\wifi_ap_usage.csv (
  dir /b data\raw\*.csv >nul 2>&1 || (
    echo [안내] data\raw 에 실데이터가 없어 개발용 합성 데이터를 생성합니다.
    python -m jeju_agent.data.synthetic
  )
)
if not exist .env copy .env.example .env
streamlit run app.py
