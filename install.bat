@echo off
REM 제주 혼잡·이동 진단 Agent — 설치 (처음 한 번, Windows / 센터 PC)
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
python -m pip install -q --upgrade pip
pip install -r requirements.txt
if not exist .env copy .env.example .env
dir /b data\raw\*.csv >nul 2>&1 || (
  echo [안내] data\raw 에 실데이터가 없어 개발용 합성 데이터를 생성합니다.
  python -m jeju_agent.data.synthetic
)
python -m pytest -q tests
echo 설치 완료. run.bat 으로 실행하세요.
pause
