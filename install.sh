#!/usr/bin/env bash
# 제주 혼잡·이동 진단 Agent — 설치 (처음 한 번, macOS/Linux)
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
ls data/raw/*.csv >/dev/null 2>&1 || { echo "[안내] data/raw 에 실데이터가 없어 개발용 합성 데이터를 생성합니다."; python -m jeju_agent.data.synthetic; }
python -m pytest -q tests
echo "설치 완료. ./run.sh 로 실행하세요."
