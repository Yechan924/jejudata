"""읽기 전용 SQL 도구 + 데이터 설명 도구. 반드시 집계 쿼리여야 하며 결과는 반출 게이트를 거친다."""
from __future__ import annotations

import re

from .. import schemas
from .base import ToolResult, register

FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|copy|pragma|export|load)\b", re.I)
ID_COLS = ("user_id", "mac_address", "user_card_id", "driver_id", "vhc_id")


@register(
    "run_sql",
    "읽기 전용 집계 SQL을 실행한다(DuckDB 또는 sqlite 문법). 다른 도구로 답할 수 없는 맞춤 집계에만 사용. "
    "GROUP BY가 있는 집계 쿼리만 허용되며 user_id·mac_address 같은 식별자 컬럼은 SELECT 할 수 없다. "
    "사용 가능한 파생 컬럼: date(YYYY-MM-DD), ym, dow(월~일), weekday(0=월), is_weekend(0/1), hour.",
    {"type": "object", "properties": {"sql": {"type": "string", "description": "SELECT ... GROUP BY ... 형태의 SQL"},
                                      "title": {"type": "string", "description": "결과 제목"}},
     "required": ["sql"]},
)
def run_sql(db, sql: str, title: str = "사용자 정의 집계") -> ToolResult:
    s = sql.strip().rstrip(";")
    if not s.lower().startswith("select") and not s.lower().startswith("with"):
        raise ValueError("SELECT/WITH 로 시작하는 읽기 전용 쿼리만 허용됩니다.")
    if FORBIDDEN.search(s):
        raise ValueError("쓰기/DDL 키워드가 포함된 쿼리는 실행할 수 없습니다.")
    if not re.search(r"\bgroup\s+by\b", s, re.I) and not re.search(r"\b(count|sum|avg|min|max)\s*\(", s, re.I):
        raise ValueError("개별 행 조회는 허용되지 않습니다. 집계(GROUP BY 또는 집계함수) 쿼리만 실행됩니다.")
    if ";" in s:
        raise ValueError("다중 문장은 허용되지 않습니다.")
    limited = s if re.search(r"\blimit\b", s, re.I) else s + " LIMIT 500"
    df = db.query(limited)
    bad = [c for c in df.columns if c.lower() in ID_COLS]
    if bad:
        raise ValueError(f"식별자 컬럼 {bad} 은(는) 결과에 포함할 수 없습니다. 집계 후 제거하세요.")
    return ToolResult("run_sql", title, df, [f"{len(df)}행 반환"], ["LLM이 작성한 SQL 결과이므로 로그의 SQL을 확인하세요."],
                      method=limited, params={"sql": limited}, datasets=[t for t in db.frames if re.search(rf"\b{t}\b", s)])


@register(
    "describe_data",
    "적재된 데이터셋의 컬럼·기간·행수·출처(실데이터/합성)를 설명한다. 어떤 데이터가 있는지 물을 때 사용.",
    {"type": "object", "properties": {"dataset": {"type": "string", "description": "데이터셋 키(생략 시 전체 요약)"}}},
)
def describe_data(db, dataset: str | None = None) -> ToolResult:
    if dataset and dataset in schemas.catalog():
        d = schemas.dataset(dataset)
        import pandas as pd
        df = pd.DataFrame(d["columns"])[["name", "kor", "type", "example", "note"]]
        p = db.provenance.get(dataset)
        facts = [f"{d['code']} {d['name']} — {d['description'][:120]}"]
        if p:
            facts.append(f"출처: {p.source}, {p.rows:,}행, 기간 {p.date_min}~{p.date_max}")
        return ToolResult("describe_data", f"{d['name']} 컬럼 명세", df, facts, [], datasets=[dataset])
    df = db.summary()
    facts = [f"{len(df)}개 데이터셋 적재, 엔진 {db.engine}"]
    if db.uses_synthetic():
        facts.append("⚠️ 일부/전체가 개발용 합성 데이터입니다. 실제 결론에 사용하지 마세요.")
    return ToolResult("describe_data", "적재 데이터 요약", df, facts, [], datasets=list(db.frames))
