#!/usr/bin/env bash
# 제주 혼잡·이동 진단 Agent — 실행 (macOS/Linux). 설치가 안 됐으면 ./install.sh 먼저.
cd "$(dirname "$0")"
[ -d .venv ] || { echo "./install.sh 를 먼저 실행하세요."; exit 1; }
source .venv/bin/activate
[ -f data/synthetic/wifi_ap_usage.csv ] || ls data/raw/*.csv >/dev/null 2>&1 || python -m jeju_agent.data.synthetic
streamlit run app.py --server.headless false
