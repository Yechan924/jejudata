from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from ..data.loader import JejuDB


@dataclass
class ToolResult:
    name: str                      # 도구 이름
    title: str                     # 사람이 읽는 제목
    table: pd.DataFrame            # 집계 결과(반출 게이트 통과 전)
    facts: list[str] = field(default_factory=list)      # 해설에 쓸 수치 사실
    caveats: list[str] = field(default_factory=list)    # 한계·주의
    chart: dict | None = None      # {"type": "bar|line", "x": .., "y": .., "color": .., "title": ..}
    map_points: pd.DataFrame | None = None  # lat, lon, value, label 컬럼
    method: str = ""               # 계산 방법(재현용 서술 또는 SQL)
    params: dict = field(default_factory=dict)
    datasets: list[str] = field(default_factory=list)   # 사용한 데이터셋 키
    gate_log: list[str] = field(default_factory=list)

    def to_llm_text(self, max_rows: int = 40) -> str:
        t = self.table.head(max_rows)
        lines = [f"[{self.title}] (총 {len(self.table)}행 중 {len(t)}행)"]
        lines.append(df_to_md(t))
        if self.facts:
            lines.append("사실: " + " / ".join(self.facts))
        if self.caveats:
            lines.append("주의: " + " / ".join(self.caveats))
        return "\n".join(lines)


TOOL_REGISTRY: dict[str, dict] = {}


def register(name: str, description: str, parameters: dict):
    """LLM 도구 스키마와 함께 함수를 등록한다 (JSON Schema)."""
    def deco(fn: Callable[..., ToolResult]):
        TOOL_REGISTRY[name] = {"fn": fn, "description": description, "parameters": parameters}
        return fn
    return deco


def tool_specs() -> list[dict]:
    return [{"name": n, "description": t["description"], "parameters": t["parameters"]} for n, t in TOOL_REGISTRY.items()]


def call_tool(db: JejuDB, name: str, args: dict[str, Any]) -> ToolResult:
    if name not in TOOL_REGISTRY:
        raise KeyError(f"알 수 없는 도구: {name}")
    return TOOL_REGISTRY[name]["fn"](db, **args)


# ---------- 공용 유틸 ----------
def df_to_md(df: pd.DataFrame, max_rows: int = 40) -> str:
    """외부 의존성 없는 마크다운 표."""
    df = df.head(max_rows)
    def fmt(v):
        if isinstance(v, float):
            return f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
        if isinstance(v, (int,)) and not isinstance(v, bool):
            return f"{v:,}"
        return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        out.append("| " + " | ".join(fmt(v) for v in row.tolist()) + " |")
    return "\n".join(out)


def period_filter(df: pd.DataFrame, date_from: str | None, date_to: str | None) -> pd.DataFrame:
    if date_from:
        df = df[df["date"] >= date_from]
    if date_to:
        df = df[df["date"] <= date_to]
    return df


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fuzzy_match(name: str, candidates: list[str]) -> str | None:
    """'성산' → '성산일출봉' 같은 부분 일치. 가장 짧은 후보 우선."""
    if not name:
        return None
    name = name.replace(" ", "")
    exact = [c for c in candidates if c.replace(" ", "") == name]
    if exact:
        return exact[0]
    part = [c for c in candidates if name in c.replace(" ", "") or c.replace(" ", "") in name]
    return sorted(part, key=len)[0] if part else None


def wifi_is_aggregated(df: pd.DataFrame) -> bool:
    """4-05 원본(행=접속 1건, user_id 있음)인지, 허브 API 집계본(행=장소×일, user_count 있음)인지."""
    return ("user_count" in df.columns and df["user_count"].notna().any()) and not ("user_id" in df.columns and df["user_id"].notna().any())


def wifi_users_agg(df: pd.DataFrame):
    """groupby().agg 에 넣을 '이용자 수' 집계 스펙. 원본이면 고유 user_id 수, 집계본이면 user_count 합."""
    return ("user_count", "sum") if wifi_is_aggregated(df) else ("user_id", "nunique")


def wifi_users_count(df: pd.DataFrame) -> int:
    """부분집합의 이용자 수(원본: 고유 user_id, 집계본: user_count 합)."""
    if wifi_is_aggregated(df):
        return int(pd.to_numeric(df["user_count"], errors="coerce").fillna(0).sum())
    return int(df["user_id"].nunique())


PERIOD_PARAMS = {
    "date_from": {"type": "string", "description": "시작일 YYYY-MM-DD (생략 시 데이터 전체)"},
    "date_to": {"type": "string", "description": "종료일 YYYY-MM-DD (생략 시 데이터 전체)"},
}
