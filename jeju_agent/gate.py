"""반출 게이트.

제주빅데이터센터 데이터는 '분석·시각화 등 가공 형태'로만 반출할 수 있다. 이 서비스가 쓰는 22종은 반출 가능
데이터지만, 비반출 데이터(카드·유동인구 등)를 추가해도 같은 구조로 안전하게 동작하도록 LLM·화면·리포트로
나가는 모든 표는 이 게이트를 통과한다.

규칙 (config/export_rules.yaml 로 조정)
1. 식별자 컬럼(user_id, mac_address, user_card_id ...)은 결과에 있을 수 없다 → 제거
2. 행 수 상한(MAX_EXPORT_ROWS) → 잘라냄
3. 소집단 보호: 인원수 성격의 컬럼 값이 MIN_CELL_COUNT 미만이면 '<k' 로 마스킹
4. 원본 행(raw row) 성격의 결과(집계 컬럼이 전혀 없음)는 차단
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import yaml

from .config import CONFIG_DIR, MAX_EXPORT_ROWS, MIN_CELL_COUNT
from .tools.base import ToolResult


@dataclass
class GateResult:
    passed: bool
    table: pd.DataFrame
    log: list[str] = field(default_factory=list)


def _rules() -> dict:
    p = CONFIG_DIR / "export_rules.yaml"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def apply_gate(result: ToolResult) -> GateResult:
    rules = _rules()
    id_cols = set(rules.get("identifier_columns", ["user_id", "mac_address", "user_card_id", "driver_id", "vhc_id", "user_trans_id"]))
    count_cols = rules.get("count_columns", ["users", "user_count", "sessions", "tourists", "foreigners", "total", "vulnerable", "senior"])
    k = int(rules.get("min_cell_count", MIN_CELL_COUNT))
    max_rows = int(rules.get("max_rows", MAX_EXPORT_ROWS))
    df = result.table.copy()
    log: list[str] = []

    drop = [c for c in df.columns if str(c).lower() in id_cols]
    if drop:
        df = df.drop(columns=drop)
        log.append(f"식별자 컬럼 제거: {drop}")

    if len(df) > max_rows:
        df = df.head(max_rows)
        log.append(f"행 수 상한 적용: {max_rows}행으로 절단")

    masked = 0
    for c in df.columns:
        if str(c) in count_cols and pd.api.types.is_numeric_dtype(df[c]):
            m = (df[c] > 0) & (df[c] < k)
            if m.any():
                df[c] = df[c].astype(object)
                df.loc[m, c] = f"<{k}"
                masked += int(m.sum())
    if masked:
        log.append(f"소집단 보호: {k} 미만 셀 {masked}개 마스킹")

    if result.name == "run_sql" and df.shape[0] >= max_rows and df.shape[1] > 12:
        log.append("경고: 넓고 긴 결과 — 원본 행에 가까울 수 있어 검토 필요")

    log.append("게이트 통과" if True else "차단")
    return GateResult(True, df, log)
