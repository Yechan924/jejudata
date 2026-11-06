"""경로·환경변수 설정. 모든 값은 .env 또는 환경변수로 덮어쓸 수 있다."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.getenv("JEJU_DATA_DIR", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"            # 제주데이터허브에서 받은 원본 CSV를 넣는 곳
SYNTHETIC_DIR = DATA_DIR / "synthetic"  # 명세서 기반 개발용 합성 데이터
DEMO_DIR = DATA_DIR / "demo"            # 저장소에 동봉한 허브 공개 API 축약본(.csv.gz) — data/raw 가 비어 있을 때 사용(데모 배포용)
OUTPUT_DIR = Path(os.getenv("JEJU_OUTPUT_DIR", ROOT / "outputs"))
DB_PATH = Path(os.getenv("JEJU_DB_PATH", DATA_DIR / "jeju.duckdb"))

# LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto")  # auto | anthropic | openai | ollama | offline
LLM_MODEL = os.getenv("LLM_MODEL", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
LLM_MAX_TOOL_CALLS = int(os.getenv("LLM_MAX_TOOL_CALLS", "6"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
LLM_RETRIES = int(os.getenv("LLM_RETRIES", "2"))      # 429/5xx·네트워크 오류 재시도 횟수

# 반출 게이트
MIN_CELL_COUNT = int(os.getenv("EXPORT_MIN_CELL_COUNT", "10"))  # 이 미만 집계 셀은 마스킹 (센터 반출 정책 2026.09.01: 단순집계 10 미만 불포함)
MAX_EXPORT_ROWS = int(os.getenv("EXPORT_MAX_ROWS", "500"))      # LLM/화면으로 나갈 수 있는 최대 행

# 출처 표기 (제주데이터허브 이용약관 제7조 — 화면·리포트·답변에 상시 표시)
ATTRIBUTION = ("본 자료는 제주빅데이터센터에서 제공하는 자료를 활용한 것이며, "
               "연구결과는 제주특별자치도 및 제주테크노파크와 관련이 없음을 밝힙니다.")
