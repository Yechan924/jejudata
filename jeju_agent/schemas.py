"""데이터 명세서(v12, 2026.1.28)에서 추출한 컬럼 카탈로그 접근자."""
from __future__ import annotations

from functools import lru_cache

import yaml

from .config import CONFIG_DIR

# 이 서비스가 실제로 사용하는 데이터셋(필수 + 보조). 나머지는 카탈로그 탐색용으로만 노출.
CORE_DATASETS = [
    "wifi_ap_usage",      # 4-05 와이파이 AP별 이용 현황  ← 혼잡 센서 (필수)
    "wifi_ap",            # 4-01 와이파이 AP 현황(고정형)
    "wifi_daily_users",   # 4-06 와이파이 일별 이용자 현황
    "bus_station_usage",  # 3-04 버스 정류소별 이용 현황  ← 이동 수요 (필수)
    "bus_route_usage",    # 3-02 버스 노선별 이용 현황
    "bus_station",        # 3-03 버스 정류소 정보
    "bus_route",          # 3-01 버스 노선 정보
    "weather_daily",      # 5-02 일일 날씨 정보
    "weather_station",    # 5-01 날씨 관측소 정보
]

# 파일이 있을 때만 적재하는 보조 데이터셋(허브 공개 API 등). 없으면 조용히 건너뛴다.
EXTRA_DATASETS = [
    "dong_population",    # 허브 #753 읍면동 단위 내국인 유동인구 ← 혼잡지수 외부 검증·읍면동 비교
]


@lru_cache(maxsize=1)
def catalog() -> dict:
    with open(CONFIG_DIR / "schemas.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def dataset(key: str) -> dict:
    return catalog()[key]


def column_names(key: str) -> list[str]:
    return [c["name"] for c in dataset(key)["columns"]]


def describe_for_llm(keys: list[str] | None = None, compact: bool = False) -> str:
    """LLM 시스템 프롬프트에 넣을 압축 스키마 설명. compact=True 면 컬럼 목록을 생략(로컬 소형 모델의 컨텍스트 절약용)."""
    keys = keys or CORE_DATASETS
    lines = []
    for k in keys:
        d = dataset(k)
        if compact:
            lines.append(f"- {k} [{d['code']} {d['name']}]")
            continue
        cols = ", ".join(f"{c['name']}({c['kor']})" for c in d["columns"])
        lines.append(f"- {k} [{d['code']} {d['name']}]: {cols}")
    return "\n".join(lines)
