#!/usr/bin/env bash
# 제주 혼잡·이동 진단 Agent — 한 번에 실행 (macOS/Linux)
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
if [ ! -f data/raw/*.csv ] 2>/dev/null && [ ! -f data/synthetic/wifi_ap_usage.csv ]; then
  echo "[안내] data/raw 에 실데이터가 없어 개발용 합성 데이터를 생성합니다."
  python -m jeju_agent.data.synthetic
fi
[ -f .env ] || cp .env.example .env
streamlit run app.py
